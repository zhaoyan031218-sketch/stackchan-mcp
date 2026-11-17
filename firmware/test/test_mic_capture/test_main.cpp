#include <unity.h>

#include "../../src/mic_service.cpp"
#include "../../src/recording_store.cpp"

static bool streamActive = false;
static bool playbackActive = false;
static bool resumeRequested = false;
bool isBoothMode() { return false; }
bool audioGateEnter(const char*, uint32_t) { return true; }
void audioGateLeave(const char*) {}
void logAudioMemory(const char*) {}
bool isPlaybackActive() { return playbackActive; }
bool isPcmStreamActive() { return streamActive; }
void setFaceExpression(FaceExpression) {}
void requestMicResume() { resumeRequested = true; }
void clearMicResumeRequest() { resumeRequested = false; }
bool shouldResumeMic() { return resumeRequested && !playbackActive && !streamActive; }

void setUp() {
    clearLastRecording();
    free(record_buffer);
    record_buffer = nullptr;
    M5 = {};
    streamActive = false;
    playbackActive = false;
    resumeRequested = false;
    capture_stop_pending = false;
    frame_count = 0;
    record_failure_count = 0;
    mic_state = MIC_IDLE;
    testMillis += 2000;
    TEST_ASSERT_TRUE(initMicrophone());
    M5.Mic.deferRecordFill = true;
}

void tearDown() {
    M5.Mic.end();
    clearLastRecording();
    free(record_buffer);
    record_buffer = nullptr;
}

static void test_pending_frame_is_not_read_or_submitted_twice() {
    capture_frame[0] = 20000;  // Stale data must not be interpreted as new speech.
    updateMicrophone();
    TEST_ASSERT_EQUAL_UINT(1, M5.Mic.recordCount);
    TEST_ASSERT_EQUAL_UINT(0, frame_count);
    TEST_ASSERT_EQUAL_UINT(0, pre_buf_write);
    updateMicrophone();
    TEST_ASSERT_EQUAL_UINT(1, M5.Mic.recordCount);
    TEST_ASSERT_EQUAL_UINT(0, frame_count);

    M5.Mic.finishRecording(1000);
    updateMicrophone();
    TEST_ASSERT_EQUAL_UINT(1, frame_count);
    TEST_ASSERT_EQUAL_INT16(1000, pre_trigger_buf[0]);
    TEST_ASSERT_EQUAL_UINT(2, M5.Mic.recordCount);
    updateMicrophone();
    TEST_ASSERT_EQUAL_UINT(1, frame_count);
    TEST_ASSERT_EQUAL_UINT(2, M5.Mic.recordCount);
}

static void test_stop_and_resume_does_not_consume_aborted_frame() {
    updateMicrophone();
    capture_frame[0] = 20000;  // A partially completed DMA write.
    TEST_ASSERT_TRUE(suspendMicrophoneCapture());
    TEST_ASSERT_TRUE(initMicrophone());
    updateMicrophone();
    TEST_ASSERT_EQUAL_UINT(0, frame_count);
    TEST_ASSERT_EQUAL_UINT(0, pre_buf_write);
    M5.Mic.finishRecording(0);
    updateMicrophone();
    TEST_ASSERT_EQUAL_UINT(1, frame_count);
    TEST_ASSERT_EQUAL_INT16(0, pre_trigger_buf[0]);
}

static void test_failed_capture_is_retried_without_reading_stale_audio() {
    M5.Mic.fail = true;
    capture_frame[0] = 20000;
    updateMicrophone();
    TEST_ASSERT_EQUAL_UINT(1, record_failure_count);
    TEST_ASSERT_EQUAL_UINT(0, frame_count);
    M5.Mic.fail = false;
    updateMicrophone();
    TEST_ASSERT_EQUAL_UINT(2, M5.Mic.recordCount);
    TEST_ASSERT_EQUAL_UINT(0, frame_count);
    M5.Mic.finishRecording(0);
    updateMicrophone();
    TEST_ASSERT_EQUAL_UINT(1, frame_count);
}

static void test_pcm_stream_does_not_queue_microphone_capture() {
    streamActive = true;
    updateMicrophone();
    TEST_ASSERT_EQUAL_UINT(0, M5.Mic.recordCount);
    TEST_ASSERT_EQUAL_UINT(0, frame_count);
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_pending_frame_is_not_read_or_submitted_twice);
    RUN_TEST(test_stop_and_resume_does_not_consume_aborted_frame);
    RUN_TEST(test_failed_capture_is_retried_without_reading_stale_audio);
    RUN_TEST(test_pcm_stream_does_not_queue_microphone_capture);
    return UNITY_END();
}
