/**
 * @file test_deep_sleep.cpp
 * @brief Tests the SLEEP protocol with actual ESP32-S3 deep sleep.
 *
 * Two-phase test:
 *   Phase 1 (first boot): pre-sleep check, signal SLEEP, enter deep sleep
 *   Phase 2 (wake):       verify wakeup cause is TIMER
 *
 * Protocol flow:
 *   1. Device runs phase 1, emits "PTR:SLEEP ms=3000 *XX", enters deep sleep
 *   2. Python runner sees PTR:SLEEP, closes serial, waits ~8s (3s + 5s padding)
 *   3. Device wakes from timer, reboots, prints READY
 *   4. Runner sends "RUN: *survives deep sleep*"
 *   5. Test re-runs — wakeup cause is TIMER → phase 2 passes → DONE
 *
 * USB-CDC note: The serial port disappears during deep sleep. The runner
 * reopens with dtr=False, rts=False to avoid USB_UART_CHIP_RESET.
 *
 * RESUME_AFTER flow: Multiple sleep tests exercise the runner's ability to
 * resume remaining tests after a sleep/wake cycle:
 *   1. RUN_ALL → "survives deep sleep" Phase 1 → SLEEP
 *   2. Resume → Phase 2 passes → DONE
 *   3. RESUME_AFTER: "survives deep sleep" → remaining tests run
 *   4. "second sleep test" Phase 1 → SLEEP
 *   5. Resume → Phase 2 passes → DONE
 *   6. RESUME_AFTER: "second sleep test" → "post-sleep test" runs
 */

#include <doctest.h>
#include <Arduino.h>
#include <esp_sleep.h>
#include <pio_test_runner/test_runner.h>

TEST_SUITE("DeepSleep") {

TEST_CASE("survives deep sleep" * doctest::timeout(30)) {
    if (pio_test_runner::is_test_wake()) {
        // Phase 2: woke from timer
        auto cause = esp_sleep_get_wakeup_cause();
        Serial.printf("Phase 2: woke with cause=%d\n", (int)cause);
        CHECK(cause == ESP_SLEEP_WAKEUP_TIMER);
    } else {
        // Phase 1: first boot — pre-sleep check, then sleep
        Serial.println("Phase 1: entering deep sleep for 3s");
        CHECK(true);

        pio_test_runner::signal_sleep(3000);
        Serial.flush();
        delay(100);

        esp_sleep_enable_timer_wakeup(3 * 1000000ULL);
        esp_deep_sleep_start();
        // never reached — device resets
    }
}

TEST_CASE("second sleep test" * doctest::timeout(30)) {
    if (pio_test_runner::is_test_wake()) {
        // Phase 2: verify wake
        auto cause = esp_sleep_get_wakeup_cause();
        Serial.printf("Second sleep: Phase 2 — woke with cause=%d\n", (int)cause);
        CHECK(cause == ESP_SLEEP_WAKEUP_TIMER);
    } else {
        // Phase 1: sleep for 2s
        Serial.println("Second sleep: Phase 1 — entering deep sleep for 2s");
        CHECK(true);

        pio_test_runner::signal_sleep(2000);
        Serial.flush();
        delay(100);

        esp_sleep_enable_timer_wakeup(2 * 1000000ULL);
        esp_deep_sleep_start();
    }
}

TEST_CASE("runs after sleep tests" * doctest::timeout(10)) {
    // This test verifies RESUME_AFTER correctly runs tests after sleep tests.
    // If RESUME_AFTER is broken, this test will never execute.
    Serial.println("Post-sleep test: running after all sleep tests");
    CHECK(true);
}

}
