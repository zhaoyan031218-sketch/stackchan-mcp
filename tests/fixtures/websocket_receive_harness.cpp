// Host-only harness for the actual pinned WebSockets::readCb source.
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <sys/types.h>
#include <vector>

#define WEBSOCKETS_NETWORK_TYPE 1
#define NETWORK_ESP8266_ASYNC 2
#define WEBSOCKETS_TCP_TIMEOUT 2000
#define DEBUG_WEBSOCKETS(...) do {} while (0)
#define WEBSOCKETS_YIELD() advanceClock()
#define WEBSOCKETS_YIELD_MORE() advanceClock()

static uint32_t nowMs = 0;
static uint32_t commandDeadline = 20000;
static uint32_t pcmLastProgress = 0;
static bool pcmReceiving = true;
static size_t progressBytes = 0;
static unsigned progressCalls = 0;
static unsigned zeroProgressCalls = 0;
static std::string lastDiagnostic;
static size_t diagnosticExpected = 0;
static size_t diagnosticReceived = 0;

uint32_t millis() { return nowMs; }
void advanceClock() { ++nowMs; }
bool stackchanWebSocketReadCurrent() { return !pcmReceiving || nowMs < commandDeadline; }
bool stackchanWebSocketReadIdleExpired(uint32_t defaultReadLastProgressMs) {
    return pcmReceiving ? nowMs - pcmLastProgress >= 8000
                        : nowMs - defaultReadLastProgressMs > WEBSOCKETS_TCP_TIMEOUT;
}
void stackchanWebSocketReadProgress(size_t bytes) {
    if (!bytes) { ++zeroProgressCalls; return; }
    ++progressCalls;
    progressBytes += bytes;
    if (pcmReceiving) pcmLastProgress = nowMs;
}
void stackchanWebSocketDiagnostic(const char* event, uint16_t, size_t expected, size_t received) {
    lastDiagnostic = event;
    diagnosticExpected = expected;
    diagnosticReceived = received;
}

struct Arrival { uint32_t at; size_t bytes; uint32_t returnDelay = 0; };
struct FakeTcp {
    std::vector<Arrival> arrivals;
    size_t index = 0;
    size_t totalRead = 0;
    unsigned reads = 0;
    unsigned zeroReads = 0;
    uint32_t disconnectAt = UINT32_MAX;
    bool connected() const { return nowMs < disconnectAt; }
    int available() const {
        return index < arrivals.size() && nowMs >= arrivals[index].at
            ? static_cast<int>(arrivals[index].bytes) : 0;
    }
    ssize_t read(uint8_t* out, size_t requested) {
        ++reads;
        if (zeroReads) { --zeroReads; return 0; }
        if (!available()) return -1;
        auto& arrival = arrivals[index];
        const size_t count = std::min(requested, arrival.bytes);
        for (size_t i = 0; i < count; ++i) out[i] = static_cast<uint8_t>((totalRead + i) % 251);
        nowMs += arrival.returnDelay;
        totalRead += count;
        arrival.bytes -= count;
        if (!arrival.bytes) ++index;
        return static_cast<ssize_t>(count);
    }
};
struct WSclient_t { FakeTcp* tcp; };
using WSreadWaitCb = std::function<void(WSclient_t*, bool)>;
class WebSockets {
public:
    bool readCb(WSclient_t* client, uint8_t* out, size_t n, WSreadWaitCb callback);
};

#include "read_callback_under_test.inc"

static void expect(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}

