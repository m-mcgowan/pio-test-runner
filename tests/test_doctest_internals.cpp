/**
 * @file test_doctest_internals.cpp
 * @brief Native tests verifying doctest internal assumptions.
 *
 * These tests run on the host (not on hardware) and verify:
 * - m_skip is writable on registered test cases
 * - modifying m_skip does not break std::set ordering
 * - glob_match works correctly
 * - tokenize_args handles quoting and whitespace
 *
 * Build and run (from tests/ directory):
 *   c++ -std=c++17 -Iintegration/.pio/libdeps/esp32s3/doctest/doctest \
 *       test_doctest_internals.cpp -o test_doctest_internals
 *   ./test_doctest_internals
 */

// We need DOCTEST_CONFIG_IMPLEMENT to access getRegisteredTests()
#define DOCTEST_CONFIG_IMPLEMENT
#include <doctest.h>

// Stub Arduino String class for host builds
#ifndef ARDUINO
#include <string>
class String {
    std::string s_;
public:
    String() = default;
    String(const char* c) : s_(c) {}
    String(const std::string& s) : s_(s) {}
    const char* c_str() const { return s_.c_str(); }
    size_t length() const { return s_.size(); }
    bool startsWith(const char* prefix) const {
        return s_.compare(0, strlen(prefix), prefix) == 0;
    }
    char operator[](size_t i) const { return s_[i]; }
    String substring(size_t from, size_t to) const {
        return String(s_.substr(from, to - from));
    }
    String substring(size_t from) const {
        return String(s_.substr(from));
    }
    void trim() {
        size_t start = s_.find_first_not_of(" \t\r\n");
        size_t end = s_.find_last_not_of(" \t\r\n");
        if (start == std::string::npos) { s_.clear(); return; }
        s_ = s_.substr(start, end - start + 1);
    }
    String& operator+=(char c) { s_ += c; return *this; }
    String& operator+=(const char* s) { s_ += s; return *this; }
    bool operator==(const String& other) const { return s_ == other.s_; }
    friend bool operator==(const String& a, const char* b) { return a.s_ == b; }
};

// Stub Serial for host builds
struct SerialStub {
    template<typename... Args>
    void printf(const char*, Args...) {}
    void println(const char*) {}
};
static SerialStub Serial;
#endif

