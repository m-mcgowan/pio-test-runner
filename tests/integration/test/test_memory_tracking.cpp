/**
 * @file test_memory_tracking.cpp
 * @brief Tests that exercise the [MEM] marker pipeline.
 *
 * PtrTestListener (from doctest_runner.h) emits [MEM] Before/After
 * markers around each test. The Python MemoryTracker parses these and
 * reports leaks in the summary.
 *
 * The "deliberate leak" test intentionally leaks memory so we can
 * verify the runner detects and reports it.
 */

#include <doctest.h>
#include <cstdlib>

TEST_SUITE("Memory") {

TEST_CASE("clean allocation has near-zero delta") {
    void* p = malloc(4096);
    CHECK(p != nullptr);
    free(p);
}

TEST_CASE("deliberate leak is detected by runner") {
    // Leak ~8KB — PtrTestListener will emit:
    //   [MEM] After: free=X (delta=-8192), min=Y
    //   [MEM] WARNING: Test leaked ~8192 bytes!
    // The Python MemoryTracker should report this in the summary.
    void* leaked = malloc(8192);
    CHECK(leaked != nullptr);
    // intentionally not freed
}

}
