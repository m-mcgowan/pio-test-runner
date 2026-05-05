# Roadmap

This document captures the forward direction for embedded-test-runner: architectural
refactoring, new capabilities, and platform expansion. The goal is to evolve from
a PlatformIO-doctest-specific tool into a general-purpose embedded test
orchestration framework.

## Guiding Principles

- **DX stability**: the user-facing API should not change for existing users.
  Refactoring happens behind the current interface. If the DX has rough edges,
  fix those before stabilizing — breaking changes are cheaper now than later.
- **Protocol is the contract**: the ETST wire protocol (ETST:READY, ETST:DONE,
  ETST:CASE:START, etc.) is framework-agnostic by design. Any test framework
  that emits these markers gets full orchestration support for free.
- **Host knows more than firmware**: the host has unlimited memory, network
  access, and persistent storage. Push complexity to the host where possible
  (e.g. `--wake` flag instead of RTC memory, host-side state replay instead
  of firmware-side RTC arrays).

## Current Architecture

```
┌─────────────────────────────┐   ┌─────────────────────────────┐
│       Host (Python)         │   │     Firmware (C++)          │
│                             │   │                             │
│  runner.py                  │   │  etst/doctest/runner.h      │
│    ├─ orchestration         │   │    ├─ command parsing       │
│    ├─ sleep/wake mgmt       │   │    ├─ idle loop             │
│    ├─ filter building ◄─────┼───┼──► ├─ filter application    │
│    └─ result reporting      │   │    ├─ EtstDoctestListener   │
│                             │   │    └─ context.run()         │
│  ready_run_protocol.py      │   │                             │
│    └─ state machine         │   │  test_runner.h              │
│                             │   │    └─ protocol markers      │
│  protocol.py                │   │                             │
│    └─ CRC, wire format      │   │  protocol.h                 │
│                             │   │    └─ CRC, emit()           │
└─────────────────────────────┘   └─────────────────────────────┘
```

**What's clean** (framework-agnostic, reusable):
- Transport: `protocol.h` / `protocol.py` — CRC, wire format
- Lifecycle: `test_runner.h` — sleep/ready/done/memory markers
- Infrastructure: serial port, crash detection, disconnect handling, timing
- Configuration: `<etst/env.h>` host→firmware env var passing via `ETST:ARGS`,
  typed lookups (`etst::env<T>`), `require_env` doctest decorator
- Extensibility: receiver plugin API via the
  `embedded_test_runner.receivers` setuptools entry-point group, plus
  `on_partition_start()` / `on_partition_complete()` lifecycle hooks on
  `EmbeddedTestRunner`

**What's coupled** (doctest-specific):
- `etst/doctest/runner.h` — monolith mixing orchestration with doctest internals
- `runner.py` — filter syntax (`--tc`/`--ts`), base class, command format
- `ready_run_protocol.py` — assumes suite/name/timeout from CASE:START

**What's PlatformIO-specific** (host side only):
- `runner.py` inherits from PIO's `TestRunnerBase`
- Serial port discovery via `SerialPortFinder`
- Result reporting via PIO's `TestCase`/`TestStatus`
- Config from `platformio.ini`

**Partially clean** (per Phase 1.5):
- `MemoryTracker` (in `embedded-bridge`) currently has inline ETST
  protocol parsing. The plan is to move parsing to the runner and pass
  plain values into the receiver.

---

## Phase 1: Framework & Platform Abstraction (C++ Side)

### Namespace Structure

```
etst::                              framework-agnostic protocol & orchestration
  signal_sleep(), signal_restart()  lifecycle signals
  is_continuation()                 multi-phase test detection
  send_data(), on_restore()         (future) data channel

etst::doctest::                     doctest adapter
  EtstDoctestListener               doctest::IReporter → ETST markers
  run_tests(), idle_loop()          doctest entry points
  config                            doctest-specific callbacks

etst::unity::                       (future) Unity adapter
etst::catch2::                      (future) Catch2 adapter

etst::platform::                    platform abstraction
  restart(), sleep(), lightsleep()  overridable platform hooks

etst::platform::esp32::             ESP32 implementation
etst::platform::stm32::            (future) STM32 implementation
```

### File Layout

