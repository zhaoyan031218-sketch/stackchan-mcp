#pragma once
#include "Arduino.h"

struct TestMic {
    bool running = false;
    bool enabled = true;
    bool fail = false;
    unsigned beginCount = 0;
    unsigned recordCount = 0;
    bool deferRecordFill = false;
    bool recording = false;
    int16_t* pendingData = nullptr;
    size_t pendingCount = 0;
    struct Config {
        uint32_t sample_rate = 16000;
        bool stereo = false;
        uint8_t magnification = 1;
        uint8_t noise_filter_level = 0;
    };
    Config config() const { return {}; }
    void config(Config) {}
    bool isEnabled() const { return enabled; }
    bool isRunning() const { return running; }
    bool begin() { ++beginCount; running = enabled && !fail; return running; }
    void end() { running = false; recording = false; pendingData = nullptr; pendingCount = 0; }
    size_t isRecording() const { return recording ? 1 : 0; }
    void finishRecording(int16_t sample) {
        for (size_t i = 0; i < pendingCount; ++i) pendingData[i] = sample;
        recording = false;
        pendingData = nullptr;
        pendingCount = 0;
    }
    bool record(int16_t* data, size_t count, uint32_t) {
        ++recordCount;
        if (!running || fail) return false;
        recording = deferRecordFill;
        pendingData = deferRecordFill ? data : nullptr;
        pendingCount = deferRecordFill ? count : 0;
        if (!deferRecordFill) memset(data, 0, count * sizeof(int16_t));
        return true;
    }
};
struct TestSpeaker {
    bool running = false;
    bool playing = false;
    bool fail = false;
    bool isRunning() const { return running; }
    bool isPlaying() const { return playing; }
    bool begin() { running = !fail; return running; }
    void end() { running = false; playing = false; }
    void stop() { playing = false; }
    void setVolume(int) {}
    bool playRaw(const int16_t*, size_t, uint32_t, bool, int, int, bool) {
        playing = !fail;
        return playing;
    }
};
struct TestI2C {
    bool releaseOk = true;
    unsigned releaseCount = 0;
    bool begin() { return true; }
    bool scanID(uint8_t) { return true; }
    bool release() { ++releaseCount; return releaseOk; }
};
struct TestM5 { TestMic Mic; TestSpeaker Speaker; TestI2C In_I2C; };
inline TestM5 M5;
