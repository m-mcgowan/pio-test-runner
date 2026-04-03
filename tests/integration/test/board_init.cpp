/// Board init for integration tests — overrides the weak default.
#include <Arduino.h>

bool ptr_board_init(Print& log) {
    log.println("[ptr] board_init OK");
    return true;
}