```
include/etst/
  ├─ protocol.h              CRC, emit(), wire format
  ├─ test_runner.h           lifecycle signals, phase detection, memory markers
  ├─ orchestrator.h          command parsing, idle loop, run cycle
  ├─ platform.h              platform abstraction (restart, sleep hooks)
  ├─ platform/esp32.h        ESP32 defaults
  ├─ doctest/
  │   ├─ runner.h            doctest adapter (listener, filters, context.run)
  │   └─ config.h            doctest-specific callbacks
  └─ unity/                  (future)
      └─ runner.h

```

### Multi-Phase Tests

Sleep/wake is one kind of phase transition. The framework should support
any operation that disrupts the test lifecycle:

| Trigger | Firmware action | Host action | Example |
|---------|----------------|-------------|---------|
| Deep sleep | `esp_deep_sleep_start()` | Wait for port drop/reappear | Verify RTC data survives |
| Software restart | `esp_restart()` | Wait for reconnect | Verify boot count persists |
| Power cycle | Signal host | Toggle USB power, PPK2, GPIO | Verify cold boot recovery |
| Firmware update | Signal host | Flash new binary, reconnect | Verify OTA upgrade path |

The protocol generalizes `--wake` to `--phase N` or `--continue`:

```
RUN: --continue --tc "survives power cycle"
```

And `is_test_wake()` generalizes to:

```cpp
if (etst::is_continuation()) {
    // Phase 2+: verify post-transition state
} else {
    // Phase 1: setup, then trigger transition
    etst::signal_phase_end(ETST_POWER_CYCLE);
}
```

Multiple phases per test are supported — each `signal_phase_end()` triggers
a new cycle. The host tracks the phase count.

### Host-Side Phase Hooks (Python)

Phase transitions are bilateral — firmware does something AND the host does
something. The Python runner needs hooks for each transition type:

```python
class PhaseHandler:
    def on_sleep(self, test_name: str, duration_ms: int) -> None:
        """Default: wait for port to disappear, then reappear."""
        ...

    def on_restart(self, test_name: str) -> None:
        """Default: wait for port reconnect."""
        ...

    def on_power_cycle(self, test_name: str) -> None:
        """User-provided: toggle hardware power via PPK2, USB hub, GPIO."""
        raise NotImplementedError("Configure a power cycle handler")

    def on_phase_start(self, test_name: str, phase: int) -> None:
        """General hook for any phase transition."""
        ...
```

This is how hardware-specific test infrastructure (PPK2, labgrid, USB hubs,
Raspberry Pi GPIO) integrates with the runner. Users subclass `PhaseHandler`
for their lab setup.

### Adapter Interface

```cpp
struct TestFrameworkAdapter {
    virtual unsigned total_tests() = 0;
    virtual std::vector<const char*> test_names() = 0;
    virtual unsigned apply_filters(const std::vector<String>& args) = 0;
    virtual int skip_to_after(const char* name) = 0;
    virtual int run() = 0;
};
```

### Entry Point / main.cpp (Done — config struct shipped in v0.3.0)

The `default_main.cpp` link conflict (DOCTEST_CONFIG_IMPLEMENT emitted by
both the library and user code) was resolved in v0.3.0 — `default_main.cpp`
is no longer in the library `srcFilter`; consumers create their own
`test/main.cpp`. Customization points are exposed via the `etst::config`
struct (framework-agnostic) and `etst::doctest::config` (doctest-specific).
The previous weak-function approach (`ptr_board_init`, `ptr_after_cycle`,
`ptr_configure_context`) was removed in the same release.

What's still open is *zero-config* setup — generating the shim and
`main.cpp` automatically so consumers only need to add `lib_deps`. See
the postinstall-hook design in `NOTES-shim-dx-2026-04-27.md` (Strategy
C, candidate for v0.4.x).

### Include Path Rename (Done)

Renamed in v0.3.0 — clean break, no backward-compat aliases:

| Old | New |
|-----|-----|
| `#include <pio_test_runner/...>` | `#include <etst/...>` |
| `pio_test_runner::signal_sleep()` | `etst::signal_sleep()` |
| `ptr_doctest::config` | `etst::config` / `etst::doctest::config` |
| `ptr_doctest::run_tests()` | `etst::doctest::run_tests()` |
| `PtrTestListener` | `EtstDoctestListener` |

