# Multi-Phase Test Design

## Overview

Generalize sleep/wake two-phase tests to support any number of phases
triggered by any kind of disruption — deep sleep, restart, power cycle,
firmware update, or custom transitions.

## Firmware API

### Phase tracking

```cpp
namespace etst {
    /// Current phase number (0 = initial, 1+ = continuation).
    /// Set by the host via --phase N in the RUN: command.
    int phase();

    /// Shorthand for phase() > 0.
    bool is_continuation();
}
```

Replaces `is_test_wake()`. The host sends `--phase N` on each cycle,
firmware stores it as a regular variable (no RTC memory).

### Ending a phase

```cpp
namespace etst {
    // Predefined transition tags
    constexpr const char* SLEEP = "sleep";
    constexpr const char* RESTART = "restart";
    constexpr const char* POWER_CYCLE = "power_cycle";

    /// Signal a phase transition. The host performs the transition
    /// and sends the test back with phase incremented.
    void signal_phase_end(const char* tag);

    /// With optional key-value parameters.
    void signal_phase_end(const char* tag, std::initializer_list<Param> params);
}
```

Users can define custom transition tags:

```cpp
constexpr const char* FLASH_UPDATE = "flash_update";
```

### Usage

```cpp
TEST_CASE("survives power cycle" * doctest::timeout(30)) {
    switch (etst::phase()) {
        case 0:
            CHECK(setup_ok());
            etst::signal_phase_end(etst::POWER_CYCLE);
            break;
        case 1:
            CHECK(recovered_ok());
            break;  // no signal_phase_end — test complete
    }
}
```

Three-phase example:

```cpp
TEST_CASE("OTA update and verify" * doctest::timeout(120)) {
    switch (etst::phase()) {
        case 0:
            CHECK(prepare_update());
            etst::signal_phase_end("flash_update", {
                {"binary", "firmware-v2.bin"},
                {"partition", "ota_1"}
            });
            break;
        case 1:
            CHECK(verify_new_firmware());
            etst::signal_phase_end(etst::RESTART);
            break;
        case 2:
            CHECK(boot_count() == expected);
            break;
    }
}
```

## Wire Protocol

### Phase transition message

```
ETST:PHASE tag="sleep" duration_ms=3000 *XX
ETST:PHASE tag="restart" *XX
ETST:PHASE tag="power_cycle" *XX
ETST:PHASE tag="flash_update" binary="firmware-v2.bin" *XX
```

Replaces `ETST:SLEEP ms=N` and `ETST:RESTART`. These become convenience
aliases that emit `ETST:PHASE` with the appropriate tag. During the
transition period, the host accepts both old and new message formats.

### RUN command

```
RUN: --phase 2 --tc "OTA update and verify" *XX
```

Replaces `--wake`. The `--continue` flag is a synonym for `--phase 1`
(backward compat for simple two-phase tests).

### Separate message types

Phase transitions, data transfer, and state save/restore are distinct
protocol messages with shared key-value encoding:

| Message | Purpose | Host action |
|---------|---------|-------------|
| `ETST:PHASE` | Transition — connection will disrupt | Perform transition, reconnect, resume |
| `ETST:DATA` | Artifact — store/process | Record for post-test analysis |
| `ETST:STATE` | Save — replay on next phase | Store, replay before next phase runs |

These are NOT unified into one message type. Different semantics,
different handler chains, shared payload encoding.

## Host Side

### PhaseHandler

```python
class PhaseHandler:
    """Override for lab-specific transition behavior."""

    def on_sleep(self, test_name: str, duration_ms: int) -> None:
        """Default: wait for USB-CDC port to disappear and reappear."""

    def on_restart(self, test_name: str) -> None:
        """Default: wait for port reconnect."""

    def on_power_cycle(self, test_name: str) -> None:
        """No default — user must implement."""
        raise NotImplementedError

    def on_transition(self, test_name: str, tag: str, params: dict) -> None:
        """Fallback for custom/unknown transition types.
        Called when no specific on_<tag> method exists."""
        raise NotImplementedError(f"No handler for transition '{tag}'")
```

The runner dispatches: looks for `on_<tag>()` method first, falls back to
`on_transition()`. Users compose via PhaseHandler, not runner subclassing.

### Phase tracking in the runner

The runner tracks per-test phase count:

```python
# test enters phase 0 (initial run)
# ETST:PHASE received → increment phase, perform transition
# send RUN: --phase 1 --tc "test name"
# ETST:PHASE received again → increment, transition
# send RUN: --phase 2 --tc "test name"
# test completes without ETST:PHASE → done, send RESUME_AFTER
```

The runner enforces a max phase count (default 10) to prevent infinite
loops from buggy tests.

