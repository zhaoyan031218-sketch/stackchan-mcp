#include <unity.h>
#include "outbound_diagnostics.h"

using outbound::CommandValidity;
static constexpr int64_t NOW = INT64_C(1800000000000);

void setUp() {}
void tearDown() {}

static void test_reject_reasons_distinguish_connection_clock_and_age() {
    TEST_ASSERT_EQUAL(CommandValidity::CURRENT,
        outbound::classifyCommand(true, 2, 2, 100, 0, NOW, NOW + 1000));
    TEST_ASSERT_EQUAL(CommandValidity::DISCONNECTED,
        outbound::classifyCommand(false, 2, 2, 100, 0, NOW, NOW + 1000));
    TEST_ASSERT_EQUAL(CommandValidity::GENERATION_CHANGED,
        outbound::classifyCommand(true, 2, 3, 100, 0, NOW, NOW + 1000));
    TEST_ASSERT_EQUAL(CommandValidity::AGE_TIMEOUT,
        outbound::classifyCommand(true, 2, 2, 20000, 0, NOW, NOW + 1000));
    TEST_ASSERT_EQUAL(CommandValidity::CLOCK_UNAVAILABLE,
        outbound::classifyCommand(true, 2, 2, 100, 0, 0, NOW));
    TEST_ASSERT_EQUAL(CommandValidity::DEADLINE_ELAPSED,
        outbound::classifyCommand(true, 2, 2, 100, 0, NOW, NOW));
    TEST_ASSERT_EQUAL(CommandValidity::DEADLINE_TOO_FAR,
        outbound::classifyCommand(true, 2, 2, 100, 0, NOW, NOW + 25001));
}

static void test_classification_preserves_existing_admission_at_boundaries() {
    const uint32_t started[] = {0, 100, UINT32_MAX - 10};
    const uint32_t ages[] = {0, 1, 19999, 20000, 20001};
    const int64_t clock[] = {-1, 0, NOW};
    const int64_t deadlines[] = {NOW - 1, NOW, NOW + 1, NOW + 25000, NOW + 25001};
    for (const uint32_t start : started) {
        for (const uint32_t age : ages) {
            for (const int64_t utc : clock) {
                for (const int64_t deadline : deadlines) {
                    for (const bool connected : {false, true}) {
                        for (const uint32_t currentGeneration : {2u, 3u}) {
                            const uint32_t now = start + age;
                            const bool expected = connected && outbound::commandIsCurrent(
                                2, currentGeneration, now, start, utc, deadline);
                            const auto reason = outbound::classifyCommand(connected, 2,
                                currentGeneration, now, start, utc, deadline);
                            TEST_ASSERT_EQUAL(expected, reason == CommandValidity::CURRENT);
                        }
                    }
                }
            }
        }
    }
}

static void test_labels_are_fixed_and_distinct_for_clock_failures() {
    TEST_ASSERT_EQUAL_STRING("deadline_elapsed",
        outbound::commandValidityLabel(CommandValidity::DEADLINE_ELAPSED));
    TEST_ASSERT_EQUAL_STRING("deadline_too_far",
        outbound::commandValidityLabel(CommandValidity::DEADLINE_TOO_FAR));
    TEST_ASSERT_EQUAL_STRING("clock_unavailable",
        outbound::commandValidityLabel(CommandValidity::CLOCK_UNAVAILABLE));
    TEST_ASSERT_EQUAL_STRING("unknown",
        outbound::commandValidityLabel(static_cast<CommandValidity>(255)));
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_reject_reasons_distinguish_connection_clock_and_age);
    RUN_TEST(test_classification_preserves_existing_admission_at_boundaries);
    RUN_TEST(test_labels_are_fixed_and_distinct_for_clock_failures);
    return UNITY_END();
}
