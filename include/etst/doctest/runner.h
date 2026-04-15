/**
 * @file runner.h
 * @brief Ready-to-use doctest test runner for Arduino/ESP32 with
 *        embedded-test-runner (etst) protocol integration.
 *
 * Provides:
 *   - EtstDoctestListener: doctest reporter that emits ETST:CASE:START
 *     and ETST:MEM:* markers (parsed by the Python host)
 *   - READY/RUN/DONE protocol handshake with the host
 *   - Compile-time filter support via TEST_FILTER_* macros
 *   - Runtime filter override from the host runner
 *   - Idle loop for post-test serial keep-alive
 *
 * Quick start — in your test's main.cpp:
 * @code
 *   #define DOCTEST_CONFIG_IMPLEMENT
 *   #include <doctest.h>
 *   #include <etst/doctest/runner.h>
 *
 *   void setup() { DOCTEST_SETUP(); }
 *   void loop()  { DOCTEST_LOOP(); }
 * @endcode
 *
 * For project-specific initialization (board setup, storage, etc.),
 * set callbacks on etst::config before DOCTEST_SETUP():
 * @code
 *   #define DOCTEST_CONFIG_IMPLEMENT
 *   #include <doctest.h>
 *   #include <etst/doctest/runner.h>
 *
 *   static bool my_board_init(Print& log) {
 *       // board pin setup, storage mount, etc.
 *       return true;  // false halts
 *   }
 *   static void my_cleanup() { gcov_serial_dump(); }
 *
 *   void setup() {
 *       etst::config.board_init = my_board_init;
 *       etst::config.after_cycle = my_cleanup;
 *       DOCTEST_SETUP();
 *   }
 *   void loop() { DOCTEST_LOOP(); }
 * @endcode
 */

#pragma once

#include <algorithm>
#include <cstring>
#include <vector>
#include <doctest.h>
#include <Arduino.h>
#include "etst/test_runner.h"
#include "etst/env.h"

// _etst_is_wake_cycle is declared in test_runner.h, defined here.
// inline variable (C++17) — safe in header-only library.
namespace etst { inline bool _etst_is_wake_cycle = false; }

#if defined(ESP_IDF_VERSION)
#include <esp_heap_caps.h>
#include <esp_system.h>
#endif

// =========================================================================
// Framework-agnostic configuration (namespace etst)
// =========================================================================

namespace etst {

/**
 * @brief Framework-agnostic runtime configuration.
 *
 * Set fields before calling DOCTEST_SETUP() (or the equivalent
 * framework entry point).
 *
 * @code
 *   etst::config.board_init = my_init;
 *   etst::config.after_cycle = my_cleanup;
 *   etst::config.ready_timeout_ms = 5000;
 * @endcode
 */
struct Config {
    /// Called after Serial.begin(), before tests run.
    /// Return false to halt (e.g. board detection failure).
    bool (*board_init)(Print& log) = nullptr;

    /// Called after each test cycle completes (after ETST:DONE).
    /// Use for coverage dumps, cleanup, etc.
    void (*after_cycle)() = nullptr;

    /// Platform-specific restart. Default: esp_restart() on ESP-IDF.
    void (*platform_restart)() = nullptr;

    /// Platform-specific deep sleep. Default: esp_deep_sleep_start() on ESP-IDF.
    void (*platform_sleep)() = nullptr;

    /// Platform-specific light sleep. Default: esp_light_sleep_start() on ESP-IDF.
    void (*platform_lightsleep)() = nullptr;

    /// How long to wait for the host runner before running standalone.
    /// 0 = wait forever (default).
    uint32_t ready_timeout_ms = 0;
};

/// Global configuration. Set fields before calling DOCTEST_SETUP().
inline Config& config = *[]() { static Config c; return &c; }();

}  // namespace etst

// =========================================================================
// Test Listener — emits markers parsed by the Python host
// =========================================================================

/**
 * @brief Doctest reporter that prints test names and memory stats.
 *
 * Emits markers consumed by the embedded-test-runner Python host:
 *   - ``ETST:CASE:START suite=... name=...`` — test timing (TestTimingTracker)
 *   - ``ETST:MEM:BEFORE/AFTER`` — heap tracking (MemoryTracker)
 */
struct EtstDoctestListener : doctest::IReporter {
    size_t free_before_{0};
    size_t min_before_{0};

    EtstDoctestListener(const doctest::ContextOptions&) {}

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
            etst::print_test_start(tc.m_test_suite, tc.m_name, tc.m_timeout);
        } else {
            etst::print_test_start(tc.m_test_suite, tc.m_name);
        }
