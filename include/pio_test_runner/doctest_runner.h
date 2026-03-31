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
 * Finds the named test in the registry and uses doctest's "first" option
 * to skip all tests up to and including it. O(1) memory — no exclude string.
 *
 * @param test_name  Exact name of the last completed test.
 * @return Number of tests skipped, or -1 if test_name not found.
 */
inline int apply_resume_after(doctest::Context& ctx, const char* test_name) {
    auto names = get_test_names();
    Serial.printf("RESUME_AFTER: \"%s\" (%u tests registered)\n",
                  test_name, (unsigned)names.size());

    // Find the index of the resume test
    int resume_idx = -1;
    for (size_t i = 0; i < names.size(); ++i) {
        if (strcmp(names[i], test_name) == 0) {
            resume_idx = static_cast<int>(i);
            break;
        }
    }

    if (resume_idx < 0) {
        Serial.printf("WARNING: test \"%s\" not found — running all tests\n",
                       test_name);
        return -1;
    }

    // Use doctest's "first" option to skip to the test after the resume point.
    // This is O(1) memory vs the old approach of building a giant exclude string
    // that could exhaust heap with 700+ test names.
    int skip = resume_idx + 1;
    ctx.setOption("first", skip + 1);  // 1-indexed
    Serial.printf("Skipping %d tests\n", skip);
    return skip;
}

/**
 * @brief Simple wildcard match (supports * and ? globs).
 *
 * Reimplements doctest's wildcmp (which is in an anonymous namespace
 * and not accessible to us).
 */
inline bool glob_match(const char* str, const char* pattern) {
    const char* cp = nullptr;
    const char* mp = nullptr;
    while (*str && *pattern != '*') {
        if (*pattern != *str && *pattern != '?') return false;
        pattern++;
        str++;
    }
    while (*str) {
        if (*pattern == '*') {
            if (!*++pattern) return true;
            mp = pattern;
            cp = str + 1;
        } else if (*pattern == *str || *pattern == '?') {
            pattern++;
            str++;
        } else {
            pattern = mp;
            str = cp++;
        }
    }
    while (*pattern == '*') pattern++;
    return !*pattern;
}

/**
 * @brief Modify m_skip on registered tests matching a pattern.
 *
 * Walks the doctest test registry and sets or clears m_skip on tests
 * whose name or suite matches the given glob pattern. This operates
 * on the test objects themselves, before doctest's filter chain runs.
 *
 * @param pattern  Glob pattern (supports * and ? wildcards).
 * @param match_suite  If true, match against test suite name; if false, test case name.
 * @param skip_value   Value to set m_skip to (false = unskip, true = force-skip).
 * @return Number of tests modified.
 */
inline int modify_skip(const char* pattern, bool match_suite, bool skip_value) {
    int count = 0;
    for (auto& tc : doctest::detail::getRegisteredTests()) {
        const char* name = match_suite ? tc.m_test_suite : tc.m_name;
        if (name && glob_match(name, pattern)) {
            // m_skip is not part of set ordering — safe to modify via const_cast
            const_cast<doctest::detail::TestCase&>(tc).m_skip = skip_value;
            count++;
        }
    }
    return count;
}

/**
 * @brief Extract and apply PTR-specific flags from args, return remaining args.
 *
 * PTR-specific flags modify the test registry (m_skip) and are removed
 * from the argument list before passing to doctest's applyCommandLine.
 *
 * Supported flags:
 *   --unskip-tc <pattern>  Clear m_skip on matching test cases
 *   --unskip-ts <pattern>  Clear m_skip on matching test suites
 *   --skip-tc <pattern>    Set m_skip on matching test cases
 *   --skip-ts <pattern>    Set m_skip on matching test suites
 *
 * @param args  Argument list (modified in place — PTR flags removed).
 */
inline void extract_ptr_flags(std::vector<String>& args) {
    struct { const char* flag; bool match_suite; bool skip_value; } ptr_flags[] = {
        {"--unskip-tc", false, false},
        {"--unskip-ts", true,  false},
        {"--skip-tc",   false, true},
        {"--skip-ts",   true,  true},
    };

    // Process in argument order so later flags override earlier ones.
    // e.g. --skip-tc *foo* --unskip-tc *foo* → foo ends up unskipped.
    for (size_t i = 0; i < args.size(); ) {
        bool matched = false;
        for (auto& pf : ptr_flags) {
            if (args[i] == pf.flag && i + 1 < args.size()) {
                const char* pattern = args[i + 1].c_str();
                int count = modify_skip(pattern, pf.match_suite, pf.skip_value);
                Serial.printf("Runner %s %s: %d test%s modified\n",
                              pf.flag, pattern, count, count == 1 ? "" : "s");
                args.erase(args.begin() + i, args.begin() + i + 2);
                matched = true;
                break;
            }
        }
        if (!matched) i++;
    }
}

