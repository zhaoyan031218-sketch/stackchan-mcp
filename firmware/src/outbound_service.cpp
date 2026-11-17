#include "outbound_service.h"

#include <M5Unified.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
#include <atomic>
#include <esp_heap_caps.h>
#include <esp_system.h>
#include <mbedtls/x509_crt.h>
#include <sys/time.h>
#include <time.h>

#include "config_loader.h"
#include "firmware_command.h"
#include "outbound_diagnostics.h"
#include "outbound_ws_writer.h"
#include "playback_service.h"

namespace {

static_assert(WEBSOCKETS_MAX_DATA_SIZE == outbound::MAX_FRAME_BYTES,
              "The pinned WebSockets bounded-frame build patch must be applied");

enum class Phase : uint8_t { IDLE, RECEIVING, QUEUED, RUNNING, READY };

static std::atomic<Phase> phase{Phase::IDLE};
static std::atomic<uint32_t> generation{1};
static std::atomic<bool> connected{false};
static FirmwareCommand command;
static TaskHandle_t networkTask = nullptr;

class OutboundSocket : public WebSocketsClient {
public:
    outbound::BinaryWriteResult sendBoundedBinary(const uint8_t* data, size_t size,
                                                 bool (*current)()) {
        return outbound::writeMaskedBinary(data, size,
            [this](uint8_t* frame, size_t bytes) -> size_t {
                if (!isConnected()) return 0;
                // Use the library's TLS connection and bounded write handling,
                // but not its large-frame sendBIN path (which uses a zero mask).
                return write(&_client, frame, bytes);
            }, []() -> uint32_t { return esp_random(); }, current);
    }
};

static OutboundSocket socket;
static outbound::MessageBuffer fragments(ps_malloc);
static uint32_t fragmentStartedMs = 0;
static uint32_t lastHeartbeatMs = 0;

static int64_t utcNowMs() {
    timeval now;
    if (gettimeofday(&now, nullptr) != 0) return 0;
    return static_cast<int64_t>(now.tv_sec) * 1000 + now.tv_usec / 1000;
}

static outbound::CommandValidity commandValidity() {
    return outbound::classifyCommand(connected.load(), command.generation,
        generation.load(), millis(), command.startedMs, utcNowMs(), command.info.expiresAtMs);
}

static bool currentCommand() {
    return commandValidity() == outbound::CommandValidity::CURRENT;
}

static void logTransportEvent(const char* event, uint16_t code = 0, size_t expected = 0,
                              size_t received = 0, const char* task = "network") {
    const Phase state = phase.load();
    const bool active = state != Phase::IDLE;
    const uint32_t age = active ? millis() - command.startedMs : 0;
    const int64_t deadlineRemaining = active ? command.info.expiresAtMs - utcNowMs() : 0;
    // Callers supply constant labels and numbers only. WebSockets debug remains
    // disabled because its HTTP/header and payload logging exposes credentials.
    Serial.printf("[OUTBOUND] event=%s code=%u phase=%u expected=%u received=%u "
                  "age_ms=%u deadline_left_ms=%lld validity=%s heap=%u psram=%u "
                  "largest_internal=%u largest_psram=%u stack_min=%u task=%s\n",
                  event, (unsigned)code, (unsigned)state, (unsigned)expected,
                  (unsigned)received, (unsigned)age, (long long)deadlineRemaining,
                  active ? outbound::commandValidityLabel(commandValidity()) : "idle",
                  ESP.getFreeHeap(), ESP.getFreePsram(),
                  (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
                  (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT),
                  (unsigned)uxTaskGetStackHighWaterMark(nullptr), task);
}

static void invalidateConnection() {
    if (connected.exchange(false)) generation.fetch_add(1);
    fragments.clear();
}

static void closeConnection(const char* reason, size_t expected = 0, size_t received = 0) {
    logTransportEvent(reason, 0, expected, received);
    invalidateConnection();
    socket.disconnect();
}

// Only the network task releases the slot. The main loop publishes READY after
// its last access, so disconnect can cancel queued work without freeing a live
// camera/audio command out from under the main loop.
static void releaseSlot() {
    command.clear();
    phase.store(Phase::IDLE);
}

static void receiveText(const uint8_t* data, size_t size) {
    if (phase.load() != Phase::IDLE) {
        closeConnection("text_wrong_phase", 0, size);
        return;
    }
    command.clear();
    if (!outbound::parseRequest(data, size, command.request, command.info)) {
        closeConnection("text_invalid_envelope", 0, size);
        return;
    }
    command.generation = generation.load();
    command.startedMs = millis();
    phase.store(command.info.binarySize ? Phase::RECEIVING : Phase::QUEUED);
    logTransportEvent("rx_envelope", 0, command.info.binarySize, size);
}

static void receiveBinary(const uint8_t* data, size_t size) {
    const size_t expected = command.info.binarySize + outbound::BINARY_HEADER_BYTES;
    if (phase.load() != Phase::RECEIVING) {
        closeConnection("binary_wrong_phase", expected, size);
        return;
    }
    if (!currentCommand()) {
        closeConnection("binary_not_current", expected, size);
        return;
    }
    if (!outbound::validBinary(data, size, command.info)) {
        closeConnection("binary_invalid_envelope", expected, size);
        return;
    }
    command.pcmData = static_cast<uint8_t*>(ps_malloc(command.info.binarySize));
    if (!command.pcmData) {
        closeConnection("binary_allocation_failed", command.info.binarySize, 0);
        return;
    }
    memcpy(command.pcmData, data + outbound::BINARY_HEADER_BYTES, command.info.binarySize);
    phase.store(Phase::QUEUED);
    logTransportEvent("rx_binary_accepted", 0, expected, size);
}

static void receiveMessage(bool binary, const uint8_t* data, size_t size) {
    if (binary) receiveBinary(data, size);
    else receiveText(data, size);
}

static void onSocketEvent(WStype_t type, uint8_t* data, size_t size) {
    switch (type) {
        case WStype_CONNECTED:
            Serial.println("[OUTBOUND] Channel connected");
            generation.fetch_add(1);
            connected.store(true);
            lastHeartbeatMs = millis();
            if (!socket.sendTXT("{\"type\":\"hello\",\"v\":1}")) closeConnection("hello_write_failed");
            break;
        case WStype_DISCONNECTED:
            logTransportEvent("channel_disconnected");
            invalidateConnection();
            break;
        case WStype_TEXT:
        case WStype_BIN:
            if (fragments.active()) closeConnection("message_during_fragment", 0, size);
            else receiveMessage(type == WStype_BIN, data, size);
            break;
        case WStype_FRAGMENT_TEXT_START:
        case WStype_FRAGMENT_BIN_START:
            fragmentStartedMs = millis();
            if (fragments.active()) {
                closeConnection("fragment_already_active", 0, size);
            } else if (!fragments.begin(type == WStype_FRAGMENT_BIN_START)) {
                closeConnection("fragment_allocation_failed", 0, size);
            } else if (!fragments.append(data, size)) {
                closeConnection("fragment_invalid_start", 0, size);
            }
            break;
        case WStype_FRAGMENT:
        case WStype_FRAGMENT_FIN:
            if (!fragments.append(data, size)) {
                closeConnection("fragment_invalid_append", 0, size);
            } else if (type == WStype_FRAGMENT_FIN) {
                receiveMessage(fragments.binary(), fragments.data(), fragments.size());
                fragments.clear();
            }
            break;
        case WStype_ERROR:
            closeConnection("websocket_error");
            break;
        default:
            break;
    }
}

static bool sendResponse() {
    if (!currentCommand()) {
        logTransportEvent("reply_not_current");
        return false;
    }
    JsonDocument response;
    response["id"] = command.info.id;
    response["status"] = command.status;
    if (command.binaryFrame) {
        response["binary_size"] = command.binarySize;
        response["content_type"] = command.contentType;
    } else {
        JsonDocument body;
        if (deserializeJson(body, command.body)) {
            response["status"] = 500;
            response["body"]["success"] = false;
            response["body"]["error"] = "invalid command response";
        } else {
            response["body"] = body;
        }
    }
    if (response.overflowed() || measureJson(response) > outbound::MAX_TEXT_BYTES) {
        logTransportEvent("reply_metadata_invalid_size");
        return false;
    }
    String text;
    serializeJson(response, text);
    if (!currentCommand() || !socket.sendTXT(text)) {
        logTransportEvent("reply_metadata_write_failed");
        return false;
    }
    if (!command.binaryFrame) return true;
    const size_t bytes = command.binarySize + outbound::BINARY_HEADER_BYTES;
    const uint32_t started = millis();
    Serial.printf("[OUTBOUND] Binary reply start bytes=%u heap=%u psram=%u\n",
                  (unsigned)bytes, ESP.getFreeHeap(), ESP.getFreePsram());
    const auto result = socket.sendBoundedBinary(command.binaryFrame, bytes, currentCommand);
    const char* status = "write_failed";
    if (result.status == outbound::BinaryWriteStatus::COMPLETE) status = "sent";
    else if (result.status == outbound::BinaryWriteStatus::EXPIRED) status = "expired";
    else if (result.status == outbound::BinaryWriteStatus::INVALID) status = "invalid";
    Serial.printf("[OUTBOUND] Binary reply %s bytes=%u/%u frames=%u elapsed_ms=%u\n",
                  status, (unsigned)result.payloadBytes, (unsigned)bytes,
                  (unsigned)result.frames, (unsigned)(millis() - started));
    return result.status == outbound::BinaryWriteStatus::COMPLETE;
}

static void outboundNetworkTask(void*) {
    // begin() clears authorization. Configure it afterwards and never log it.
    socket.beginSslWithCA(STACKCHAN_OUTBOUND_HOST, STACKCHAN_OUTBOUND_PORT,
                          "/stackchan/ws", STACKCHAN_OUTBOUND_CA_PEM, "");
    String authorization = String("Bearer ") + STACKCHAN_OUTBOUND_DEVICE_TOKEN;
    socket.setAuthorization(authorization.c_str());
    socket.setExtraHeaders("");
    socket.setReconnectInterval(5000);
    socket.enableHeartbeat(15000, 5000, 2);
    socket.onEvent(onSocketEvent);
    bool timeRequested = false;
    enum class WaitStage { STARTING, WIFI, CLOCK, SERVER, ONLINE };
    WaitStage lastStage = WaitStage::STARTING;
    uint32_t lastStageLogMs = 0;

    for (;;) {
        // READY is an acquire/release handoff; no request fields are read while
        // the main loop owns RUNNING, including during a connection timeout.
        Phase state = phase.load();
        if (state == Phase::READY) {
            if (connected.load() && command.generation == generation.load() && !sendResponse()) {
                closeConnection("reply_failed");
            }
            releaseSlot();
        } else if (state == Phase::RECEIVING && !connected.load()) {
            releaseSlot();
        }

        const bool wifiReady = WiFi.status() == WL_CONNECTED;
        if (!wifiReady) {
            if (connected.load()) closeConnection("wifi_disconnected");
        } else {
            if (!timeRequested) {
                configTime(0, 0, STACKCHAN_OUTBOUND_NTP_SERVER);
                timeRequested = true;
            }
            // Certificate validity needs a plausible clock. Never bypass TLS
            // verification when time sync or provisioning is unavailable.
            if (time(nullptr) >= 1704067200) socket.loop();
        }

        // Distinguish startup failures without enabling WebSockets debug output,
        // which could expose the Authorization header. Repeat waiting states
        // every 30 seconds so a monitor opened after boot is still useful.
        const WaitStage stage = !wifiReady ? WaitStage::WIFI
            : time(nullptr) < 1704067200 ? WaitStage::CLOCK
            : connected.load() ? WaitStage::ONLINE : WaitStage::SERVER;
        const uint32_t stageNow = millis();
        if (stage != lastStage || (stage != WaitStage::ONLINE &&
                                  stageNow - lastStageLogMs >= 30000)) {
            if (stage == WaitStage::WIFI) {
                Serial.println("[OUTBOUND] Waiting for WiFi");
            } else if (stage == WaitStage::CLOCK) {
                Serial.println("[OUTBOUND] Waiting for NTP time; TLS has not started");
            } else if (stage == WaitStage::SERVER) {
                Serial.println("[OUTBOUND] WSS not connected; check host, network, CA and DEVICE token");
            }
            lastStage = stage;
            lastStageLogMs = stageNow;
        }

        state = phase.load();
        if (connected.load()) {
            const uint32_t now = millis();
            if (state != Phase::IDLE && !currentCommand()) {
                closeConnection("command_not_current");
            } else if (state == Phase::RECEIVING && outbound::deadlineExpired(
                           now, command.startedMs, outbound::BINARY_TIMEOUT_MS)) {
                closeConnection("binary_wait_timeout", command.info.binarySize, 0);
            } else if (fragments.active() && outbound::deadlineExpired(
                           now, fragmentStartedMs, outbound::BINARY_TIMEOUT_MS)) {
                closeConnection("fragment_wait_timeout", 0, fragments.size());
            } else if (outbound::deadlineExpired(now, lastHeartbeatMs, outbound::HEARTBEAT_MS)) {
                lastHeartbeatMs = now;
                if (!socket.sendTXT("{\"type\":\"heartbeat\"}")) closeConnection("heartbeat_write_failed");
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

}  // namespace

// Called only by the version-checked local WebSockets middleware patch. Its
// event labels are literals; code/length values never contain message content.
void stackchanWebSocketDiagnostic(const char* event, uint16_t code,
                                 size_t expected, size_t received) {
    logTransportEvent(event, code, expected, received);
}

void initOutboundService() {
    if (networkTask) return;
    if (!outbound::validConfiguration(STACKCHAN_OUTBOUND_ENABLED,
            STACKCHAN_OUTBOUND_HOST, STACKCHAN_OUTBOUND_PORT,
            STACKCHAN_OUTBOUND_DEVICE_TOKEN, STACKCHAN_OUTBOUND_CA_PEM)) {
        Serial.println("[OUTBOUND] Disabled (opt-in or TLS configuration missing/invalid)");
        return;
    }
    mbedtls_x509_crt ca;
    mbedtls_x509_crt_init(&ca);
    int parsed = mbedtls_x509_crt_parse(&ca,
        reinterpret_cast<const unsigned char*>(STACKCHAN_OUTBOUND_CA_PEM),
        strlen(STACKCHAN_OUTBOUND_CA_PEM) + 1);
    mbedtls_x509_crt_free(&ca);
    if (parsed != 0) {
        Serial.println("[OUTBOUND] Disabled (invalid CA certificate)");
        return;
    }
    Serial.println("[OUTBOUND] Configuration accepted; starting WSS task");
    if (xTaskCreatePinnedToCore(outboundNetworkTask, "outboundWss", 12288,
            nullptr, 1, &networkTask, 0) != pdPASS) {
        networkTask = nullptr;
        Serial.println("[OUTBOUND] Disabled (network task allocation failed)");
    }
}

void serviceOutboundCommands() {
    clearStaleOutboundPcm();
    Phase expected = Phase::QUEUED;
    if (!phase.compare_exchange_strong(expected, Phase::RUNNING)) return;
    logTransportEvent("dispatch_begin", 0, command.info.binarySize, 0, "main");
    if (currentCommand()) {
        executeFirmwareCommand(command);
        logTransportEvent("dispatch_end", command.status, command.info.binarySize, 0, "main");
    } else {
        logTransportEvent("dispatch_skipped", 0, command.info.binarySize, 0, "main");
    }
    phase.store(Phase::READY);
}

bool isOutboundGenerationCurrent(uint32_t commandGeneration) {
    return connected.load() && generation.load() == commandGeneration;
}
