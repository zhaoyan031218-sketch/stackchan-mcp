#pragma once

#include <stddef.h>
#include <stdint.h>

namespace outbound {

// One logical binary message, bounded RFC 6455 fragments. The full SCB1 reply
// stays in PSRAM; only this small masked frame is copied onto the task stack.
constexpr size_t WS_FRAGMENT_BYTES = 1024;
constexpr size_t WS_FRAME_HEADER_BYTES = 8;

enum class BinaryWriteStatus { COMPLETE, INVALID, EXPIRED, WRITE_FAILED };

struct BinaryWriteResult {
    BinaryWriteStatus status = BinaryWriteStatus::INVALID;
    size_t payloadBytes = 0;  // Only fully written fragments count.
    size_t frames = 0;
};

// write() may accept a prefix. Never resend bytes it has accepted, restart a
// partial frame, or send after the command is no longer current. The caller
// must close the connection on any incomplete result to discard a partial message.
template <typename Write, typename Random, typename Current>
BinaryWriteResult writeMaskedBinary(const uint8_t* data, size_t size,
                                   Write write, Random random, Current current) {
    BinaryWriteResult result;
    if (!data || !size) return result;
    uint8_t frame[WS_FRAME_HEADER_BYTES + WS_FRAGMENT_BYTES];
    while (result.payloadBytes < size) {
        if (!current()) {
            result.status = BinaryWriteStatus::EXPIRED;
            return result;
        }
        const size_t remaining = size - result.payloadBytes;
        const size_t count = remaining < WS_FRAGMENT_BYTES ? remaining : WS_FRAGMENT_BYTES;
        const bool final = count == remaining;
        // Binary opcode on the first frame, continuation opcode thereafter.
        frame[0] = (final ? 0x80 : 0x00) | (result.frames == 0 ? 0x02 : 0x00);
        size_t header = 2;
        if (count < 126) {
            frame[1] = 0x80 | static_cast<uint8_t>(count);
        } else {
            frame[1] = 0x80 | 126;
            frame[2] = static_cast<uint8_t>(count >> 8);
            frame[3] = static_cast<uint8_t>(count);
            header = 4;
        }
        // Production supplies esp_random(), with the Wi-Fi hardware RNG active.
        // A new unpredictable key is required for every client-to-server frame.
        const uint32_t key = random();
        uint8_t* mask = frame + header;
        for (size_t i = 0; i < 4; ++i) mask[i] = static_cast<uint8_t>(key >> (8 * i));
        header += 4;
        for (size_t i = 0; i < count; ++i) {
            frame[header + i] = data[result.payloadBytes + i] ^ mask[i % 4];
        }
        size_t offset = 0;
        while (offset < header + count) {
            if (!current()) {
                result.status = BinaryWriteStatus::EXPIRED;
                return result;
            }
            const size_t left = header + count - offset;
            const size_t sent = write(frame + offset, left);
            if (!sent || sent > left) {
                result.status = BinaryWriteStatus::WRITE_FAILED;
                return result;
            }
            offset += sent;
        }
        result.payloadBytes += count;
        ++result.frames;
    }
    result.status = BinaryWriteStatus::COMPLETE;
    return result;
}

}  // namespace outbound