### PIO integration

PhaseHandler is provided via the runner customization mechanism:

```python
# my_lab/test_runner.py
def create_runner(suite, config, options):
    from etst.runner import EmbeddedTestRunner
    from etst.hooks import PhaseHandler

    class LabHandler(PhaseHandler):
        def on_power_cycle(self, test_name):
            ppk2_toggle_power(port="/dev/ttyACM0")

        def on_transition(self, test_name, tag, params):
            if tag == "flash_update":
                flash_binary(params["binary"], params.get("partition"))
            else:
                super().on_transition(test_name, tag, params)

    runner = EmbeddedTestRunner(suite, config, options)
    runner.phase_handler = LabHandler()
    return runner
```

Loaded via `ETST_RUNNER=my_lab.test_runner` env var.

## Migration from Current API

| Old | New | Notes |
|-----|-----|-------|
| `is_test_wake()` | `is_continuation()` | Shorthand for `phase() > 0` |
| `signal_sleep(ms)` | `signal_phase_end(etst::SLEEP, {{"duration_ms", ms}})` | Convenience wrapper kept |
| `signal_restart()` | `signal_phase_end(etst::RESTART)` | Convenience wrapper kept |
| `--wake` flag | `--phase 1` | `--continue` as synonym |
| `ETST:SLEEP ms=N` | `ETST:PHASE tag="sleep" duration_ms=N` | Old format accepted during transition |
| `ETST:RESTART` | `ETST:PHASE tag="restart"` | Old format accepted during transition |

Convenience wrappers `signal_sleep(ms)` and `signal_restart()` remain as
shorthand — they call `signal_phase_end()` internally.

## Design Constraint: Phases Require a Transition

Phases currently require a device transition (sleep, restart, power cycle)
because `signal_phase_end()` works by ending the current `context.run()`
batch. On disruptive transitions this happens naturally (device reboots).

Non-disruptive phases (host does work between phases while device stays
running) would require a mechanism to pause `context.run()` mid-execution.
Doctest doesn't support this natively — the test function must return or
the device must restart for the next phase to begin.

Non-disruptive phases are deferred to a future design. The workaround is
to use `etst::RESTART` as the transition — the device reboots (fast on
ESP32) and the host sends the next phase command.

## Future Exploration: Distributed Test Fixtures

A test fixture could span multiple devices: DUT + harness hardware (e.g.
an ESP32 with GPIOs wired to the DUT for hardware-level monitoring,
current measurement, power control). A single test body would describe
what to verify, and the framework would distribute execution:

- **Host** orchestrates the overall test
- **DUT** runs test code, reports results
- **Harness** toggles GPIOs, measures current, provides/disables power
- A harness could run part of the test protocol itself, forwarding
  commands to the DUT (host → harness → DUT serial chain)

This is architecturally compatible with the phase/handler model — the
harness is another device with its own PhaseHandler, and the host
coordinates both. But the test authoring experience (one body of code
that spans DUT and harness) needs its own design.

## Testing

The implementation plan must include tests for:

**Unit tests (Python, mocked serial):**
- `ETST:PHASE` message parsed correctly (tag + params)
- Phase counter increments across cycles
- Host sends `--phase N` on resume
- PhaseHandler dispatch: `on_sleep`, `on_restart`, `on_transition` fallback
- Custom transition tag dispatches to `on_transition()`
- Max phase count triggers error (infinite loop prevention)
- Backward compat: `ETST:SLEEP` / `--wake` still accepted

**Unit tests (C++, native or mocked):**
- `etst::phase()` returns value from `--phase N`
- `etst::is_continuation()` returns `phase() > 0`
- `signal_phase_end()` emits correct `ETST:PHASE` wire format
- Convenience wrappers emit correct messages

**Integration tests (mocked serial, full orchestration):**
- Two-phase sleep test (existing behavior, new protocol)
- Three-phase test: sleep → restart → verify
- Custom transition type end-to-end

**Acceptance tests (on hardware):**
- Two-phase deep sleep test passes with `--phase` protocol
- Three-phase test with two sleep cycles
- `RESUME_AFTER` correctly runs remaining tests after multi-phase test

## Scope

This spec covers:
- Phase tracking API (firmware + host)
- Phase transition protocol messages (disruptive transitions only)
- PhaseHandler dispatch mechanism
- Migration path from current sleep/wake API

Does NOT cover:
- Non-disruptive phases (host work between phases, device stays running)
- Distributed test fixtures (DUT + harness hardware)
- `ETST:DATA` / `ETST:STATE` message implementation — separate spec
- Platform namespace implementation — covered in rename spec
- PIO deferred loading mechanism — PIO-specific implementation detail