---

## Phase 1.5: Dependency Direction Fix (embedded-bridge)

Currently embedded-bridge's `MemoryTracker` has an inline copy of the ETST
protocol parser (prefix, regexes, CRC). This creates an upward dependency
from the generic library to the test runner protocol.

### Correct dependency direction

```
embedded-bridge          (generic receivers: MemoryTracker, CrashDetector)
    ↑                     format-agnostic, plain value APIs
embedded-test-runner     (ETST protocol → parses lines → feeds values)
    ↑                     owns protocol, adapts to generic receivers
consumer firmware        (emits ETST: markers via C++ headers)
```

### MemoryTracker API change

Current (protocol-aware):
```python
tracker.feed("ETST:MEM:BEFORE free=200000 min=180000 *XX")
```

Future (plain values):
```python
tracker.record_before(test_name="Suite/test1", free=200000, min_free=180000)
tracker.record_after(test_name="Suite/test1", free=199000, delta=-1000, min_free=179000)
```

The protocol parsing moves to embedded-test-runner's router, which calls these
methods after parsing `ETST:MEM:*` lines. embedded-bridge knows nothing
about `ETST:`, making it reusable for embedded-trace production profiling
with different wire formats.

---

## Phase 2: Host Abstraction (Python Side)

Decouple from PlatformIO's runner infrastructure:

```
etst/
  ├─ runner.py               orchestration (framework-agnostic)
  ├─ protocol.py             wire format, message builders
  ├─ hooks.py                phase transition hooks (extensible)
  │   ├─ PhaseHandler        base class with defaults
  │   ├─ on_sleep()          wait for port drop/reappear
  │   ├─ on_restart()        wait for reconnect
  │   └─ on_power_cycle()    user-provided: PPK2, USB hub, GPIO
  ├─ filters.py              framework-specific filter builders
  └─ results.py              plain Python result collection

etst.pio/                    PIO integration layer
  ├─ adapter.py              inherits TestRunnerBase
  └─ maps PIO options → runner config

etst.cli/                    (future: standalone CLI)
  └─ argparse for port, baud, filters
```

This enables:
- **Arduino IDE users**: `python -m etst --port /dev/ttyUSB0`
- **Zephyr users**: integrate via west extension or standalone script
- **CI/CD**: run without PIO installed
- **PIO users**: unchanged experience via `test_custom_runner.py`

### Phase Transition Hooks

Phase transitions are bilateral. The host needs hooks that mirror the
firmware's platform hooks:

```python
class PhaseHandler:
    """Override for your lab hardware setup."""

    def on_sleep(self, test_name, duration_ms):
        """Default: wait for USB-CDC port to disappear and reappear."""

    def on_restart(self, test_name):
        """Default: wait for port reconnect."""

    def on_power_cycle(self, test_name):
        """No default — user must implement for their hardware.
        Examples: PPK2 power toggle, USB hub port control, RPi GPIO."""
        raise NotImplementedError

    def on_firmware_update(self, test_name, binary_path):
        """Flash a new binary, wait for reboot."""
        raise NotImplementedError
```

### Filter Syntax Abstraction

The host translates from a generic filter format to framework-specific
syntax:

```python
class DoctestFilterBuilder:
    # RUN: --continue --tc "pattern" --ts "pattern"

class UnityFilterBuilder:
    # RUN: --test "pattern" --group "pattern"
```

---

## Phase 3: Bidirectional Data Channel

A general-purpose mechanism for firmware ↔ host data transfer during tests.

### Use Cases

1. **Cross-sleep state**: firmware sends state before sleep, host replays on
   wake. No RTC memory needed.
2. **Test artifacts**: firmware sends binary captures, sensor dumps, log
   snippets to host for post-test analysis or storage.
3. **Test data injection**: host sends calibration tables, test vectors, or
   config blobs to firmware before a test runs.
