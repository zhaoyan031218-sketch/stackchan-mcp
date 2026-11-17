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
struct TestExternalI2C {
    bool beginOk = true;
    bool startOk = true;
    bool writeOk = true;
    bool readOk = true;
    bool stopOk = true;
    bool acknowledged[128] = {};
    uint8_t currentAddress = 0;
    uint8_t readData[25] = {};
    unsigned beginCount = 0;
    unsigned startCount = 0;
    unsigned stopCount = 0;
    unsigned writeCount = 0;
    unsigned readCount = 0;
    bool begin() { ++beginCount; return beginOk; }
    // M5GFX starts an asynchronous address transfer here. A missing device is
    // reported by stop(), not by the successful transaction start.
    bool start(uint8_t address, bool, uint32_t) {
        ++startCount;
        currentAddress = address;
        return startOk;
    }
    bool stop() { ++stopCount; return stopOk && acknowledged[currentAddress]; }
    // This is the pinned M5Unified scanID contract.
    bool scanID(uint8_t address, uint32_t frequency) { return start(address, false, frequency) && stop(); }
    bool write(const uint8_t*, size_t) { ++writeCount; return writeOk && acknowledged[currentAddress]; }
    bool read(uint8_t* data, size_t length, bool = false) {
        ++readCount;
        if (!readOk || !acknowledged[currentAddress] || length > sizeof(readData)) return false;
        memcpy(data, readData, length);
        return true;
    }
};
struct TestM5 { TestMic Mic; TestSpeaker Speaker; TestI2C In_I2C; TestExternalI2C Ex_I2C; };
inline TestM5 M5;