#if defined(ESP_IDF_VERSION)
        etst::print_mem_before(free_before_, min_before_);
#endif
    }

    void test_case_end(const doctest::CurrentTestCaseStats& stats) override {
#if defined(ESP_IDF_VERSION)
        size_t free_after = esp_get_free_heap_size();
        size_t min_after = esp_get_minimum_free_heap_size();
        int64_t delta = static_cast<int64_t>(free_after) - static_cast<int64_t>(free_before_);
        etst::print_mem_after(free_after, delta, min_after);
        if (delta < -10000) {
            etst::print_mem_warning(-delta);
        }
#endif
        (void)stats;
    }
};

REGISTER_LISTENER("etst_doctest_listener", 1, EtstDoctestListener);

// =========================================================================
// Doctest runner with protocol integration
// =========================================================================

namespace etst::doctest {

// =========================================================================
// Doctest-specific configuration
// =========================================================================

/**
 * @brief Doctest-specific runtime configuration.
 *
 * @code
 *   etst::doctest::config.configure = [](::doctest::Context& ctx) {
 *       ctx.setOption("test-suite-exclude", "slow*");
 *   };
 * @endcode
 */
struct Config {
    /// Called after all filters applied, before context.run().
    /// Use for runtime-derived excludes (e.g. firmware version gating).
    void (*configure)(::doctest::Context& ctx) = nullptr;
};

/// Doctest-specific global configuration.
inline Config& config = *[]() { static Config c; return &c; }();

static bool tests_complete = false;

/**
 * @brief Apply compile-time filter macros to a doctest context.
 */
inline void apply_compile_time_filters(::doctest::Context& ctx) {
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
    std::vector<const ::doctest::detail::TestCase*> tests;
    for (const auto& tc : ::doctest::detail::getRegisteredTests()) {
        tests.push_back(&tc);
    }
    // Sort by file then line (matches doctest's fileOrderComparator)
    std::sort(tests.begin(), tests.end(),
        [](const ::doctest::detail::TestCase* a, const ::doctest::detail::TestCase* b) {
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
    Serial.printf("ETST:LIST count=%u\n", (unsigned)names.size());
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
inline int apply_resume_after(::doctest::Context& ctx, const char* test_name) {
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
    for (auto& tc : ::doctest::detail::getRegisteredTests()) {
        const char* name = match_suite ? tc.m_test_suite : tc.m_name;
        if (name && glob_match(name, pattern)) {
            // m_skip is not part of set ordering — safe to modify via const_cast
            const_cast<::doctest::detail::TestCase&>(tc).m_skip = skip_value;
            count++;
        }
    }
    return count;
}

/**
 * @brief Extract and apply ETST-specific flags from args, return remaining args.
 *
 * ETST-specific flags modify the test registry (m_skip) and are removed
 * from the argument list before passing to doctest's applyCommandLine.
 *
 * Supported flags:
 *   --unskip-tc <pattern>  Clear m_skip on matching test cases
 *   --unskip-ts <pattern>  Clear m_skip on matching test suites
 *   --skip-tc <pattern>    Set m_skip on matching test cases
 *   --skip-ts <pattern>    Set m_skip on matching test suites
 *
 * @param args  Argument list (modified in place — ETST flags removed).
 */
inline void extract_etst_flags(std::vector<String>& args) {
    struct { const char* flag; bool match_suite; bool skip_value; } etst_flags[] = {
        {"--unskip-tc", false, false},
        {"--unskip-ts", true,  false},
        {"--skip-tc",   false, true},
        {"--skip-ts",   true,  true},
    };

    // Process in argument order so later flags override earlier ones.
    // e.g. --skip-tc *foo* --unskip-tc *foo* → foo ends up unskipped.
    for (size_t i = 0; i < args.size(); ) {
        // --env KEY=VALUE: store in env var store
        if (args[i] == "--env" && i + 1 < args.size()) {
            const char* kv = args[i + 1].c_str();
            const char* eq = strchr(kv, '=');
            if (eq && eq != kv) {
                String key(kv, eq - kv);
                String value(eq + 1);
                etst::detail::env_set(key.c_str(), value.c_str());
            } else {
                String msg = "malformed --env arg: ";
                msg += args[i + 1];
                etst::signal_error("config", msg.c_str());
                args.clear();
                return;
            }
            args.erase(args.begin() + i, args.begin() + i + 2);
            continue;
        }
        // --wake: Phase 2 after deep sleep (no value, just a flag)
        if (args[i] == "--wake") {
            etst::_etst_is_wake_cycle = true;
            args.erase(args.begin() + i);
            continue;
        }
        bool matched = false;
        for (auto& ef : etst_flags) {
            if (args[i] == ef.flag && i + 1 < args.size()) {
                const char* pattern = args[i + 1].c_str();
                int count = modify_skip(pattern, ef.match_suite, ef.skip_value);
                Serial.printf("Runner %s %s: %d test%s modified\n",
                              ef.flag, pattern, count, count == 1 ? "" : "s");
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
 * @brief Build argv for doctest's applyCommandLine().
 *
 * doctest expects --flag=value (joined with =), not --flag value
 * (separate entries). This function joins adjacent flag+value pairs
 * and prepends a dummy program name at argv[0].
 *
 * Examples:
 *   ["--tc", "*foo*"]           -> ["test", "--tc=*foo*"]
 *   ["--no-skip"]               -> ["test", "--no-skip"]
 *   ["--tc", "*a*", "--ts", "*b*"] -> ["test", "--tc=*a*", "--ts=*b*"]
 *   ["--tc=*foo*"]              -> ["test", "--tc=*foo*"]  (already joined)
 */
inline std::vector<String> build_doctest_argv(const std::vector<String>& args) {
    std::vector<String> result;
    result.push_back("test");  // argv[0] = program name
    for (size_t i = 0; i < args.size(); i++) {
        if (args[i].startsWith("--") && i + 1 < args.size()
                && !args[i + 1].startsWith("--")) {
            // Join --flag value -> --flag=value
            String combined;
            combined += args[i].c_str();
            combined += "=";
            combined += args[i + 1].c_str();
            result.push_back(combined);
            i++;  // skip the value
        } else {
            // Standalone flag (e.g. --no-skip) or already joined (--tc=*foo*)
            result.push_back(args[i]);
        }
    }
    return result;
}

/**
 * @brief Check if a name matches any pattern in a filter list.
 *
 * Mirrors doctest's internal matchesAny() (anonymous namespace, not
 * accessible to us). Used to compute accurate test counts before run().
 */
inline bool matches_any(const char* name, const std::vector<String>& filters,
                        bool match_empty) {
    if (filters.empty() && match_empty) return true;
    if (!name) name = "";  // tests not in a suite have nullptr
    for (auto& f : filters) {
        if (glob_match(name, f.c_str())) return true;
    }
    return false;
}

/// Captured filter state from apply_run_filters(), used by count_passing_filters().
struct FilterState {
    std::vector<String> ts;    // test-suite include
    std::vector<String> tse;   // test-suite exclude
    std::vector<String> tc;    // test-case include
    std::vector<String> tce;   // test-case exclude
    bool no_skip = false;

    void clear() { ts.clear(); tse.clear(); tc.clear(); tce.clear(); no_skip = false; }
};
inline FilterState& active_filters() {
    static FilterState state;
    return state;
}

/**
 * @brief Count tests that will pass the current filter configuration.
 *
 * Replicates doctest's filter logic using the filters captured during
 * apply_run_filters(). Call after apply_runner_command().
 */
inline unsigned count_passing_filters() {
    auto& f = active_filters();
    unsigned count = 0;
    for (auto& tc : ::doctest::detail::getRegisteredTests()) {
        bool skip = false;
        if (tc.m_skip && !f.no_skip) skip = true;
        if (!matches_any(tc.m_test_suite, f.ts, true)) skip = true;
        if (matches_any(tc.m_test_suite, f.tse, false)) skip = true;
        if (!matches_any(tc.m_name, f.tc, true)) skip = true;
        if (matches_any(tc.m_name, f.tce, false)) skip = true;
        if (!skip) count++;
    }
    return count;
}

/**
 * @brief Parse and apply filter flags from a RUN: command body.
 *
 * Two-phase processing:
 * 1. Extract ETST-specific flags (--unskip-tc, --skip-tc, etc.) and
 *    apply them to the test registry (modify m_skip).
 * 2. Pass remaining flags to doctest's applyCommandLine(), which
 *    handles all native flags: --tc, --ts, --tce, --tse, --no-skip,
 *    comma-separated patterns, multiple instances, etc.
 *
 * If no flags are present, the body is treated as a bare test-case
 * pattern for backwards compatibility (e.g. "RUN: *foo*").
 */
inline void apply_run_filters(::doctest::Context& ctx, const String& body) {
    auto args = tokenize_args(body);
    auto& filters = active_filters();

    // Check for bare pattern (no -- flags) for backwards compatibility
    if (args.size() == 1 && !args[0].startsWith("--")) {
        ctx.setOption("test-case", args[0].c_str());
        filters.tc.push_back(args[0]);
        Serial.printf("Runner filter applied: %s\n", args[0].c_str());
        return;
    }

    // Phase 1: extract and apply ETST-specific flags (modifies registry)
    extract_etst_flags(args);

    // Phase 2: pass remaining flags to doctest's native parser.
    if (!args.empty()) {
        auto argv = build_doctest_argv(args);
        // Capture filter values for count_passing_filters().
        // argv entries are "--flag=value" after build_doctest_argv.
        for (auto& a : argv) {
            // Parse comma-separated values (doctest native feature)
            auto capture = [](const String& a, const char* prefix, std::vector<String>& out) {
                if (!a.startsWith(prefix)) return false;
                String val = a.substring(strlen(prefix));
                // Split comma-separated patterns
                int start = 0;
                for (int i = 0; i <= (int)val.length(); i++) {
                    if (i == (int)val.length() || val[i] == ',') {
                        if (i > start) out.push_back(val.substring(start, i));
                        start = i + 1;
                    }
                }
                return true;
            };
            capture(a, "--tc=", filters.tc) ||
            capture(a, "--test-case=", filters.tc) ||
            capture(a, "--ts=", filters.ts) ||
            capture(a, "--test-suite=", filters.ts) ||
            capture(a, "--tce=", filters.tce) ||
            capture(a, "--test-case-exclude=", filters.tce) ||
            capture(a, "--tse=", filters.tse) ||
            capture(a, "--test-suite-exclude=", filters.tse);
            if (a == "--no-skip") filters.no_skip = true;
        }

        std::vector<const char*> argv_ptrs;
        for (auto& a : argv) {
            argv_ptrs.push_back(a.c_str());
        }
        ctx.applyCommandLine(static_cast<int>(argv_ptrs.size()), argv_ptrs.data());
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

inline RunCommand apply_runner_command(::doctest::Context& ctx, const String& command) {
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

/// Result of waiting for host commands.
struct CommandResult {
    String command;                   // The RUN/RUN_ALL/RESUME_AFTER command
    std::vector<String> args;         // Accumulated ETST:ARGS payloads
};

/**
 * @brief Wait for a host command via the READY/RUN protocol.
 *
 * Sends ETST:READY periodically until the host responds with a CRC-valid
 * command or the timeout expires. Returns the command (CRC stripped),
 * or empty string on timeout. Discards any input that fails CRC
 * validation (e.g. garbage from macOS DTR assertion on serial open).
 *
 * Accumulates ETST:ARGS lines received during the configure phase.
 * On each READY emission, clears accumulated args and the env store.
 */
inline CommandResult wait_for_command(uint32_t timeout_ms) {
    constexpr uint32_t READY_INTERVAL_MS = 1000;
    const bool wait_forever = (timeout_ms == 0);
    uint32_t deadline = millis() + timeout_ms;
    uint32_t next_ready = 0;  // send immediately on first iteration
    CommandResult cmd_result;
    while (wait_forever || millis() < deadline) {
        if (millis() >= next_ready) {
            cmd_result.args.clear();
            etst::detail::env_clear();
            etst::signal_ready();
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
                char buf[etst::MAX_LINE_LEN];
                size_t len = raw.length();
                if (len >= sizeof(buf)) len = sizeof(buf) - 1;
                memcpy(buf, raw.c_str(), len);
                buf[len] = '\0';

                auto result = etst::validate_crc(buf, len);
                if (result.valid) {
                    String content(result.content);
                    // ETST:ARGS lines are accumulated, not returned as commands
                    if (content.startsWith("ETST:ARGS ")) {
                        String payload = content.substring(10);
                        payload.trim();
                        cmd_result.args.push_back(payload);
                        continue;
                    }
                    cmd_result.command = content;
                    return cmd_result;
                }
                // CRC failed — log, discard, and force immediate READY re-send
                etst::log_crc_failure(Serial, raw.c_str(), raw.length());
                cmd_result.args.clear();
                etst::detail::env_clear();
                next_ready = 0;  // force immediate READY re-send
            }
        }
        delay(10);
    }
    return cmd_result;
}

/**
 * @brief Execute one test cycle: apply command, run tests, signal done.
 *
 * Creates a fresh doctest::Context, applies compile-time and runtime
 * filters, executes matching tests, and signals ETST:DONE.
 *
 * Accepts a CommandResult which may contain accumulated ETST:ARGS
 * payloads from the configure phase. These are combined with any
 * inline args from the RUN command before applying filters.
 */
inline void run_cycle(const CommandResult& cmd_result) {
    ::doctest::Context context;
    context.setOption("success", true);
    context.setOption("no-exitcode", true);
    apply_compile_time_filters(context);
    context.applyCommandLine(0, nullptr);

    active_filters().clear();

    // Build combined command: accumulated ARGS + RUN body
    String command = cmd_result.command;
    if (!cmd_result.args.empty()) {
        String combined_body;
        for (const auto& arg : cmd_result.args) {
            if (combined_body.length() > 0) combined_body += " ";
            combined_body += arg;
        }
        if (command.startsWith("RUN:")) {
            String inline_args = command.substring(4);
            inline_args.trim();
            if (inline_args.length() > 0) {
                combined_body += " ";
                combined_body += inline_args;
            }
            command = "RUN: " + combined_body;
        } else if (command == "RUN" || command == "RUN_ALL") {
            command = "RUN: " + combined_body;
        }
    }

    auto cmd = apply_runner_command(context, command);

    if (config.configure) {
        config.configure(context);
    }

    if (cmd.should_run) {
        unsigned total = static_cast<unsigned>(get_test_names().size());
        unsigned run = count_passing_filters();
        // Adjust for RESUME_AFTER: skip_count tests are excluded by
        // doctest's "first" option, not reflected in filter counts.
        if (cmd.skip_count > 0 && run > static_cast<unsigned>(cmd.skip_count)) {
            run -= static_cast<unsigned>(cmd.skip_count);
        }
        unsigned skip = total - run;
        etst::print_test_count(total, skip, run);

        try {
            int result = context.run();
            (void)result;
        } catch (...) {
            Serial.println("Exception caught during test execution");
        }
    }

    // Clear the wake flag so subsequent tests in RESUME_AFTER cycles
    // see is_test_wake()==false.
    etst::clear_test_wake();

    if (etst::config.after_cycle) {
        etst::config.after_cycle();
    }

    etst::signal_done();
}

/// @brief Overload for plain String commands (no accumulated ARGS).
inline void run_cycle(const String& command) {
    CommandResult cr;
    cr.command = command;
    run_cycle(cr);
}

/**
 * @brief Initialize and run doctest tests.
 *
 * Call from setup(). Handles serial init, board init, and the first
 * READY/RUN/DONE cycle. After completion, idle_loop() accepts
 * additional commands for RESUME_AFTER cycles.
 *
 * Set callbacks on etst::config before calling this.
 */
inline void run_tests() {
    Serial.begin(115200);

    // Delay for serial connection — shorter for IDF USB-CDC
#if defined(ESP_IDF_VERSION) && !defined(ARDUINO_USB_MODE)
    delay(500);
#else
    delay(4000);
#endif

    if (etst::config.board_init) {
        if (!etst::config.board_init(Serial)) {
            etst::signal_error("hardware", "Board init failed");
            while (true) { delay(1000); }
        }
    }

    auto cmd_result = wait_for_command(etst::config.ready_timeout_ms);
    run_cycle(cmd_result);
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
    // wait_for_command sends ETST:READY periodically so the runner
    // knows the device is accepting commands.
    while (true) {
        auto cmd_result = wait_for_command(0);  // 0 = wait forever

        if (cmd_result.command.length() == 0) {
            // Should not happen with infinite wait, but be safe
            delay(1000);
            continue;
        }

        if (cmd_result.command == "RESTART") {
            Serial.println("[ETST] Restarting...");
            Serial.flush();
            delay(100);
            if (etst::config.platform_restart) {
                etst::config.platform_restart();
            }
#if defined(ESP_IDF_VERSION)
            else { esp_restart(); }
#endif
        } else if (cmd_result.command == "SLEEP") {
            Serial.println("[ETST] Entering deep sleep...");
            Serial.flush();
            delay(100);
            if (etst::config.platform_sleep) {
                etst::config.platform_sleep();
            }
#if defined(ESP_IDF_VERSION)
            else { esp_deep_sleep_start(); }
#endif
        } else if (cmd_result.command == "LIGHTSLEEP") {
            Serial.println("[ETST] Entering light sleep...");
            Serial.flush();
            delay(100);
            if (etst::config.platform_lightsleep) {
                etst::config.platform_lightsleep();
            }
#if defined(ESP_IDF_VERSION)
            else {
                esp_sleep_enable_uart_wakeup(0);
                esp_light_sleep_start();
            }
#endif
            Serial.println("[ETST] Woke from light sleep");
        } else if (cmd_result.command == "WAIT") {
            Serial.println("[ETST] Waiting (idle, no sleep)...");
        } else {
            // LIST, RUN:, or other commands
            run_cycle(cmd_result);
        }
    }
}

}  // namespace etst::doctest

// Convenience macros
#define DOCTEST_SETUP() etst::doctest::run_tests()
#define DOCTEST_LOOP()  etst::doctest::idle_loop()
