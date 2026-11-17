#include <unity.h>

#include "outbound_diagnostics.h"
#include "outbound_receive_policy.h"

void setUp() {}
void tearDown() {}

static void test_reported_delay_and_pause_keep_the_binary_window_open() {
    outbound::BinaryReceiveWindow window;
    window.begin(0);
    TEST_ASSERT_FALSE(window.idleExpired(4713));
    window.progress(4713, 13900);
    // The observed release-.2 disconnect was at age7015 despite a valid command.
    TEST_ASSERT_FALSE(window.idleExpired(7015));
    window.progress(8000, 49188 - 13900);
    TEST_ASSERT_FALSE(window.idleExpired(8000));
}

static void test_fragment_progress_is_not_capped_at_five_seconds_from_envelope() {
    outbound::BinaryReceiveWindow window;
    window.begin(100);
    window.progress(4813, 1024);
    TEST_ASSERT_FALSE(window.idleExpired(9000));
    window.progress(9000, 1024);
    TEST_ASSERT_FALSE(window.idleExpired(15000));
    TEST_ASSERT_TRUE(window.idleExpired(17000));
}

static void test_empty_reads_do_not_extend_idle_window_and_timer_wrap_is_safe() {
    outbound::BinaryReceiveWindow window;
    const uint32_t start = UINT32_MAX - 100;
    window.begin(start);
    window.progress(start + 7000, 0);
    TEST_ASSERT_FALSE(window.idleExpired(start + 7999));
    TEST_ASSERT_TRUE(window.idleExpired(start + 8000));
    window.progress(start + 8000, 1);
    TEST_ASSERT_FALSE(window.idleExpired(start + 15999));
    TEST_ASSERT_TRUE(window.idleExpired(start + 16000));
}

static void test_progress_never_renews_absolute_or_monotonic_command_deadline() {
    constexpr int64_t utc = INT64_C(1800000000000);
    outbound::BinaryReceiveWindow window;
    window.begin(0);
    for (uint32_t now = 0; now <= 20000; now += 100) {
        window.progress(now, 1);
        TEST_ASSERT_FALSE(window.idleExpired(now));
        const bool valid = outbound::classifyCommand(true, 1, 1, now, 0,
            utc + now, utc + 19800) == outbound::CommandValidity::CURRENT;
        TEST_ASSERT_EQUAL(now < 19800, valid);
    }
    // Even a later server expiry cannot extend the20-second device lifetime.
    TEST_ASSERT_EQUAL(outbound::CommandValidity::AGE_TIMEOUT,
        outbound::classifyCommand(true, 1, 1, 20000, 0, utc + 20000, utc + 25000));
}

static void test_new_envelope_restarts_only_its_own_idle_window() {
    outbound::BinaryReceiveWindow window;
    window.begin(0);
    TEST_ASSERT_TRUE(window.idleExpired(100000));
    window.begin(100000);
    TEST_ASSERT_FALSE(window.idleExpired(107999));
    TEST_ASSERT_TRUE(window.idleExpired(108000));
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_reported_delay_and_pause_keep_the_binary_window_open);
    RUN_TEST(test_fragment_progress_is_not_capped_at_five_seconds_from_envelope);
    RUN_TEST(test_empty_reads_do_not_extend_idle_window_and_timer_wrap_is_safe);
    RUN_TEST(test_progress_never_renews_absolute_or_monotonic_command_deadline);
    RUN_TEST(test_new_envelope_restarts_only_its_own_idle_window);
    return UNITY_END();
}
