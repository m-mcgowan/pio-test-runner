/**
 * @file test_timing.cpp
 * @brief Tests that exercise the ETST:CASE:START timing pipeline.
 *
 * PtrTestListener emits ETST:CASE:START markers that the Python
 * TestTimingTracker uses to measure per-test duration. The "slow test"
 * should appear in the slow tests report (>5s threshold).
 */

#include <doctest.h>
#include <Arduino.h>

TEST_SUITE("Timing") {

TEST_CASE("fast test completes quickly") {
    CHECK(true);
}

TEST_CASE("slow test appears in timing report" * doctest::timeout(30)) {
    // 6s delay — should trigger slow test reporting (threshold 5s)
    delay(6000);
    CHECK(true);
}

}
