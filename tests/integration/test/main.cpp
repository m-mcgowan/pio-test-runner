/// Test entry point — provides setup()/loop() and doctest implementation.
/// Replicates what default_main.cpp provided before it was excluded from
/// the library build (header-only mode).

#define DOCTEST_CONFIG_IMPLEMENT
#include <doctest.h>
#include <pio_test_runner/doctest_runner.h>

// board_init.cpp provides ptr_board_init() as a strong symbol.
extern bool ptr_board_init(Print& log);

void setup() {
    ptr_doctest::config.board_init = ptr_board_init;
    ptr_doctest::run_tests();
}

void loop() {
    ptr_doctest::idle_loop();
}
