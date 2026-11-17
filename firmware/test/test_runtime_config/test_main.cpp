#include <unity.h>
#include <ArduinoJson.h>
#include "runtime_config.h"

using runtime_config::Config;

static bool certificateOk;
static unsigned certificateCalls;
static const char CERTIFICATE[] = "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n";

static bool validateCertificate(const char* value, size_t length) {
    ++certificateCalls;
    TEST_ASSERT_EQUAL_UINT(strlen(value) + 1, length);
    return certificateOk;
}

static std::string request(const char* name = nullptr, const std::string& value = "") {
    JsonDocument document;
    document["type"] = "stackchan.configure";
    document["v"] = 1;
    document["host"] = "robot.example.com";
    document["token"] = "test-token-0123456789abcdef";
    document["ssid"] = "home-network";
    document["password"] = "test-password";
    document["ca_pem"] = CERTIFICATE;
    if (name) document[name] = value;
    std::string result;
    serializeJson(document, result);
    return result;
}

static void expectError(const std::string& encoded, const char* expected) {
    Config config{"old.example.com", "old-token", "old-ssid", "old-password", "old-ca"};
    TEST_ASSERT_EQUAL_STRING(expected,
        runtime_config::parse(encoded.data(), encoded.size(), config, validateCertificate));
    TEST_ASSERT_EQUAL_STRING("old.example.com", config.host.c_str());
    TEST_ASSERT_EQUAL_STRING("old-token", config.token.c_str());
    TEST_ASSERT_EQUAL_STRING("old-ssid", config.ssid.c_str());
    TEST_ASSERT_EQUAL_STRING("old-password", config.password.c_str());
    TEST_ASSERT_EQUAL_STRING("old-ca", config.caPem.c_str());
}

void setUp() { certificateOk = true; certificateCalls = 0; }
void tearDown() {}

static void test_valid_configuration_and_certificate_validation() {
    const std::string encoded = request("ssid", "家里的WiFi") + "\r\n";
    Config config;
    TEST_ASSERT_NULL(runtime_config::parse(encoded.data(), encoded.size(), config, validateCertificate));
    TEST_ASSERT_EQUAL_STRING("家里的WiFi", config.ssid.c_str());
    TEST_ASSERT_EQUAL_STRING("robot.example.com", config.host.c_str());
    TEST_ASSERT_EQUAL_UINT(1, certificateCalls);
}

static void test_protocol_must_match_exactly() {
    expectError(request("type", std::string("stackchan.configure\0suffix", 26)), "invalid_protocol");
    expectError(request("type", "wrong"), "invalid_protocol");
    expectError(request("v", "1"), "invalid_protocol");
    expectError("[]", "invalid_request");
}

static void test_host_is_dns_name_without_url_or_port() {
    const char* invalid[] = {"", "localhost", "https://robot.example.com", "robot.example.com:443",
                             "-robot.example.com", "robot..example.com", "robot.example.com/", "robot.example.com."};
    for (const auto* value : invalid) expectError(request("host", value), "invalid_host");
    expectError(request("host", std::string(64, 'a') + ".example.com"), "invalid_host");
}

static void test_token_matches_outbound_bearer_charset() {
    expectError(request("token", std::string(23, 'a')), "invalid_token");
    expectError(request("token", std::string(513, 'a')), "invalid_token");
    expectError(request("token", "test-token-0123456789abc:def"), "invalid_token");
    expectError(request("token", "test-token-0123456789abc\nInjected"), "invalid_token");
    expectError(request("token", std::string("test-token-0123456789abcdef") + std::string("\0x", 2)), "invalid_token");
    TEST_ASSERT_EQUAL_UINT(0, certificateCalls);
}

static void test_wifi_constraints_and_supported_password_forms() {
    expectError(request("ssid", ""), "invalid_ssid");
    expectError(request("ssid", std::string(33, 's')), "invalid_ssid");
    expectError(request("ssid", "line\nbreak"), "invalid_ssid");
    expectError(request("password", "short"), "invalid_password");
    expectError(request("password", std::string(64, 'z')), "invalid_password");
    for (const auto& password : {std::string(""), std::string(64, 'a')}) {
        const std::string encoded = request("password", password);
        Config config;
        TEST_ASSERT_NULL(runtime_config::parse(encoded.data(), encoded.size(), config, validateCertificate));
    }
}

static void test_ca_requires_pem_and_successful_certificate_parse() {
    expectError(request("ca_pem", "not-a-certificate"), "invalid_ca");
    expectError(request("ca_pem", std::string(CERTIFICATE) + std::string(4096, 'x')), "invalid_ca");
    certificateOk = false;
    expectError(request(), "invalid_ca");
    TEST_ASSERT_EQUAL_UINT(1, certificateCalls);
}

static void test_truncated_or_extra_input_never_changes_configuration() {
    std::string encoded = request();
    expectError(encoded.substr(0, encoded.size() - 1), "invalid_json");
    expectError(encoded + "{}", "invalid_json");
    expectError(encoded + std::string("\0", 1), "invalid_json");
    expectError(std::string(runtime_config::MAX_LINE_BYTES + 1, 'x'), "invalid_size");
    expectError("", "invalid_size");
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_valid_configuration_and_certificate_validation);
    RUN_TEST(test_protocol_must_match_exactly);
    RUN_TEST(test_host_is_dns_name_without_url_or_port);
    RUN_TEST(test_token_matches_outbound_bearer_charset);
    RUN_TEST(test_wifi_constraints_and_supported_password_forms);
    RUN_TEST(test_ca_requires_pem_and_successful_certificate_parse);
    RUN_TEST(test_truncated_or_extra_input_never_changes_configuration);
    return UNITY_END();
}
