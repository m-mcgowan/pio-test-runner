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
 *   #define PTR_READY_TIMEOUT_MS 30000  // Optional: wait up to 30s for host
 *   #include <pio_test_runner/doctest_runner.h>
 *
 *   void setup() { DOCTEST_SETUP(); }
 *   void loop()  { DOCTEST_LOOP(); }
 * @endcode
 */

#pragma once

#include <algorithm>
#include <cstring>
#include <vector>
#include <doctest.h>
#include <Arduino.h>
#include "pio_test_runner/test_runner.h"

#if defined(ESP_IDF_VERSION)
#include <esp_heap_caps.h>
#include <esp_system.h>
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

static bool tests_complete = false;

/**
 * @brief Apply compile-time filter macros to a doctest context.
 */
inline void apply_compile_time_filters(doctest::Context& ctx) {
#ifdef TEST_FILTER_SUITE
    ctx.setOption("test-suite", TEST_FILTER_SUITE);
    Serial.printf("Filtering test suite: %s\n", TEST_FILTER_SUITE);
#endif
#ifdef TEST_FILTER_CASE
    ctx.setOption("test-case", TEST_FILTER_CASE);
    Serial.printf("Filtering test case: %s\n", TEST_FILTER_CASE);
#endif
#ifdef TEST_EXCLUDE_SUITE
    ctx.setOption("test-suite-exclude", TEST_EXCLUDE_SUITE);
    Serial.printf("Excluding test suite: %s\n", TEST_EXCLUDE_SUITE);
#endif
#ifdef TEST_EXCLUDE_CASE
    ctx.setOption("test-case-exclude", TEST_EXCLUDE_CASE);
    Serial.printf("Excluding test case: %s\n", TEST_EXCLUDE_CASE);
#endif
#ifdef TEST_VERBOSE
    ctx.setOption("success", true);
    ctx.setOption("duration", true);
#endif
}

/**
 * @brief Get registered test names in doctest execution order.
 *
 * Iterates doctest's internal test registry and sorts by file/line
 * (matching doctest's default order_by="file" execution order).
 */
inline std::vector<const char*> get_test_names() {
    // Collect pointers to sort — same approach doctest uses internally
    std::vector<const doctest::detail::TestCase*> tests;
    for (const auto& tc : doctest::detail::getRegisteredTests()) {
        tests.push_back(&tc);
    }
    // Sort by file then line (matches doctest's fileOrderComparator)
    std::sort(tests.begin(), tests.end(),
        [](const doctest::detail::TestCase* a, const doctest::detail::TestCase* b) {
            const int res = a->m_file.compare(b->m_file);
            if (res != 0) return res < 0;
            return a->m_line < b->m_line;
        });
    std::vector<const char*> names;
    names.reserve(tests.size());
    for (const auto* tc : tests) {
        names.push_back(tc->m_name);
    }
    return names;
}

/**
 * @brief List all registered tests and signal done without executing.
 */
inline void list_tests() {
    auto names = get_test_names();
    Serial.printf("PTR:LIST count=%u\n", (unsigned)names.size());
    for (size_t i = 0; i < names.size(); ++i) {
        Serial.printf("  [%u] %s\n", (unsigned)i, names[i]);
    }
}

/**
 * @brief Resume tests after the named test.
 *
 * Iterates the test registry to find test_name, then builds a
 * test-case-exclude pattern for all tests up to and including it.
 * The subsequent context.run() will only execute tests after the
 * resume point.
 *
 * @param test_name  Exact name of the last completed test.
 * @return true if the test was found and excludes were applied.
 */
inline bool apply_resume_after(doctest::Context& ctx, const char* test_name) {
    auto names = get_test_names();
    Serial.printf("RESUME_AFTER: \"%s\" (%u tests registered)\n",
                  test_name, (unsigned)names.size());

    String exclude;
    bool found = false;
    for (size_t i = 0; i < names.size(); ++i) {
        if (exclude.length() > 0) {
            exclude += ",";
        }
        exclude += "*";
        exclude += names[i];
        exclude += "*";

        if (strcmp(names[i], test_name) == 0) {
            found = true;
            break;
        }
    }

    if (!found) {
        Serial.printf("WARNING: test \"%s\" not found — running all tests\n",
                       test_name);
        return false;
    }

    unsigned count = 1;
    for (size_t i = 0; i < exclude.length(); ++i) {
        if (exclude[i] == ',') count++;
    }
    Serial.printf("Excluding %u tests before resume point\n", count);
    ctx.setOption("test-case-exclude", exclude.c_str());
    return true;
}

