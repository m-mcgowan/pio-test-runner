#define DOCTEST_CONFIG_IMPLEMENT
#include <doctest.h>
#include <Arduino.h>
#include <pio_test_runner/doctest_runner.h>

static bool debug_init(Print& log) {
    log.println("[ptr] board_init OK");
    return true;
}

void setup() {
    ptr_doctest::config.board_init = debug_init;
    DOCTEST_SETUP();
}
void loop() { DOCTEST_LOOP(); }
