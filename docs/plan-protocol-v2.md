# Plan: Protocol v2 — `TEST:` prefix and post-test lifecycle

## Motivation

The current `ETST:` protocol prefix reads as "pointer" and ties the protocol
to PlatformIO. The protocol is generic enough to work with any host runner.
Additionally, post-test behavior is hardcoded — the runner sends `SLEEP` and
the firmware either sleeps or loops. Real devices need configurable cleanup
and post-test actions.

## Protocol prefix rename

Rename `ETST:` to `TEST:` throughout:

| Current | Proposed |
|---------|----------|
| `ETST:READY` | `TEST:READY` |
| `ETST:DONE` | `TEST:DONE` |
| `ETST:SLEEP` | `TEST:SLEEP` |
| `ETST:TEST:START` | `TEST:START` |
| `ETST:MEM:BEFORE` | `TEST:MEM:BEFORE` |
| `ETST:MEM:AFTER` | `TEST:MEM:AFTER` |
| `ETST:DISCONNECT` | `TEST:DISCONNECT` |
| `ETST:RECONNECT` | `TEST:RECONNECT` |

Accept both prefixes during a transition period for backward compatibility.

Environment variables: `PTR_TEST_CASE` → `TEST_CASE` (keep `PTR_` as aliases).

## Post-test lifecycle

### Problem

After tests complete, the device needs to:
1. Deinitialize hardware (I2C buses, sensors, LEDs, radios)
2. Put hardware in a safe/low-power state
3. Enter a post-test mode (wait, sleep, halt, bootloader)

Currently the runner sends SLEEP and the firmware either understands it or
loops forever. The firmware should control this since it knows what hardware
is present and what "safe state" means.

### Design

#### Firmware-side callbacks

```cpp
// Project defines these before including the runner header
#define TEST_BOARD_INIT     my_board_init      // existing
#define TEST_AFTER_ACTION      my_after_action       // NEW: cleanup after all tests
#define TEST_DEFAULT_IDLE   TestIdleMode::SLEEP // NEW: default post-test action
```

`TEST_AFTER_ACTION` signature:
```cpp
void my_after_action(Print& log) {
    disable_all_sensors();
    i2c_buses_end();
    leds_off();
    log.println("Hardware deinitialized");
}
```

Called after `TEST:DONE` is sent, before entering idle mode.

#### Idle modes

```cpp
enum class TestIdleMode {
    WAIT,        // Block waiting for host commands (default for tethered devices)
    SLEEP,       // Deep sleep (battery-powered devices)
    HALT,        // Busy-wait loop (debugging, prevents watchdog reset)
    BOOTLOADER,  // Enter ROM bootloader (ready for reflash)
    RESET,       // Software reset
};
```

The firmware declares a default via `TEST_DEFAULT_IDLE`. The host can
override by sending a command before the firmware acts:

```
Device: TEST:DONE
Device: TEST:IDLE mode=sleep    ← declares intent
Host:   WAIT                    ← override (optional, within 1s window)
Device: (blocks waiting)
```

If no override arrives within the window, the firmware proceeds with its
declared mode.

#### Platform abstraction

Sleep, bootloader, and reset are platform-specific. The runner header
provides weak defaults for ESP32:

```cpp
namespace test_runner::platform {
    void enter_sleep();       // esp_deep_sleep_start()
    void enter_bootloader();  // ROM bootloader via RTC flag + reset
    void reset();             // esp_restart()
    void halt();              // while(true) delay(1000);
}
```

Projects can override these for custom hardware (e.g. external watchdog
that needs feeding during halt, or specific GPIO states for safe sleep).

### Runner-side (Python)

The runner reads `TEST:IDLE mode=X` and can:
- Accept it (do nothing, let firmware proceed)
- Override it (send `WAIT`, `SLEEP`, `BOOTLOADER`, `RESET`)
- Configure a default via `--post-test` CLI arg or env var

