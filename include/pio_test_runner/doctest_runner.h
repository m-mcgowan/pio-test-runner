/**
 * @file doctest_runner.h
 * @brief Ready-to-use doctest test runner for Arduino/ESP32 with
 *        pio-test-runner protocol integration.
 *
 * Provides:
 *   - PtrTestListener: doctest reporter that emits PTR:TEST:START
 *     and PTR:MEM:* markers (parsed by the Python host)
 *   - READY/RUN/DONE protocol handshake with the host
 *   - Compile-time filter support via TEST_FILTER_* macros
 *   - Runtime filter override from the host runner
 *   - Idle loop for post-test serial keep-alive
 *
 * Quick start — in your test's main.cpp:
 * @code
 *   #define DOCTEST_CONFIG_IMPLEMENT
 *   #include <doctest.h>
 *   #include <pio_test_runner/doctest_runner.h>
 *
 *   void setup() { DOCTEST_SETUP(); }
 *   void loop()  { DOCTEST_LOOP(); }
 * @endcode
 *
 * For project-specific initialization (board setup, storage, etc.),
 * provide callbacks before including this header:
 * @code
 *   #define DOCTEST_CONFIG_IMPLEMENT
 *   #include <doctest.h>
 *
 *   // Optional: called after Serial.begin(), before doctest runs
 *   static bool my_board_init(Print& log) {
 *       // board pin setup, storage mount, etc.
 *       return true;  // false halts
 *   }
 *
 *   #define PTR_BOARD_INIT my_board_init
 *   #include <pio_test_runner/doctest_runner.h>
 *
 *   void setup() { DOCTEST_SETUP(); }
 *   void loop()  { DOCTEST_LOOP(); }
 * @endcode
 */

#pragma once

#include <doctest.h>
#include <Arduino.h>
#include "pio_test_runner/test_runner.h"

#if defined(ESP_IDF_VERSION)
#include <esp_heap_caps.h>
#endif

// =========================================================================
// Test Listener — emits markers parsed by the Python host
// =========================================================================

/**
 * @brief Doctest reporter that prints test names and memory stats.
 *
 * Emits markers consumed by the pio-test-runner Python host:
 *   - ``PTR:TEST:START suite=... name=...`` — test timing (TestTimingTracker)
 *   - ``PTR:MEM:BEFORE/AFTER`` — heap tracking (MemoryTracker)
 */
struct PtrTestListener : doctest::IReporter {
    size_t free_before_{0};
    size_t min_before_{0};

    PtrTestListener(const doctest::ContextOptions&) {}

    void report_query(const doctest::QueryData&) override {}
    void test_run_start() override {}
    void test_run_end(const doctest::TestRunStats&) override {}
    void test_case_reenter(const doctest::TestCaseData&) override {}
    void test_case_exception(const doctest::TestCaseException&) override {}
    void subcase_start(const doctest::SubcaseSignature&) override {}
    void subcase_end() override {}
    void log_assert(const doctest::AssertData&) override {}
    void log_message(const doctest::MessageData&) override {}
    void test_case_skipped(const doctest::TestCaseData&) override {}

    void test_case_start(const doctest::TestCaseData& tc) override {
#if defined(ESP_IDF_VERSION)
        free_before_ = esp_get_free_heap_size();
        min_before_ = esp_get_minimum_free_heap_size();
#endif
        if (tc.m_timeout > 0) {
            pio_test_runner::print_test_start(tc.m_test_suite, tc.m_name, tc.m_timeout);
        } else {
            pio_test_runner::print_test_start(tc.m_test_suite, tc.m_name);
        }
#if defined(ESP_IDF_VERSION)
        pio_test_runner::print_mem_before(free_before_, min_before_);
#endif
    }

    void test_case_end(const doctest::CurrentTestCaseStats& stats) override {
#if defined(ESP_IDF_VERSION)
        size_t free_after = esp_get_free_heap_size();
        size_t min_after = esp_get_minimum_free_heap_size();
        int64_t delta = static_cast<int64_t>(free_after) - static_cast<int64_t>(free_before_);
        pio_test_runner::print_mem_after(free_after, delta, min_after);
        if (delta < -10000) {
            pio_test_runner::print_mem_warning(-delta);
        }
#endif
        (void)stats;
    }
};

REGISTER_LISTENER("ptr_test_listener", 1, PtrTestListener);

// =========================================================================
// Doctest runner with protocol integration
// =========================================================================

