/**
 * @file default_main.cpp
 * @brief Default Arduino entry points and weak hook functions.
 *
 * Provides setup()/loop() and weak customization hooks so consuming
 * projects don't need a main.cpp. Override any hook by defining the
 * same function (strong symbol) in a separate .cpp file in your test
 * directory.
 *
 * Example — test/board_init.cpp:
 * @code
 *   #include <Arduino.h>
 *
 *   bool ptr_board_init(Print& log) {
 *       log.println("My board init");
 *       return true;
 *   }
 * @endcode
 */

#define DOCTEST_CONFIG_IMPLEMENT
#include <doctest.h>
#include <pio_test_runner/doctest_runner.h>

// =========================================================================
// Weak hooks — override these in your test .cpp files
// =========================================================================

/// Board/hardware initialization. Called after Serial.begin(), before
/// tests run. Return false to halt (e.g. board detection failure).
__attribute__((weak)) bool ptr_board_init(Print& log) {
    (void)log;
    return true;
}

/// Called after all filters applied, before doctest context.run().
/// Use for runtime-derived excludes (e.g. firmware version gating).
__attribute__((weak)) void ptr_configure_context(doctest::Context& ctx) {
    (void)ctx;
}

/// Called after each test cycle completes (after PTR:DONE).
/// Use for coverage dumps, cleanup, resource deinitialization.
__attribute__((weak)) void ptr_after_cycle() {
}

// =========================================================================
// Default Arduino entry points (weak — existing main.cpp overrides these)
// =========================================================================

__attribute__((weak)) void setup() {
    ptr_doctest::config.board_init = ptr_board_init;
    ptr_doctest::config.configure_context = ptr_configure_context;
    ptr_doctest::config.after_cycle = ptr_after_cycle;
    ptr_doctest::run_tests();
}

__attribute__((weak)) void loop() {
    ptr_doctest::idle_loop();
}
