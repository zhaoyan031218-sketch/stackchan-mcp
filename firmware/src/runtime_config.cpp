#include "runtime_config.h"

#include <ArduinoJson.h>
#include <cstring>

namespace runtime_config {
namespace {

struct BoundedReader {
    const char* data;
    size_t size;
    size_t position = 0;
    int read() { return position < size ? static_cast<unsigned char>(data[position++]) : -1; }
    size_t readBytes(char* destination, size_t count) {
        const size_t available = size - position;
        if (count > available) count = available;
        memcpy(destination, data + position, count);
        position += count;
        return count;
    }
};

bool asciiAlphaNumeric(char ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
           (ch >= '0' && ch <= '9');
}

bool dnsHost(const std::string& value) {
    if (value.empty() || value.size() > 253) return false;
    size_t labelStart = 0;
    bool hasDot = false;
    for (size_t i = 0; i <= value.size(); ++i) {
        if (i == value.size() || value[i] == '.') {
            const size_t length = i - labelStart;
            if (!length || length > 63 || value[labelStart] == '-' || value[i - 1] == '-') return false;
            if (i < value.size()) hasDot = true;
            labelStart = i + 1;
        } else {
            if (!asciiAlphaNumeric(value[i]) && value[i] != '-') return false;
        }
    }
    return hasDot;
}

bool stringField(JsonObjectConst object, const char* name, std::string& result) {
    const JsonVariantConst value = object[name];
    if (!value.is<const char*>()) return false;
    const JsonString string = value.as<JsonString>();
    if (strlen(string.c_str()) != string.size()) return false;
    result.assign(string.c_str(), string.size());
    return true;
}

bool noControls(const std::string& value) {
    for (const unsigned char ch : value) {
        if (ch < 0x20 || ch == 0x7f) return false;
    }
    return true;
}

bool wifiPassword(const std::string& value) {
    if (value.empty()) return true;
    if (value.size() >= 8 && value.size() <= 63) return noControls(value);
    if (value.size() != 64) return false;
    for (const char ch : value) {
        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f') ||
              (ch >= 'A' && ch <= 'F'))) return false;
    }
    return true;
}

}  // namespace

const char* parse(const char* data, size_t size, Config& result, CertificateValidator validateCa) {
    if (!data || !size || size > MAX_LINE_BYTES) return "invalid_size";
    if (memchr(data, '\0', size)) return "invalid_json";
    JsonDocument document;
    BoundedReader reader{data, size};
    if (deserializeJson(document, reader, DeserializationOption::NestingLimit(3))) return "invalid_json";
    while (reader.position < size) {
        const char ch = data[reader.position++];
        if (ch != ' ' && ch != '\t' && ch != '\r' && ch != '\n') return "invalid_json";
    }
    if (!document.is<JsonObject>()) return "invalid_request";
    const JsonObjectConst object = document.as<JsonObjectConst>();
    std::string requestType;
    if (!stringField(object, "type", requestType) || requestType != "stackchan.configure" ||
        !object["v"].is<int>() || object["v"].as<int>() != 1) return "invalid_protocol";

    Config candidate;
    if (!stringField(object, "host", candidate.host) || !dnsHost(candidate.host)) return "invalid_host";
    if (!stringField(object, "token", candidate.token) || candidate.token.size() < 24 ||
        candidate.token.size() > 512) return "invalid_token";
    for (const char ch : candidate.token) {
        if (!asciiAlphaNumeric(ch) && !strchr("-._~+/=", ch)) return "invalid_token";
    }
    if (!stringField(object, "ssid", candidate.ssid) || candidate.ssid.empty() ||
        candidate.ssid.size() > 32 || !noControls(candidate.ssid)) return "invalid_ssid";
    if (!stringField(object, "password", candidate.password) || !wifiPassword(candidate.password)) return "invalid_password";
    if (!stringField(object, "ca_pem", candidate.caPem) || candidate.caPem.size() > MAX_CA_BYTES ||
        candidate.caPem.find("-----BEGIN CERTIFICATE-----") != 0 ||
        candidate.caPem.find("-----END CERTIFICATE-----") == std::string::npos ||
        !validateCa || !validateCa(candidate.caPem.c_str(), candidate.caPem.size() + 1)) return "invalid_ca";
    result = std::move(candidate);
    return nullptr;
}

}  // namespace runtime_config

#if defined(STACKCHAN_RUNTIME_CONFIG)
#include <Arduino.h>
#include <Preferences.h>
#include <mbedtls/x509_crt.h>