/**
 * @brief Apply a runtime command from the host runner.
 *
 * @return true if tests should be executed, false if the command
 *         was handled without needing a test run (e.g. LIST).
 */
inline bool apply_runner_command(doctest::Context& ctx, const String& command) {
    if (command.startsWith("RUN:")) {
        String filter = command.substring(4);
        filter.trim();
        if (filter.length() > 0) {
            ctx.setOption("test-case", filter.c_str());
            Serial.printf("Runner filter applied: %s\n", filter.c_str());
        }
        return true;
    } else if (command.startsWith("RESUME_AFTER:")) {
        String name = command.substring(13);
        name.trim();
        if (name.length() > 0) {
            apply_resume_after(ctx, name.c_str());
        }
        return true;
    } else if (command == "LIST") {
        list_tests();
        return false;
    } else if (command == "RUN_ALL") {
        Serial.println("Runner: RUN_ALL (no additional filter)");
        return true;
    } else if (command.length() == 0) {
        Serial.println("No runner detected (timeout) — using compiled-in filters");
        return true;
    } else {
        Serial.printf("Unknown runner command: %s — using compiled-in filters\n",
                       command.c_str());
        return true;
    }
}

/**
 * @brief Wait for a host command via the READY/RUN protocol.
 *
 * Sends PTR:READY periodically until the host responds with a command
 * or the timeout expires. Returns the received command (empty on timeout).
 */
inline String wait_for_command(uint32_t timeout_ms) {
    constexpr uint32_t READY_INTERVAL_MS = 1000;
    uint32_t deadline = millis() + timeout_ms;
    uint32_t next_ready = 0;  // send immediately on first iteration
    while (millis() < deadline) {
        if (millis() >= next_ready) {
            pio_test_runner::signal_ready();
            next_ready = millis() + READY_INTERVAL_MS;
        }
        if (Serial.available()) {
            String command = Serial.readStringUntil('\n');
            command.trim();
            if (command.length() > 0) return command;
        }
        delay(10);
    }
    return String();
}

/**
 * @brief Execute one test cycle: apply command, run tests, signal done.
 *
 * Creates a fresh doctest::Context, applies compile-time and runtime
 * filters, executes matching tests, and signals PTR:DONE.
 */
inline void run_cycle(const String& command) {
    doctest::Context context;
    context.setOption("success", true);
    context.setOption("no-exitcode", true);
    apply_compile_time_filters(context);
    context.applyCommandLine(0, nullptr);

    bool should_run = apply_runner_command(context, command);

    if (should_run) {
        try {
            int result = context.run();
            (void)result;
        } catch (...) {
            Serial.println("Exception caught during test execution");
        }
    }

    pio_test_runner::signal_done();
}

/**
 * @brief Initialize and run doctest tests.
 *
 * Call from setup(). Handles serial init, board init, and the first
 * READY/RUN/DONE cycle. After completion, idle_loop() accepts
 * additional commands for RESUME_AFTER cycles.
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

#ifdef PTR_READY_TIMEOUT_MS
    constexpr uint32_t TIMEOUT_MS = PTR_READY_TIMEOUT_MS;
#else
    constexpr uint32_t TIMEOUT_MS = 5000;
#endif

    String command = wait_for_command(TIMEOUT_MS);
    run_cycle(command);
    tests_complete = true;
}

/**
 * @brief Idle loop after tests complete.
 *
 * Call from loop(). After the initial cycle, enters a new READY
 * handshake so the host can send follow-up commands (e.g.
 * RESTART to reboot for RESUME_AFTER, LIST, etc.).
 */
inline void idle_loop() {
    if (!tests_complete) return;

    // Accept follow-up commands
    String command = wait_for_command(5000);
    if (command.length() == 0) {
        Serial.println("Tests complete");
        return;
    }

    if (command == "RESTART") {
        Serial.println("[PTR] Restarting...");
        Serial.flush();
        delay(100);
#if defined(ESP_IDF_VERSION)
        esp_restart();
#endif
    }

    // LIST or other non-run commands can be handled without restart
    run_cycle(command);
}

}  // namespace ptr_doctest

// Convenience macros
#define DOCTEST_SETUP() ptr_doctest::run_tests()
#define DOCTEST_LOOP()  ptr_doctest::idle_loop()
