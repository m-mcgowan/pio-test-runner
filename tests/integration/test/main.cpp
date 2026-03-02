#define DOCTEST_CONFIG_IMPLEMENT
#include <doctest.h>
#include <Arduino.h>

static bool debug_init(Print& log) {
    log.println("[ptr] board_init OK");
    return true;
}

#define PTR_BOARD_INIT debug_init
#include <pio_test_runner/doctest_runner.h>

void setup() { DOCTEST_SETUP(); }
void loop()  { DOCTEST_LOOP(); }