// Now include our header (needs String and Serial stubs above)
// We only include the functions we need, not the full runner
namespace etst::doctest {

// Copy glob_match and tokenize_args from doctest_runner.h
// (can't include the full header without Arduino.h)

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

inline std::vector<String> tokenize_args(const String& body) {
    std::vector<String> args;
    size_t i = 0;
    while (i < body.length()) {
        while (i < body.length() && body[i] == ' ') i++;
        if (i >= body.length()) break;
        String arg;
        if (body[i] == '"' || body[i] == '\'') {
            char quote = body[i++];
            while (i < body.length() && body[i] != quote) {
                arg += body[i++];
            }
            if (i < body.length()) i++;
        } else {
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

inline int modify_skip(const char* pattern, bool match_suite, bool skip_value) {
    int count = 0;
    for (auto& tc : ::doctest::detail::getRegisteredTests()) {
        const char* name = match_suite ? tc.m_test_suite : tc.m_name;
        if (name && glob_match(name, pattern)) {
            const_cast<::doctest::detail::TestCase&>(tc).m_skip = skip_value;
            count++;
        }
    }
    return count;
}

}  // namespace etst::doctest

// =========================================================================
// Tests
// =========================================================================

TEST_SUITE("glob_match") {

TEST_CASE("exact match") {
    CHECK(etst::doctest::glob_match("hello", "hello"));
    CHECK_FALSE(etst::doctest::glob_match("hello", "world"));
}

TEST_CASE("star wildcard") {
    CHECK(etst::doctest::glob_match("hello world", "*world"));
    CHECK(etst::doctest::glob_match("hello world", "hello*"));
    CHECK(etst::doctest::glob_match("hello world", "*lo wo*"));
    CHECK(etst::doctest::glob_match("hello world", "*"));
    CHECK_FALSE(etst::doctest::glob_match("hello world", "*xyz*"));
}

TEST_CASE("question mark wildcard") {
    CHECK(etst::doctest::glob_match("cat", "c?t"));
    CHECK_FALSE(etst::doctest::glob_match("cart", "c?t"));
}

TEST_CASE("combined wildcards") {
    CHECK(etst::doctest::glob_match("Service/WDT", "*WDT*"));
    CHECK(etst::doctest::glob_match("Service/WDT", "Service/*"));
    CHECK(etst::doctest::glob_match("join feeds WDT during slow cleanup", "*WDT*slow*"));
}

TEST_CASE("empty strings") {
    CHECK(etst::doctest::glob_match("", ""));
    CHECK(etst::doctest::glob_match("", "*"));
    CHECK_FALSE(etst::doctest::glob_match("", "a"));
    CHECK_FALSE(etst::doctest::glob_match("a", ""));
}

}  // TEST_SUITE glob_match

TEST_SUITE("tokenize_args") {

TEST_CASE("simple flags") {
    auto args = etst::doctest::tokenize_args("--tc *foo* --ts *bar*");
    REQUIRE(args.size() == 4);
    CHECK(args[0] == "--tc");
    CHECK(args[1] == "*foo*");
    CHECK(args[2] == "--ts");
    CHECK(args[3] == "*bar*");
}

TEST_CASE("quoted values") {
    auto args = etst::doctest::tokenize_args("--tc \"hello world\"");
    REQUIRE(args.size() == 2);
    CHECK(args[0] == "--tc");
    CHECK(args[1] == "hello world");
}

TEST_CASE("single flag") {
    auto args = etst::doctest::tokenize_args("--no-skip");
    REQUIRE(args.size() == 1);
    CHECK(args[0] == "--no-skip");
}

TEST_CASE("empty string") {
    auto args = etst::doctest::tokenize_args("");
    CHECK(args.size() == 0);
}

TEST_CASE("extra whitespace") {
    auto args = etst::doctest::tokenize_args("  --tc   *foo*  ");
    REQUIRE(args.size() == 2);
    CHECK(args[0] == "--tc");
    CHECK(args[1] == "*foo*");
}

}  // TEST_SUITE tokenize_args

// =========================================================================
// build_doctest_argv — flag=value joining for applyCommandLine
// =========================================================================

namespace etst::doctest {

inline std::vector<String> build_doctest_argv(const std::vector<String>& args) {
    std::vector<String> result;
    result.push_back("test");
    for (size_t i = 0; i < args.size(); i++) {
        if (args[i].startsWith("--") && i + 1 < args.size()
                && !args[i + 1].startsWith("--")) {
            String combined;
            combined += args[i].c_str();
            combined += "=";
            combined += args[i + 1].c_str();
            result.push_back(combined);
            i++;
        } else {
            result.push_back(args[i]);
        }
    }
    return result;
}

}  // namespace etst::doctest

TEST_SUITE("build_doctest_argv") {

TEST_CASE("flag and value joined with =") {
    auto args = etst::doctest::tokenize_args("--tc *foo*");
    auto argv = etst::doctest::build_doctest_argv(args);
    REQUIRE(argv.size() == 2);
    CHECK(argv[0] == "test");
    CHECK(argv[1] == "--tc=*foo*");
}

TEST_CASE("standalone flag preserved") {
    auto args = etst::doctest::tokenize_args("--no-skip");
    auto argv = etst::doctest::build_doctest_argv(args);
    REQUIRE(argv.size() == 2);
    CHECK(argv[0] == "test");
    CHECK(argv[1] == "--no-skip");
}

TEST_CASE("multiple flags joined independently") {
    auto args = etst::doctest::tokenize_args("--tc *a* --ts *b*");
    auto argv = etst::doctest::build_doctest_argv(args);
    REQUIRE(argv.size() == 3);
    CHECK(argv[0] == "test");
    CHECK(argv[1] == "--tc=*a*");
    CHECK(argv[2] == "--ts=*b*");
}

TEST_CASE("already-joined flag=value preserved") {
    auto args = etst::doctest::tokenize_args("--tc=*foo*");
    auto argv = etst::doctest::build_doctest_argv(args);
    REQUIRE(argv.size() == 2);
    CHECK(argv[0] == "test");
    CHECK(argv[1] == "--tc=*foo*");
}

TEST_CASE("mixed standalone and value flags") {
    auto args = etst::doctest::tokenize_args("--no-skip --tc *foo* --tse *slow*");
    auto argv = etst::doctest::build_doctest_argv(args);
    REQUIRE(argv.size() == 4);
    CHECK(argv[0] == "test");
    CHECK(argv[1] == "--no-skip");
    CHECK(argv[2] == "--tc=*foo*");
    CHECK(argv[3] == "--tse=*slow*");
}

TEST_CASE("empty args produces only program name") {
    std::vector<String> args;
    auto argv = etst::doctest::build_doctest_argv(args);
    REQUIRE(argv.size() == 1);
    CHECK(argv[0] == "test");
}

}  // TEST_SUITE build_doctest_argv

// Skip-decorated targets for modify_skip tests
TEST_CASE("_target_skip_A" * doctest::skip()) { FAIL("should not run"); }
TEST_CASE("_target_skip_B" * doctest::skip()) { FAIL("should not run"); }

TEST_SUITE("modify_skip") {

TEST_CASE("unskip clears m_skip") {
    // Find target_A and verify it starts skipped
    bool found = false;
    for (const auto& tc : doctest::detail::getRegisteredTests()) {
        if (strcmp(tc.m_name, "_target_skip_A") == 0) {
            CHECK(tc.m_skip == true);
            found = true;
            break;
        }
    }
    REQUIRE(found);

    int count = etst::doctest::modify_skip("*_target_skip_A*", false, false);
    CHECK(count == 1);

    for (const auto& tc : doctest::detail::getRegisteredTests()) {
        if (strcmp(tc.m_name, "_target_skip_A") == 0) {
            CHECK(tc.m_skip == false);
            break;
        }
    }

    // Restore
    etst::doctest::modify_skip("*_target_skip_A*", false, true);
}

TEST_CASE("pattern only affects matching tests") {
    etst::doctest::modify_skip("*_target_skip_A*", false, false);

    bool a_skip = true, b_skip = false;
    for (const auto& tc : doctest::detail::getRegisteredTests()) {
        if (strcmp(tc.m_name, "_target_skip_A") == 0) a_skip = tc.m_skip;
        if (strcmp(tc.m_name, "_target_skip_B") == 0) b_skip = tc.m_skip;
    }
    CHECK_FALSE(a_skip);  // unskipped
    CHECK(b_skip);         // still skipped

    etst::doctest::modify_skip("*_target_skip_A*", false, true);
}

TEST_CASE("set ordering preserved after modification") {
    // Modify m_skip and verify the set is still iterable and consistent
    etst::doctest::modify_skip("*_target_skip_A*", false, false);
    etst::doctest::modify_skip("*_target_skip_A*", false, true);

    // Iterate the full set — if ordering is broken, this would crash
    // or produce duplicate/missing entries
    size_t count = 0;
    for (const auto& tc : doctest::detail::getRegisteredTests()) {
        REQUIRE(tc.m_name != nullptr);
        count++;
    }
    CHECK(count > 5);  // we have at least our test cases + targets
}

TEST_CASE("force-skip sets m_skip on non-skipped test") {
    // Find a non-skipped test
    const char* target = nullptr;
    for (const auto& tc : doctest::detail::getRegisteredTests()) {
        if (!tc.m_skip && strcmp(tc.m_name, "_target_skip_A") != 0
                       && strcmp(tc.m_name, "_target_skip_B") != 0) {
            target = tc.m_name;
            break;
        }
    }
    REQUIRE(target != nullptr);

    etst::doctest::modify_skip(target, false, true);
    for (const auto& tc : doctest::detail::getRegisteredTests()) {
        if (strcmp(tc.m_name, target) == 0) {
            CHECK(tc.m_skip == true);
            break;
        }
    }

    // Restore
    etst::doctest::modify_skip(target, false, false);
}

}  // TEST_SUITE modify_skip

// =========================================================================
// extract_etst_flags — argument-order processing
// =========================================================================

// Replicate extract_etst_flags for native testing (uses our local String/Serial stubs)
namespace etst::doctest {

inline void extract_etst_flags(std::vector<String>& args) {
    struct { const char* flag; bool match_suite; bool skip_value; } etst_flags[] = {
        {"--unskip-tc", false, false},
        {"--unskip-ts", true,  false},
        {"--skip-tc",   false, true},
        {"--skip-ts",   true,  true},
    };

    for (size_t i = 0; i < args.size(); ) {
        bool matched = false;
        for (auto& pf : etst_flags) {
            if (args[i] == pf.flag && i + 1 < args.size()) {
                const char* pattern = args[i + 1].c_str();
                modify_skip(pattern, pf.match_suite, pf.skip_value);
                args.erase(args.begin() + i, args.begin() + i + 2);
                matched = true;
                break;
            }
        }
        if (!matched) i++;
    }
}

}  // namespace etst::doctest

// Non-skipped target for ordering tests
TEST_CASE("_target_ordering" * doctest::skip()) { FAIL("should not run"); }

TEST_SUITE("extract_etst_flags") // was extract_ptr_flags {

TEST_CASE("unskip then skip: last flag wins") {
    // Start: _target_ordering is skipped (decorator)
    // Apply: --unskip-tc first, then --skip-tc
    // Expected: skipped (skip-tc is last)
    auto args = etst::doctest::tokenize_args("--unskip-tc *_target_ordering* --skip-tc *_target_ordering*");
    etst::doctest::extract_etst_flags(args);

    for (const auto& tc : doctest::detail::getRegisteredTests()) {
        if (strcmp(tc.m_name, "_target_ordering") == 0) {
            CHECK(tc.m_skip == true);  // skip was last
            break;
        }
    }
    // Already in correct state (skipped), no restore needed
}

TEST_CASE("skip then unskip: last flag wins") {
    // Apply: --skip-tc first, then --unskip-tc
    // Expected: unskipped (unskip-tc is last)
    auto args = etst::doctest::tokenize_args("--skip-tc *_target_ordering* --unskip-tc *_target_ordering*");
    etst::doctest::extract_etst_flags(args);

    for (const auto& tc : doctest::detail::getRegisteredTests()) {
        if (strcmp(tc.m_name, "_target_ordering") == 0) {
            CHECK(tc.m_skip == false);  // unskip was last
            break;
        }
    }
    // Restore
    etst::doctest::modify_skip("*_target_ordering*", false, true);
}

TEST_CASE("non-ptr flags are preserved in args") {
    auto args = etst::doctest::tokenize_args("--unskip-tc *_target_ordering* --tc *foo* --no-skip");
    etst::doctest::extract_etst_flags(args);

    // --unskip-tc + value removed, --tc *foo* and --no-skip remain
    REQUIRE(args.size() == 3);
    CHECK(args[0] == "--tc");
    CHECK(args[1] == "*foo*");
    CHECK(args[2] == "--no-skip");

    // Restore
    etst::doctest::modify_skip("*_target_ordering*", false, true);
}

TEST_CASE("mixed skip and doctest flags preserve order") {
    auto args = etst::doctest::tokenize_args("--ts *Suite* --unskip-tc *_target_ordering* --tce *exclude*");
    etst::doctest::extract_etst_flags(args);

    // Only --unskip-tc removed, doctest flags preserved in order
    REQUIRE(args.size() == 4);
    CHECK(args[0] == "--ts");
    CHECK(args[1] == "*Suite*");
    CHECK(args[2] == "--tce");
    CHECK(args[3] == "*exclude*");

    // Restore
    etst::doctest::modify_skip("*_target_ordering*", false, true);
}

}  // TEST_SUITE extract_etst_flags

int main(int argc, char** argv) {
    doctest::Context context;
    context.setOption("no-skip", false);  // don't run skip targets
    context.applyCommandLine(argc, argv);
    return context.run();
}