namespace ptr_doctest {

static doctest::Context context;
static bool tests_complete = false;

/**
 * @brief Apply compile-time filter macros to the doctest context.
 *
 * Define these macros before including this header (typically via
 * a generated test_runner_config.h):
 *   - TEST_FILTER_SUITE    — doctest -ts=<value>
 *   - TEST_FILTER_CASE     — doctest -tc=<value>
 *   - TEST_EXCLUDE_SUITE   — doctest -tse=<value>
 *   - TEST_EXCLUDE_CASE    — doctest -tce=<value>
 *   - TEST_VERBOSE         — enable success + duration reporting
 */
inline void apply_compile_time_filters() {
#ifdef TEST_FILTER_SUITE
    context.setOption("test-suite", TEST_FILTER_SUITE);
    Serial.printf("Filtering test suite: %s\n", TEST_FILTER_SUITE);
#endif
#ifdef TEST_FILTER_CASE
    context.setOption("test-case", TEST_FILTER_CASE);
    Serial.printf("Filtering test case: %s\n", TEST_FILTER_CASE);
#endif
#ifdef TEST_EXCLUDE_SUITE
    context.setOption("test-suite-exclude", TEST_EXCLUDE_SUITE);
    Serial.printf("Excluding test suite: %s\n", TEST_EXCLUDE_SUITE);
#endif
#ifdef TEST_EXCLUDE_CASE
    context.setOption("test-case-exclude", TEST_EXCLUDE_CASE);
    Serial.printf("Excluding test case: %s\n", TEST_EXCLUDE_CASE);
#endif
#ifdef TEST_VERBOSE
    context.setOption("success", true);
    context.setOption("duration", true);
#endif
}

/**
 * @brief Apply a runtime command from the host runner.
 *
 * Handles RUN_ALL, RUN:<filter>, and timeout (no runner present).
 */
inline void apply_runner_command(const String& command) {
    if (command.startsWith("RUN:")) {
        String filter = command.substring(4);
        filter.trim();
        if (filter.length() > 0) {
            context.setOption("test-case", filter.c_str());
            Serial.printf("Runner filter applied: %s\n", filter.c_str());
        }
    } else if (command == "RUN_ALL") {
        Serial.println("Runner: RUN_ALL (no additional filter)");
    } else if (command.length() == 0) {
        Serial.println("No runner detected (timeout) -- using compiled-in filters");
    } else {
        Serial.printf("Unknown runner command: %s -- using compiled-in filters\n",
                       command.c_str());
    }
}

/**
 * @brief Initialize and run doctest tests.
 *
 * Call from setup(). This function:
 *   1. Initializes serial
 *   2. Calls PTR_BOARD_INIT if defined (project-specific setup)
 *   3. Configures doctest context with filters
 *   4. Runs the READY/RUN/DONE protocol with the host
 *   5. Executes all matching tests
 *   6. Signals completion
 *
 * @note Define PTR_BOARD_INIT as a function with signature
 *       ``bool init(Print& log)`` for project-specific initialization.
 *       Return false to halt (e.g. board detection failure).
 */
inline void run_tests() {
    Serial.begin(115200);

    // Delay for serial connection — shorter for IDF USB-CDC
#if defined(ESP_IDF_VERSION) && !defined(ARDUINO_USB_MODE)
    delay(500);
#else
    delay(4000);
#endif

    // Project-specific initialization
#ifdef PTR_BOARD_INIT
    if (!PTR_BOARD_INIT(Serial)) {
        Serial.println("FATAL: Board init failed - halting tests");
        while (true) { delay(1000); }
    }
#endif

    // PlatformIO required options
    context.setOption("success", true);
    context.setOption("no-exitcode", true);

    // Apply compile-time filters
    apply_compile_time_filters();

    context.applyCommandLine(0, nullptr);

    // Protocol handshake: repeat READY until host responds or timeout.
    // Repeating ensures the host sees READY even if it opens the port
    // after the first signal (e.g. reconnecting after deep sleep).
    String command;
    {
        constexpr uint32_t TOTAL_TIMEOUT_MS = 5000;
        constexpr uint32_t READY_INTERVAL_MS = 1000;
        uint32_t deadline = millis() + TOTAL_TIMEOUT_MS;
        uint32_t next_ready = 0;  // send immediately on first iteration
        while (millis() < deadline) {
            if (millis() >= next_ready) {
                pio_test_runner::signal_ready();
                next_ready = millis() + READY_INTERVAL_MS;
            }
            if (Serial.available()) {
                command = Serial.readStringUntil('\n');
                command.trim();
                if (command.length() > 0) break;
            }
            delay(10);
        }
    }
    apply_runner_command(command);

    // Run tests
    try {
        int result = context.run();
        (void)result;
    } catch (...) {
        Serial.println("Exception caught during test execution");
    }

    pio_test_runner::signal_done();
    tests_complete = true;
}

/**
 * @brief Idle loop after tests complete.
 *
 * Call from loop(). Prints "Tests complete" periodically so the
 * host knows the device is still alive.
 */
inline void idle_loop() {
    if (tests_complete) {
        Serial.println("Tests complete");
        delay(1000);
    }
}

}  // namespace ptr_doctest

// Convenience macros
#define DOCTEST_SETUP() ptr_doctest::run_tests()
#define DOCTEST_LOOP()  ptr_doctest::idle_loop()