/**
 * @brief Tokenize a command string into argv-style arguments.
 *
 * Splits on whitespace. Handles quoted strings (single or double quotes)
 * so patterns like --tc "foo bar" work correctly.
 */
inline std::vector<String> tokenize_args(const String& body) {
    std::vector<String> args;
    size_t i = 0;
    while (i < body.length()) {
        // Skip whitespace
        while (i < body.length() && body[i] == ' ') i++;
        if (i >= body.length()) break;

        String arg;
        if (body[i] == '"' || body[i] == '\'') {
            // Quoted argument
            char quote = body[i++];
            while (i < body.length() && body[i] != quote) {
                arg += body[i++];
            }
            if (i < body.length()) i++;  // skip closing quote
        } else {
            // Unquoted argument
            while (i < body.length() && body[i] != ' ') {
                arg += body[i++];
            }
        }
        if (arg.length() > 0) {
            args.push_back(arg);
        }
    }
    return args;
}

/**
 * @brief Parse and apply filter flags from a RUN: command body.
 *
 * Two-phase processing:
 * 1. Extract PTR-specific flags (--unskip-tc, --skip-tc, etc.) and
 *    apply them to the test registry (modify m_skip).
 * 2. Pass remaining flags to doctest's applyCommandLine(), which
 *    handles all native flags: --tc, --ts, --tce, --tse, --no-skip,
 *    comma-separated patterns, multiple instances, etc.
 *
 * If no flags are present, the body is treated as a bare test-case
 * pattern for backwards compatibility (e.g. "RUN: *foo*").
 */
inline void apply_run_filters(doctest::Context& ctx, const String& body) {
    auto args = tokenize_args(body);

    // Check for bare pattern (no -- flags) for backwards compatibility
    if (args.size() == 1 && !args[0].startsWith("--")) {
        ctx.setOption("test-case", args[0].c_str());
        Serial.printf("Runner filter applied: %s\n", args[0].c_str());
        return;
    }

    // Phase 1: extract and apply PTR-specific flags (modifies registry)
    extract_ptr_flags(args);

    // Phase 2: build argc/argv and pass to doctest's native parser
    if (!args.empty()) {
        // Build argv array with a dummy program name at index 0
        std::vector<const char*> argv;
        argv.push_back("test");  // argv[0] = program name
        for (auto& a : args) {
            argv.push_back(a.c_str());
        }
        ctx.applyCommandLine(static_cast<int>(argv.size()), argv.data());
    }
}

/**
 * @brief Apply a runtime command from the host runner.
 *
 * @return true if tests should be executed, false if the command
 *         was handled without needing a test run (e.g. LIST).
 */
struct RunCommand {
    bool should_run = false;
    int skip_count = 0;  // tests skipped by RESUME_AFTER
};

inline RunCommand apply_runner_command(doctest::Context& ctx, const String& command) {
    if (command.startsWith("RUN:")) {
        String filter = command.substring(4);
        filter.trim();
        if (filter.length() > 0) {
            apply_run_filters(ctx, filter);
        }
        return {true, 0};
    } else if (command.startsWith("RESUME_AFTER:")) {
        String rest = command.substring(13);
        rest.trim();
        // Format: "RESUME_AFTER: <test_name> [--tc ... --ts ...]"
        // Split name from optional trailing filters at first "--"
        String name = rest;
        String filters;
        int dash_pos = rest.indexOf(" --");
        if (dash_pos >= 0) {
            name = rest.substring(0, dash_pos);
            filters = rest.substring(dash_pos + 1);
            name.trim();
            filters.trim();
        }
        int skipped = 0;
        if (name.length() > 0) {
            skipped = apply_resume_after(ctx, name.c_str());
            if (skipped < 0) skipped = 0;
        }
        if (filters.length() > 0) {
            apply_run_filters(ctx, filters);
        }
        return {true, skipped};
    } else if (command == "LIST") {
        list_tests();
        return {false, 0};
    } else if (command == "RUN_ALL") {
        Serial.println("Runner: RUN_ALL (no additional filter)");
        return {true, 0};
    } else if (command.length() == 0) {
        Serial.println("No runner detected (timeout) — using compiled-in filters");
        return {true, 0};
    } else {
        Serial.printf("Unknown runner command: %s — using compiled-in filters\n",
                       command.c_str());
        return {true, 0};
    }
}

