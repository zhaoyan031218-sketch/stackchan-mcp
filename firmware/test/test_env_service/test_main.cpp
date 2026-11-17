#include <unity.h>

#include "../../src/env_service.cpp"

void setUp() {
    M5 = {};
    s_sht31Found = s_qmpFound = s_qmpCalOk = false;
    s_sht31Addr = s_qmpAddr = 0;
    s_busReady = false;
    s_lastError[0] = '\0';
    testMillis = 10000;
}
void tearDown() {}

static void test_deferred_address_nack_does_not_detect_a_sensor() {
    // beginTransaction succeeds; NACK arrives only at endTransaction.
    TEST_ASSERT_TRUE(M5.Ex_I2C.start(0x44, false, 100000));
    TEST_ASSERT_FALSE(M5.Ex_I2C.stop());
    TEST_ASSERT_FALSE(initEnvService());
    TEST_ASSERT_FALSE(isEnvAvailable());
    TEST_ASSERT_TRUE(getEnvRuntimeStatus().busReady);
    TEST_ASSERT_EQUAL_UINT(0, M5.Ex_I2C.writeCount);
    TEST_ASSERT_EQUAL_STRING("no sensor ACK on Port A", envLastError());
}

static void test_bus_initialization_failure_is_not_sensor_absence() {
    M5.Ex_I2C.beginOk = false;
    TEST_ASSERT_FALSE(initEnvService());
    TEST_ASSERT_EQUAL_UINT(0, M5.Ex_I2C.startCount);
    TEST_ASSERT_FALSE(getEnvRuntimeStatus().busReady);
    TEST_ASSERT_EQUAL_STRING("Port A bus init failed", envLastError());
}

static void test_failed_sht31_reset_is_not_a_ready_sensor() {
    M5.Ex_I2C.acknowledged[0x44] = true;
    M5.Ex_I2C.writeOk = false;
    TEST_ASSERT_FALSE(initEnvService());
    TEST_ASSERT_FALSE(isEnvAvailable());
    TEST_ASSERT_EQUAL_STRING("SHT31 reset failed", envLastError());
}

static void test_alternate_sht31_is_detected_without_a_qmp() {
    M5.Ex_I2C.acknowledged[0x45] = true;
    TEST_ASSERT_TRUE(initEnvService());
    TEST_ASSERT_TRUE(s_sht31Found);
    TEST_ASSERT_EQUAL_UINT8(0x45, s_sht31Addr);
    TEST_ASSERT_FALSE(s_qmpFound);
}

static void test_qmp_requires_a_valid_chip_id() {
    M5.Ex_I2C.acknowledged[0x70] = true;
    M5.Ex_I2C.readData[0] = 0;
    TEST_ASSERT_FALSE(initEnvService());
    TEST_ASSERT_FALSE(s_qmpFound);
    TEST_ASSERT_EQUAL_STRING("QMP6988 ID mismatch", envLastError());
}

static void test_failed_read_does_not_leave_stale_diagnostics_after_recovery() {
    M5.Ex_I2C.acknowledged[0x44] = true;
    TEST_ASSERT_TRUE(initEnvService());
    float temperature = 0, humidity = 0, pressure = 0;
    TEST_ASSERT_FALSE(readEnv(temperature, humidity, pressure));
    TEST_ASSERT_EQUAL_STRING("CRC mismatch", envLastError());
    const uint8_t sample[] = {0x66, 0x66, 0x93, 0x80, 0x00, 0xA2};
    memcpy(M5.Ex_I2C.readData, sample, sizeof(sample));
    TEST_ASSERT_TRUE(readEnv(temperature, humidity, pressure));
    TEST_ASSERT_FLOAT_WITHIN(0.01, 25.0, temperature);
    TEST_ASSERT_FLOAT_WITHIN(0.01, 50.0, humidity);
    TEST_ASSERT_TRUE(isnan(pressure));
    TEST_ASSERT_EQUAL_STRING("", envLastError());
}

static void test_absent_sensors_never_return_previous_reading_values() {
    TEST_ASSERT_FALSE(initEnvService());
    float temperature = 20, humidity = 50, pressure = 1000;
    TEST_ASSERT_FALSE(readEnv(temperature, humidity, pressure));
    TEST_ASSERT_TRUE(isnan(temperature));
    TEST_ASSERT_TRUE(isnan(humidity));
    TEST_ASSERT_TRUE(isnan(pressure));
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_deferred_address_nack_does_not_detect_a_sensor);
    RUN_TEST(test_bus_initialization_failure_is_not_sensor_absence);
    RUN_TEST(test_failed_sht31_reset_is_not_a_ready_sensor);
    RUN_TEST(test_alternate_sht31_is_detected_without_a_qmp);
    RUN_TEST(test_qmp_requires_a_valid_chip_id);
    RUN_TEST(test_failed_read_does_not_leave_stale_diagnostics_after_recovery);
    RUN_TEST(test_absent_sensors_never_return_previous_reading_values);
    return UNITY_END();
}
