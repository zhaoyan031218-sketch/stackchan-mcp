#include <unity.h>

#include "../../src/camera_service.cpp"

static camera_config_t capturedConfig;
static camera_fb_t frames[] = {{1}, {2}};
static esp_err_t initResult;
static unsigned initCount;
static unsigned deinitCount;
static unsigned captureCount;
static unsigned returnedCount;
static unsigned suspendCount;
static unsigned resumeCount;
static unsigned failCapture;
static bool conversionOk;
static bool restoreOk;
static bool playing;

esp_err_t esp_camera_init(const camera_config_t* config) {
    capturedConfig = *config;
    ++initCount;
    return initResult;
}
esp_err_t esp_camera_deinit() { ++deinitCount; return ESP_OK; }
camera_fb_t* esp_camera_fb_get() {
    ++captureCount;
    return captureCount == failCapture ? nullptr : &frames[(captureCount - 1) % 2];
}
void esp_camera_fb_return(camera_fb_t*) { ++returnedCount; }
bool frame2jpg(camera_fb_t* fb, uint8_t, uint8_t** out, size_t* len) {
    *out = static_cast<uint8_t*>(malloc(1));
    *len = 1;
    **out = fb->marker;
    return conversionOk;
}
bool isPlaybackActive() { return playing; }
bool isPcmStreamActive() { return false; }
void suspendTouchService() { ++suspendCount; }
bool resumeTouchService() { ++resumeCount; return restoreOk; }

void setUp() {
    cameraReady = false;
    cameraSessionActive = false;
    cameraSessionDeadlineMs = 0;
    cameraSessionTimeoutMs = 0;
    capturedConfig = {};
    M5 = {};
    initResult = ESP_OK;
    initCount = deinitCount = captureCount = returnedCount = 0;
    suspendCount = resumeCount = failCapture = 0;
    conversionOk = restoreOk = true;
    playing = false;
    testMillis = 10000;
}
void tearDown() {}

static void test_snapshot_uses_board_clock_and_a_fresh_frame() {
    uint8_t* jpeg = nullptr;
    size_t size = 0;
    TEST_ASSERT_TRUE(captureJpeg(&jpeg, &size));
    // GPIO 2 belongs to external I2C, not the camera clock on CoreS3.
    TEST_ASSERT_EQUAL_INT(-1, capturedConfig.pin_xclk);
    TEST_ASSERT_EQUAL_INT(12, capturedConfig.pin_sccb_sda);
    TEST_ASSERT_EQUAL_INT(11, capturedConfig.pin_sccb_scl);
    TEST_ASSERT_EQUAL_UINT(1, size);
    TEST_ASSERT_EQUAL_UINT8(2, *jpeg);
    TEST_ASSERT_EQUAL_UINT(2, returnedCount);
    TEST_ASSERT_EQUAL_UINT(1, deinitCount);
    TEST_ASSERT_EQUAL_UINT(1, resumeCount);
    TEST_ASSERT_FALSE(cameraReady);
    free(jpeg);
}

static void test_failed_init_restores_touch_without_capturing() {
    initResult = -1;
    uint8_t* jpeg = nullptr;
    size_t size = 0;
    TEST_ASSERT_FALSE(captureJpeg(&jpeg, &size));
    TEST_ASSERT_NULL(jpeg);
    TEST_ASSERT_EQUAL_UINT(0, captureCount);
    TEST_ASSERT_EQUAL_UINT(1, resumeCount);
}

static void test_failed_capture_or_conversion_restores_bus_and_clears_result() {
    for (unsigned failure = 1; failure <= 3; ++failure) {
        setUp();
        failCapture = failure;
        conversionOk = failure != 3;
        uint8_t* jpeg = nullptr;
        size_t size = 0;
        TEST_ASSERT_FALSE(captureJpeg(&jpeg, &size));
        TEST_ASSERT_NULL(jpeg);
        TEST_ASSERT_EQUAL_UINT(0, size);
        TEST_ASSERT_EQUAL_UINT(1, deinitCount);
        TEST_ASSERT_EQUAL_UINT(1, resumeCount);
    }
}

static void test_failed_bus_release_or_restore_is_not_a_successful_snapshot() {
    M5.In_I2C.releaseOk = false;
    uint8_t* jpeg = nullptr;
    size_t size = 0;
    TEST_ASSERT_FALSE(captureJpeg(&jpeg, &size));
    TEST_ASSERT_EQUAL_UINT(0, initCount);
    TEST_ASSERT_EQUAL_UINT(1, resumeCount);

    setUp();
    restoreOk = false;
    TEST_ASSERT_FALSE(captureJpeg(&jpeg, &size));
    TEST_ASSERT_NULL(jpeg);
    TEST_ASSERT_EQUAL_UINT(0, size);
    TEST_ASSERT_EQUAL_UINT(1, deinitCount);
}

static void test_playback_does_not_take_camera_bus() {
    playing = true;
    uint8_t* jpeg = nullptr;
    size_t size = 0;
    TEST_ASSERT_FALSE(captureJpeg(&jpeg, &size));
    TEST_ASSERT_FALSE(startCameraSession());
    TEST_ASSERT_EQUAL_UINT(0, suspendCount);
    TEST_ASSERT_EQUAL_UINT(0, initCount);
}

static void test_session_expiry_restores_touch_without_host_stop() {
    TEST_ASSERT_TRUE(startCameraSession(1000));
    uint8_t* jpeg = nullptr;
    size_t size = 0;
    testMillis += 900;
    TEST_ASSERT_TRUE(captureJpeg(&jpeg, &size));
    free(jpeg);
    testMillis += 900;
    updateCameraService();
    TEST_ASSERT_TRUE(isCameraSessionActive());
    TEST_ASSERT_EQUAL_UINT(0, resumeCount);
    testMillis += 100;
    updateCameraService();
    TEST_ASSERT_FALSE(isCameraSessionActive());
    TEST_ASSERT_EQUAL_UINT(1, deinitCount);
    TEST_ASSERT_EQUAL_UINT(1, resumeCount);
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_snapshot_uses_board_clock_and_a_fresh_frame);
    RUN_TEST(test_failed_init_restores_touch_without_capturing);
    RUN_TEST(test_failed_capture_or_conversion_restores_bus_and_clears_result);
    RUN_TEST(test_failed_bus_release_or_restore_is_not_a_successful_snapshot);
    RUN_TEST(test_playback_does_not_take_camera_bus);
    RUN_TEST(test_session_expiry_restores_touch_without_host_stop);
    return UNITY_END();
}
