/**
 * @file test_deep_sleep.cpp
 * @brief Tests the SLEEP protocol with actual ESP32-S3 deep sleep.
 *
 * Two-phase test:
 *   Phase 1 (first boot): pre-sleep check, signal SLEEP, enter deep sleep
 *   Phase 2 (wake):       verify wakeup cause is TIMER
 *
 * Protocol flow:
 *   1. Device runs phase 1, emits "SLEEP: 3000", enters deep sleep
 *   2. Python runner sees SLEEP, closes serial, waits ~8s (3s + 5s padding)
 *   3. Device wakes from timer, reboots, prints READY
 *   4. Runner sends "RUN: *survives deep sleep*"
 *   5. Test re-runs — wakeup cause is TIMER → phase 2 passes → DONE
 *
 * USB-CDC note: The serial port disappears during deep sleep. The runner
 * reopens with dtr=False, rts=False to avoid USB_UART_CHIP_RESET.
 */

#include <doctest.h>
#include <Arduino.h>
#include <esp_sleep.h>
#include <pio_test_runner/test_runner.h>

TEST_SUITE("DeepSleep") {

TEST_CASE("survives deep sleep" * doctest::timeout(30)) {
    auto cause = esp_sleep_get_wakeup_cause();

    if (cause == ESP_SLEEP_WAKEUP_UNDEFINED) {
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

    // Phase 2: woke from timer
    Serial.printf("Phase 2: woke with cause=%d\n", (int)cause);
    CHECK(cause == ESP_SLEEP_WAKEUP_TIMER);
}

}