static void run(const std::string& scenario) {
    constexpr size_t fullFrame = 49152 + 36;
    size_t requested = fullFrame;
    FakeTcp tcp;
    bool expectedSuccess = false;
    uint32_t expectedTime = 0;
    size_t expectedReceived = 13900;
    const char* expectedDiagnostic = "rx_timeout";
    nowMs = 4713;

    if (scenario == "observed-gap") {
        tcp.arrivals = {{4713, 13900}, {7713, fullFrame - 13900}};
        expectedSuccess = CALLBACK_PATCHED;
        expectedTime = CALLBACK_PATCHED ? 7714 : 6714;
        expectedReceived = CALLBACK_PATCHED ? fullFrame : 13900;
    } else if (scenario == "drip-deadline") {
        tcp.arrivals = {{4713, 13900}, {7713, 1}, {10713, 1}, {13713, 1},
                        {16713, 1}, {19713, 1}, {22713, fullFrame - 13905}};
        expectedTime = 20000;
        expectedReceived = 13905;
        expectedDiagnostic = "rx_deadline";
    } else if (scenario == "pcm-idle") {
        tcp.arrivals = {{4713, 13900}};
        expectedTime = 12713;
    } else if (scenario == "control-idle") {
        nowMs = 21000;  // A control frame must ignore a previous PCM deadline.
        pcmReceiving = false;
        requested = 8;
        tcp.arrivals = {{21000, 1}, {24000, 7}};
        expectedTime = 23001;
        expectedReceived = 1;
    } else if (scenario == "last-read-deadline") {
        nowMs = 19999;
        pcmLastProgress = 19000;
        tcp.arrivals = {{19999, fullFrame, 2}};  // Scheduling delay across the final read.
        expectedTime = 20001;
        expectedReceived = fullFrame;
        expectedDiagnostic = "rx_deadline";
    } else if (scenario == "shared-idle") {
        nowMs = 7999;  // New readCb call must not restart the envelope's idle clock.
        expectedTime = 8000;
        expectedReceived = 0;
    } else if (scenario == "disconnect") {
        tcp.arrivals = {{4713, 13900}, {7713, fullFrame - 13900}};
        tcp.disconnectAt = 5000;
        expectedTime = 5000;
        expectedDiagnostic = "rx_disconnected";
    } else if (scenario == "zero-progress") {
        nowMs = 7999;
        tcp.arrivals = {{7999, fullFrame}};
        tcp.zeroReads = 10;
        expectedTime = 8000;
        expectedReceived = 0;
    } else {
        throw std::runtime_error("unknown scenario");
    }

    WSclient_t client{&tcp};
    WebSockets socket;
    auto* buffer = static_cast<uint8_t*>(malloc(requested));
    expect(buffer != nullptr, "test allocation failed");
    unsigned callbackCount = 0;
    unsigned releases = 0;
    bool callbackSuccess = false;
    const bool result = socket.readCb(&client, buffer, requested, [&](WSclient_t* callbackClient, bool ok) {
        ++callbackCount;
        expect(callbackClient == &client, "callback got another client");
        callbackSuccess = ok;
        if (ok) {
            for (size_t i = 0; i < requested; ++i) {
                expect(buffer[i] == static_cast<uint8_t>(i % 251), "received payload was changed or repeated");
            }
        }
        free(buffer);  // Mirrors the actual payload callback's single-owner cleanup.
        buffer = nullptr;
        ++releases;
    });

    expect(result == expectedSuccess && callbackSuccess == expectedSuccess, "unexpected completion result");
    expect(callbackCount == 1 && releases == 1, "completion or buffer release happened more than once");
    expect(nowMs == expectedTime, "wrong receive deadline");
    expect(tcp.totalRead == expectedReceived, "wrong byte count or automatic replay");
    if (CALLBACK_PATCHED) {
        expect(progressBytes == tcp.totalRead, "progress hook did not match successful reads");
        expect(zeroProgressCalls == 0, "zero-byte read incorrectly renewed progress");
        if (!expectedSuccess) {
            expect(lastDiagnostic == expectedDiagnostic, "wrong failure diagnosis");
            expect(diagnosticExpected == requested && diagnosticReceived == tcp.totalRead,
                   "failure byte counters do not match transport progress");
        }
    }
    std::cout << "PASS " << scenario << " received=" << tcp.totalRead << " success=" << result
              << " age_ms=" << nowMs << " callbacks=" << callbackCount << '\n';
}

int main(int argc, char** argv) {
    try {
        expect(argc == 2, "scenario argument missing");
        run(argv[1]);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