4. **Hybrid tests**: test logic split between firmware and host. Firmware
   produces artifacts, Python code on the host validates them (e.g. verify a
   binary capture's structure, check a sensor reading against a reference).

### Protocol Extension

```
ETST:DATA tag="cal_offset" size=4 base64=AAAEAA== *XX    # firmware → host
ETST:DATA tag="cal_offset" size=4 base64=AAAEAA== *XX    # host → firmware (replay)
```

### Firmware API (Sketch)

```cpp
// Send data to host (during test execution)
etst::send_data("cal_offset", &offset, sizeof(offset));

// Register a restore handler (called before test runs on wake)
etst::on_restore("cal_offset", [](const void* data, size_t len) {
    memcpy(&offset, data, len);
});
```

Per-test handlers mean no intermediate firmware storage — data flows directly
from the protocol stream into the test's variables.

---

## Phase 4: Platform Expansion

### Platform Abstraction (C++ Side)

Each target platform provides implementations for restart, sleep, heap
tracking, etc. via `etst::platform`:

```cpp
// etst/platform/esp32.h — provided by the library
namespace etst::platform::esp32 {
    void restart()     { esp_restart(); }
    void sleep()       { esp_deep_sleep_start(); }
    void lightsleep()  { esp_light_sleep_start(); }
    size_t free_heap() { return esp_get_free_heap_size(); }
}

// etst/platform/stm32.h — future
namespace etst::platform::stm32 {
    void restart()     { NVIC_SystemReset(); }
    void sleep()       { HAL_PWR_EnterSTANDBYMode(); }
}
```

The active platform is selected at compile time (build flag or auto-detect).
The orchestrator calls `etst::platform::restart()` etc. without knowing
which platform is active.

### Zephyr

Zephyr has its own test framework (`ztest`) with features that overlap:
test suites, setup/teardown, assertion macros.

**Investigation needed**: does ztest handle sleep/wake cycles, serial
disconnects, and multi-phase tests? If not, ETST's orchestration layer
adds value. A `ztest_adapter.h` would emit ETST markers from ztest's
reporter hooks.

### Catch2

Catch2 has `IStreamingReporter` which maps to ETST's listener pattern.
Filter syntax differs (Catch2 uses `[tags]` not `--tc`/`--ts`).

### Arduino IDE (No Build System)

With the host abstraction (Phase 2), users could:
1. Flash firmware via Arduino IDE
2. Run tests via `python -m etst --port COM3`

The firmware side already has zero PIO dependency — only the host side
needs the standalone CLI.

---

## What's Stable (This Release)

- **ETST wire protocol**: markers, CRC format, command structure
- **C++ marker API**: `signal_ready()`, `signal_done()`, `signal_sleep()`,
  `is_test_wake()`, memory markers
- **Environment variables**: `ETST_*` names (old `PTR_*` accepted with
  deprecation warning)
- **test_custom_runner.py shim**: copy-to-project bootstrapper for PIO

### What Will Change (Next Release)

- **`is_test_wake()`** → `is_continuation()` (multi-phase generalization)
- **`--wake` flag** → `--continue` (not sleep-specific)
- **Zero-config setup**: postinstall hook that auto-creates the
  `test/test_custom_runner.py` shim (Strategy C in
  `NOTES-shim-dx-2026-04-27.md`). Replaces the copy-this-file step.

### Already Shipped (v0.3.x)

What used to be Phase 2 / Phase 1 future work that has since landed:

- **Receiver plugin API + lifecycle hooks** (v0.3.0). Was implicit in
  the Phase 2 host abstraction; landed as `embedded_test_runner.receivers`
  setuptools entry-point group + `on_partition_start/complete` virtuals.
- **Test environment variables** (v0.3.0). The `ETST:ARGS` host→device
  message + `<etst/env.h>` firmware lookups + `require_env` doctest
  decorator. Not previously called out in the roadmap.
- **Config struct** (v0.3.0). Replaced the weak-function customization
  approach (`ptr_board_init` etc.) with `etst::config` /
  `etst::doctest::config`. See "Entry Point / main.cpp (Done)" above.
- **Package rename** (v0.3.0). `pio_test_runner` → `etst` (Python),
  `pio-test-runner` → `embedded-test-runner` (project / library.json),
  `<pio_test_runner/...>` → `<etst/...>` (include path), etc.
  Anticipated in "Include Path Rename (Done)" above.
- **Explicit dependency declarations** (v0.3.1). Both `pyproject.toml`
  and `library.json` now pin `embedded-bridge` to its git URL; fresh
  consumers no longer need to install it manually.
