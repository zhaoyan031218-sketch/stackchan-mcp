#pragma once

#include <stddef.h>
#include <string>

namespace runtime_config {

constexpr size_t MAX_LINE_BYTES = 12288;
constexpr size_t MAX_CA_BYTES = 4096;
constexpr size_t MAX_SAVED_BYTES = 6144;

struct Config {
    std::string host;
    std::string token;
    std::string ssid;
    std::string password;
    std::string caPem;
};

using CertificateValidator = bool (*)(const char*, size_t);

// Parse into a temporary value; failure never changes the active configuration.
const char* parse(const char* data, size_t size, Config& result, CertificateValidator validateCa);

}  // namespace runtime_config

#if defined(STACKCHAN_RUNTIME_CONFIG)
// Call before peripheral initialization. Serial must already be running.
bool runtimeConfigBootWindow();
// Used only before application services start, when no configuration is saved.
void waitForRuntimeConfig();
const char* runtimeCloudHost();
const char* runtimeCloudToken();
const char* runtimeCloudCa();
const char* runtimeWifiSsid();
const char* runtimeWifiPassword();
#endif