```bash
# Let firmware decide (default)
./run_tests.sh 1.10

# Force wait after tests (debugging)
./run_tests.sh --post-test wait 1.10

# Force bootloader (reflash workflow)
./run_tests.sh --post-test bootloader 1.10
```

## Runtime configuration via -a

Post-test behavior and other configuration can be passed alongside filter
flags using the same `-a` mechanism:

```bash
# Post-test action
pio test -a "--post-test sleep"
pio test -a "--post-test bootloader"

# Combined with filters
pio test -a "--ts *Sensors*" -a "--post-test sleep"

# Via env var
TEST_POST_ACTION=sleep pio test
```

The firmware receives all flags in the `RUN:` command body and parses them
in `apply_runner_command()`. Unrecognized flags are ignored (forward compat).

Supported configuration flags (proposed):
- `--post-test <mode>` — override idle mode (wait/sleep/halt/bootloader/reset)
- `--tc`, `--ts`, `--tce`, `--tse` — existing filter flags
- `--verbose` — enable detailed test output on device (future)

## Handshake for disruptive commands

### Problem

Commands that interrupt the serial stream (SLEEP, RESTART, LIGHTSLEEP)
are currently fire-and-forget. The device sends the signal and immediately
acts, without knowing if the host received it. This causes:

- Host misses ETST:SLEEP → never reconnects after wake
- Host misses ETST:DONE before SLEEP → reports test as failed
- Acceptance tests can't safely send RUN_ALL when sleep tests are present

### Design

All disruptive commands use a two-phase handshake:

```
Device: TEST:SLEEP ms=3000       ← intent
Host:   ACK                      ← host confirms receipt
Device: (enters deep sleep)

Device: TEST:RESTART
Host:   ACK
Device: esp_restart()

Device: TEST:IDLE mode=sleep     ← post-test intent
Host:   ACK  (or OVERRIDE wait)  ← host can override
Device: (acts on final mode)
```

Timeout fallback: if no ACK arrives within 2s, the device proceeds
anyway (for standalone operation without a host runner). The timeout
is configurable via `PTR_ACK_TIMEOUT_MS`.

This applies to:
- `signal_sleep()` — wait for ACK before `esp_deep_sleep_start()`
- `signal_restart()` — wait for ACK before `esp_restart()`
- `signal_lightsleep()` — wait for ACK before `esp_light_sleep_start()`
- Post-test idle mode — wait for ACK/OVERRIDE before acting

### Firmware API

```cpp
// In test_runner.h:
bool wait_for_ack(uint32_t timeout_ms = 2000);

void signal_sleep(uint32_t ms) {
    emit(Serial, "ETST:SLEEP ms=%lu", (unsigned long)ms);
    wait_for_ack();  // blocks until ACK or timeout
}
```

### Runner API

The runner sends ACK after processing each disruptive signal:

```python
def _on_sleep(self, duration_ms):
    self._send_ack()  # device can now sleep
    self._handle_sleep_resume()
```

## Migration

1. Add `TEST:` prefix support alongside `ETST:` (both accepted)
2. Add handshake for disruptive commands (ACK protocol)
3. Add `TEST_AFTER_ACTION` callback
4. Add idle mode declaration and override
5. Deprecate `ETST:` prefix (warn in logs)
6. Remove `ETST:` support in a future major version

## Files to modify

### Firmware (C++ headers)
- `include/pio_test_runner/test_runner.h` — prefix rename, idle modes
- `include/pio_test_runner/doctest_runner.h` — post-test callback, idle loop

### Runner (Python)
- `src/pio_test_runner/protocol.py` — accept both prefixes
- `src/pio_test_runner/ready_run_protocol.py` — parse `TEST:IDLE`
- `src/pio_test_runner/runner.py` — idle mode override, `--post-test` option

### Tests
- Update all test fixtures and assertions for new prefix
- Add idle mode negotiation tests