namespace {

runtime_config::Config activeConfig;
char setupLine[runtime_config::MAX_LINE_BYTES + 1];
size_t setupLength = 0;
bool discardLine = false;
uint32_t lastInputMs = 0;
uint32_t lastReadyMs = 0;

bool validCertificate(const char* pem, size_t size) {
    mbedtls_x509_crt certificate;
    mbedtls_x509_crt_init(&certificate);
    const int result = mbedtls_x509_crt_parse(&certificate,
        reinterpret_cast<const unsigned char*>(pem), size);
    mbedtls_x509_crt_free(&certificate);
    return result == 0;
}

void resetLine() {
    memset(setupLine, 0, sizeof(setupLine));
    setupLength = 0;
    discardLine = false;
}

void setupResult(const char* error) {
    // Every error is a fixed code, never parser input or a library diagnostic.
    Serial.print("{\"type\":\"stackchan.setup.result\",\"ok\":");
    Serial.print(error ? "false" : "true");
    if (error) {
        Serial.print(",\"error\":\"");
        Serial.print(error);
        Serial.print('"');
    }
    Serial.println("}");
}

bool loadConfiguration() {
    Preferences preferences;
    if (!preferences.begin("sc-cloud", true)) return false;
    const size_t size = preferences.getBytesLength("config");
    if (!size || size > runtime_config::MAX_SAVED_BYTES) {
        preferences.end();
        return false;
    }
    std::string saved(size, '\0');
    const size_t read = preferences.getBytes("config", &saved[0], size);
    preferences.end();
    return read == size && !runtime_config::parse(saved.data(), saved.size(), activeConfig, validCertificate);
}

const char* saveConfiguration(const runtime_config::Config& candidate) {
    JsonDocument document;
    document["type"] = "stackchan.configure";
    document["v"] = 1;
    document["host"] = candidate.host;
    document["token"] = candidate.token;
    document["ssid"] = candidate.ssid;
    document["password"] = candidate.password;
    document["ca_pem"] = candidate.caPem;
    if (document.overflowed()) return "memory_failed";
    std::string encoded;
    const size_t expected = measureJson(document);
    if (expected > runtime_config::MAX_SAVED_BYTES) return "invalid_size";
    if (serializeJson(document, encoded) != expected) return "memory_failed";
    Preferences preferences;
    if (!preferences.begin("sc-cloud", false)) return "storage_open_failed";
    // NVS replaces one key atomically. Never clear the namespace or write the
    // credentials as independent keys that can leave a partially updated set.
    const bool saved = preferences.putBytes("config", encoded.data(), encoded.size()) == encoded.size();
    if (!saved) {
        preferences.end();
        return "storage_full_or_write_failed";
    }
    std::string verified(encoded.size(), '\0');
    const bool verifiedOk = preferences.getBytesLength("config") == encoded.size() &&
        preferences.getBytes("config", &verified[0], verified.size()) == verified.size() &&
        verified == encoded;
    preferences.end();
    return verifiedOk ? nullptr : "storage_verify_failed";
}

void processSetupLine() {
    runtime_config::Config candidate;
    const char* error = runtime_config::parse(setupLine, setupLength, candidate, validCertificate);
    if (!error) error = saveConfiguration(candidate);
    resetLine();
    setupResult(error);
    if (!error) {
        Serial.flush();
        delay(250);
        ESP.restart();
        // Credentials remain immutable during a boot, even if restart fails.
        for (;;) delay(1000);
    }
}

void pollSetup() {
    const uint32_t now = millis();
    if (now - lastReadyMs >= 1000) {
        Serial.println("{\"type\":\"stackchan.setup.ready\",\"v\":1}");
        lastReadyMs = now;
    }
    if ((setupLength || discardLine) && now - lastInputMs >= 5000) {
        resetLine();
        setupResult("input_timeout");
    }
    // Bound work even if a host continuously sends bytes without a newline.
    for (unsigned count = 0; count < 512 && Serial.available(); ++count) {
        const int byte = Serial.read();
        if (byte < 0) break;
        lastInputMs = millis();
        if (byte == '\n') {
            if (discardLine) {
                resetLine();
                setupResult("invalid_size");
            } else if (setupLength) {
                processSetupLine();
            }
            continue;
        }
        if (discardLine) continue;
        if (setupLength == runtime_config::MAX_LINE_BYTES) {
            memset(setupLine, 0, sizeof(setupLine));
            setupLength = 0;
            discardLine = true;
            continue;
        }
        setupLine[setupLength++] = static_cast<char>(byte);
    }
}

}  // namespace

bool runtimeConfigBootWindow() {
    const bool configured = loadConfiguration();
    Serial.println("[SETUP] STACKCHAN_RUNTIME_SETUP_V1");
    lastReadyMs = millis() - 1000;
    const uint32_t started = millis();
    // A host may start just before the 8-second announcement window ends.
    // Let that one line finish, but never extend setup beyond 13 seconds.
    while (millis() - started < 8000 ||
           ((setupLength || discardLine) && millis() - started < 13000)) {
        pollSetup();
        delay(5);
    }
    // Do not carry a partial input from the boot window into application code.
    if (configured && (setupLength || discardLine)) {
        resetLine();
        setupResult("input_timeout");
    }
    return configured;
}

void waitForRuntimeConfig() {
    for (;;) {
        pollSetup();
        delay(5);
    }
}

const char* runtimeCloudHost() { return activeConfig.host.c_str(); }
const char* runtimeCloudToken() { return activeConfig.token.c_str(); }
const char* runtimeCloudCa() { return activeConfig.caPem.c_str(); }
const char* runtimeWifiSsid() { return activeConfig.ssid.c_str(); }
const char* runtimeWifiPassword() { return activeConfig.password.c_str(); }
#endif