/**
 * @brief Wait for a host command via the READY/RUN protocol.
 *
 * Sends PTR:READY periodically until the host responds with a CRC-valid
 * command or the timeout expires. Returns the command (CRC stripped),
 * or empty string on timeout. Discards any input that fails CRC
 * validation (e.g. garbage from macOS DTR assertion on serial open).
 */
inline String wait_for_command(uint32_t timeout_ms) {
    constexpr uint32_t READY_INTERVAL_MS = 1000;
    const bool wait_forever = (timeout_ms == 0);
    uint32_t deadline = millis() + timeout_ms;
    uint32_t next_ready = 0;  // send immediately on first iteration
    while (wait_forever || millis() < deadline) {
        if (millis() >= next_ready) {
            pio_test_runner::signal_ready();
            next_ready = millis() + READY_INTERVAL_MS;
        }
        if (Serial.available()) {
            String raw = Serial.readStringUntil('\n');
            raw.trim();
            if (raw.length() > 0) {
                // Strip leading garbage bytes from serial open (macOS DTR
                // assertion injects stray bytes into USB-CDC RX). Protocol
                // commands always start with an uppercase ASCII letter.
                size_t start = 0;
                while (start < raw.length() && !(raw[start] >= 'A' && raw[start] <= 'Z')) {
                    start++;
                }
                if (start > 0 && start < raw.length()) {
                    raw = raw.substring(start);
                }

                // Validate CRC at the transport layer
                char buf[pio_test_runner::MAX_LINE_LEN];
                size_t len = raw.length();
                if (len >= sizeof(buf)) len = sizeof(buf) - 1;
                memcpy(buf, raw.c_str(), len);
                buf[len] = '\0';

                auto result = pio_test_runner::validate_crc(buf, len);
                if (result.valid) {
                    return String(result.content);
                }
                // CRC failed — log and discard
                pio_test_runner::log_crc_failure(Serial, raw.c_str(), raw.length());
            }
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

    auto cmd = apply_runner_command(context, command);

    // Project-specific context configuration hook.
    // Called after all filters are applied, before tests run.
    // Use for runtime-derived excludes (e.g. firmware version gating).
#ifdef PTR_CONFIGURE_CONTEXT
    PTR_CONFIGURE_CONTEXT(context);
#endif

    if (cmd.should_run) {
        unsigned total = static_cast<unsigned>(get_test_names().size());
        unsigned skip = static_cast<unsigned>(cmd.skip_count);
        pio_test_runner::print_test_count(total, skip, total - skip);

        try {
            int result = context.run();
            (void)result;
        } catch (...) {
            Serial.println("Exception caught during test execution");
        }
    }

    // Hook for post-cycle actions (e.g. coverage dump)
#ifdef PTR_AFTER_CYCLE
    PTR_AFTER_CYCLE();
#endif

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
    constexpr uint32_t TIMEOUT_MS = 0;  // Wait forever for runner
#endif

    String command = wait_for_command(TIMEOUT_MS);
    run_cycle(command);
    tests_complete = true;
}

/**
 * @brief Idle loop after tests complete.
 *
 * Call from loop(). After the initial cycle, blocks indefinitely
 * waiting for host commands (RESTART, SLEEP, LIST, etc.).
 * Never returns — prevents unintended test re-runs and battery drain.
 */
inline void idle_loop() {
    if (!tests_complete) return;

    // Block forever waiting for host commands.
    // This prevents the device from draining battery or re-running
    // tests if USB causes a reset while no host is connected.
    // wait_for_command sends PTR:READY periodically so the runner
    // knows the device is accepting commands.
    while (true) {
        String command = wait_for_command(0);  // 0 = wait forever

        if (command.length() == 0) {
            // Should not happen with infinite wait, but be safe
            delay(1000);
            continue;
        }

        if (command == "RESTART") {
            Serial.println("[PTR] Restarting...");
            Serial.flush();
            delay(100);
#if defined(ESP_IDF_VERSION)
            esp_restart();
#endif
        } else if (command == "SLEEP") {
            Serial.println("[PTR] Entering deep sleep...");
            Serial.flush();
            delay(100);
#if defined(ESP_IDF_VERSION)
            esp_deep_sleep_start();
#endif
        } else {
            // LIST, RUN:, or other commands
            run_cycle(command);
        }
    }
}

}  // namespace ptr_doctest

// Convenience macros
#define DOCTEST_SETUP() ptr_doctest::run_tests()
#define DOCTEST_LOOP()  ptr_doctest::idle_loop()
