#include <unity.h>
#include <algorithm>
#include <vector>

#include "outbound_ws_writer.h"

void setUp() {}
void tearDown() {}

static std::vector<uint8_t> payload(size_t size) {
    std::vector<uint8_t> data(size);
    for (size_t i = 0; i < size; ++i) data[i] = static_cast<uint8_t>(i);
    return data;
}

static void checkWire(const std::vector<uint8_t>& wire, const std::vector<uint8_t>& expected) {
    std::vector<uint8_t> decoded;
    size_t offset = 0;
    size_t frames = 0;
    while (offset < wire.size()) {
        TEST_ASSERT_GREATER_OR_EQUAL(offset + 6, wire.size());
        const uint8_t op = wire[offset++];
        TEST_ASSERT_EQUAL_UINT8(frames == 0 ? 2 : 0, op & 0x0f);
        TEST_ASSERT_EQUAL_UINT8(0, op & 0x70);
        const uint8_t flags = wire[offset++];
        TEST_ASSERT_TRUE(flags & 0x80);
        size_t count = flags & 0x7f;
        if (count == 126) {
            count = (static_cast<size_t>(wire[offset]) << 8) | wire[offset + 1];
            offset += 2;
            TEST_ASSERT_GREATER_OR_EQUAL(126, count);
        } else {
            TEST_ASSERT_LESS_THAN(126, count);
        }
        TEST_ASSERT_LESS_OR_EQUAL(outbound::WS_FRAGMENT_BYTES, count);
        TEST_ASSERT_GREATER_OR_EQUAL(offset + 4 + count, wire.size());
        const size_t mask = offset;
        offset += 4;
        for (size_t i = 0; i < count; ++i) {
            decoded.push_back(wire[offset + i] ^ wire[mask + i % 4]);
        }
        offset += count;
        TEST_ASSERT_EQUAL(offset == wire.size(), (op & 0x80) != 0);
        ++frames;
    }
    TEST_ASSERT_EQUAL(expected.size(), decoded.size());
    TEST_ASSERT_EQUAL_MEMORY(expected.data(), decoded.data(), expected.size());
}

static void test_sizes_masks_and_partial_writes() {
    for (size_t size : {1u, 125u, 126u, 1024u, 1025u, 7783u, 131108u, 524324u}) {
        const auto data = payload(size);
        const auto original = data;
        for (size_t writeLimit : {17u, 1032u}) {
            std::vector<uint8_t> wire;
            uint32_t keys = 0;
            auto result = outbound::writeMaskedBinary(data.data(), data.size(),
                [&](uint8_t* bytes, size_t count) -> size_t {
                    TEST_ASSERT_LESS_OR_EQUAL(1032, count);
                    const size_t accepted = std::min(count, writeLimit);
                    wire.insert(wire.end(), bytes, bytes + accepted);
                    return accepted;
                }, [&]() -> uint32_t { return 0x12345678u + keys++; }, []() { return true; });
            TEST_ASSERT_EQUAL(outbound::BinaryWriteStatus::COMPLETE, result.status);
            TEST_ASSERT_EQUAL(size, result.payloadBytes);
            TEST_ASSERT_EQUAL((size + 1023) / 1024, result.frames);
            TEST_ASSERT_EQUAL(result.frames, keys);
            TEST_ASSERT_TRUE(data == original);
            checkWire(wire, data);
        }
    }
}

static void test_failed_write_stops_without_replay() {
    const auto data = payload(7783);
    std::vector<uint8_t> wire;
    size_t calls = 0;
    auto result = outbound::writeMaskedBinary(data.data(), data.size(),
        [&](uint8_t* bytes, size_t size) -> size_t {
            if (++calls == 3) return 0;
            wire.insert(wire.end(), bytes, bytes + size);
            return size;
        }, []() -> uint32_t { return 42; }, []() { return true; });
    TEST_ASSERT_EQUAL(outbound::BinaryWriteStatus::WRITE_FAILED, result.status);
    TEST_ASSERT_EQUAL(2048, result.payloadBytes);
    TEST_ASSERT_EQUAL(2, result.frames);
    TEST_ASSERT_EQUAL(3, calls);
    TEST_ASSERT_EQUAL(2064, wire.size());
}

static void test_expiry_during_partial_frame_stops_without_replay() {
    const auto data = payload(7783);
    size_t calls = 0;
    auto result = outbound::writeMaskedBinary(data.data(), data.size(),
        [&](uint8_t*, size_t) -> size_t { ++calls; return 17; },
        []() -> uint32_t { return 0; }, [&]() { return calls < 3; });
    TEST_ASSERT_EQUAL(outbound::BinaryWriteStatus::EXPIRED, result.status);
    TEST_ASSERT_EQUAL(0, result.payloadBytes);
    TEST_ASSERT_EQUAL(0, result.frames);
    TEST_ASSERT_EQUAL(3, calls);
}

static void test_invalid_and_already_expired_never_write() {
    uint8_t byte = 1;
    size_t calls = 0;
    auto write = [&](uint8_t*, size_t count) -> size_t { ++calls; return count; };
    auto random = []() -> uint32_t { return 42; };
    auto current = []() { return false; };
    TEST_ASSERT_EQUAL(outbound::BinaryWriteStatus::INVALID,
                      outbound::writeMaskedBinary(nullptr, 1, write, random, current).status);
    TEST_ASSERT_EQUAL(outbound::BinaryWriteStatus::INVALID,
                      outbound::writeMaskedBinary(&byte, 0, write, random, current).status);
    TEST_ASSERT_EQUAL(outbound::BinaryWriteStatus::EXPIRED,
                      outbound::writeMaskedBinary(&byte, 1, write, random, current).status);
    TEST_ASSERT_EQUAL(0, calls);
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_sizes_masks_and_partial_writes);
    RUN_TEST(test_failed_write_stops_without_replay);
    RUN_TEST(test_expiry_during_partial_frame_stops_without_replay);
    RUN_TEST(test_invalid_and_already_expired_never_write);
    return UNITY_END();
}
