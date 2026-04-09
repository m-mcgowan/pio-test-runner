# Roadmap

This document captures the forward direction for pio-test-runner: architectural
refactoring, new capabilities, and platform expansion. The goal is to evolve from
a PlatformIO-doctest-specific tool into a general-purpose embedded test
orchestration framework.

## Guiding Principles

- **DX stability**: the user-facing API should not change for existing users.
  Refactoring happens behind the current interface. If the DX has rough edges,
  fix those before stabilizing — breaking changes are cheaper now than later.
- **Protocol is the contract**: the PTR wire protocol (ETST:READY, ETST:DONE,
  ETST:TEST:START, etc.) is framework-agnostic by design. Any test framework
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
│  runner.py                  │   │  doctest_runner.h           │
│    ├─ orchestration         │   │    ├─ command parsing       │
│    ├─ sleep/wake mgmt       │   │    ├─ idle loop             │
│    ├─ filter building ◄─────┼───┼──► ├─ filter application    │
│    └─ result reporting      │   │    ├─ PtrTestListener       │
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

**What's coupled** (doctest-specific):
- `doctest_runner.h` — monolith mixing orchestration with doctest internals
- `runner.py` — filter syntax (`--tc`/`--ts`), base class, command format
- `ready_run_protocol.py` — assumes suite/name/timeout from TEST:START

**What's PlatformIO-specific** (host side only):
- `runner.py` inherits from PIO's `TestRunnerBase`
- Serial port discovery via `SerialPortFinder`
- Result reporting via PIO's `TestCase`/`TestStatus`
- Config from `platformio.ini`

---

## Phase 1: Framework Abstraction (C++ Side)

Split `doctest_runner.h` into layers:

```
test_orchestrator.h          (framework-agnostic)
  ├─ command parsing         RUN:, RESUME_AFTER:, LIST, --wake
  ├─ idle loop               READY/command/DONE cycle
  ├─ run_cycle()             calls adapter.run()
  └─ filter state            FilterState, count_passing_filters()

doctest_adapter.h            (doctest-specific)
  ├─ PtrTestListener         doctest::IReporter → PTR markers
  ├─ registry walking        getRegisteredTests()
  ├─ filter application      ctx.applyCommandLine(), modify_skip()
  └─ execution               context.run()

unity_adapter.h              (future)
  ├─ UnityReporter           Unity reporter → PTR markers
  ├─ test listing            Unity test registry
  └─ execution               UnityMain()
```

### Adapter Interface

```cpp
struct TestFrameworkAdapter {
    /// Return the number of registered tests.
    virtual unsigned total_tests() = 0;

    /// List all test names (for LIST command and RESUME_AFTER).
    virtual std::vector<const char*> test_names() = 0;

    /// Apply filters and return the number of tests that will run.
    /// Filters are passed as a tokenized arg list (e.g. ["--tc", "pattern"]).
    virtual unsigned apply_filters(const std::vector<String>& args) = 0;

    /// Skip to the test after `name` (for RESUME_AFTER). Return skip count.
    virtual int skip_to_after(const char* name) = 0;

    /// Execute tests. Returns number of failures.
    virtual int run() = 0;
};
```

### DX Impact: None

Users still write:
```cpp
#define DOCTEST_CONFIG_IMPLEMENT
#include <doctest.h>
#include <pio_test_runner/doctest_runner.h>

void setup() { DOCTEST_SETUP(); }
void loop()  { DOCTEST_LOOP(); }
```

The split is internal — `doctest_runner.h` includes `test_orchestrator.h` and
wires up `DoctestAdapter` automatically. Unity users would include
`unity_runner.h` instead.

### DX Issues to Fix Before Stabilizing

- `DOCTEST_SETUP()` / `DOCTEST_LOOP()` macros vs `ptr_doctest::run_tests()` /
  `ptr_doctest::idle_loop()` — the macros exist for brevity but the namespace
  functions are the real API. Pick one and deprecate the other.
- `ptr_doctest::config.configure_context` takes `doctest::Context&` — this
  leaks the framework into the user API. Consider whether this callback is
  necessary or if all use cases can be handled via env vars and build flags.
- The `ptr_doctest` namespace name is doctest-specific. Consider `ptr` as the
  namespace, with `ptr::config`, `ptr::run_tests()`, `ptr::idle_loop()`.

---

## Phase 2: Host Abstraction (Python Side)

Decouple from PlatformIO's runner infrastructure:

```
embedded_test_runner.py      (standalone, no PIO dependency)
  ├─ serial management       pyserial directly
  ├─ protocol handling       existing protocol.py
  ├─ orchestration           sleep/wake, RESUME_AFTER
  └─ result collection       plain Python dicts

pio_adapter.py               (PIO integration layer)
  ├─ inherits TestRunnerBase
  ├─ maps PIO options to runner config
  └─ reports results via TestCase/TestStatus

cli.py                       (future: standalone CLI)
  ├─ argparse for port, baud, filters
  └─ wraps embedded_test_runner
```

This enables:
- **Arduino IDE users**: run `python -m pio_test_runner --port /dev/ttyUSB0`
- **Zephyr users**: integrate via west extension or standalone script
- **CI/CD**: run without PIO installed
- **PIO users**: unchanged experience via `test_custom_runner.py`

### Filter Syntax Abstraction

Currently filters are doctest-specific (`--tc`, `--ts`). The host should
translate from a generic filter format to framework-specific syntax:

```python
class FilterBuilder:
    """Framework-specific filter command builder."""
    def build_run_command(self, tc=None, ts=None, tce=None, tse=None, wake=False) -> str:
        ...

class DoctestFilterBuilder(FilterBuilder):
    def build_run_command(self, **kwargs) -> str:
        # Returns: RUN: --wake --tc "pattern" --ts "pattern"

class UnityFilterBuilder(FilterBuilder):
    def build_run_command(self, **kwargs) -> str:
        # Returns: RUN: --test "pattern" --group "pattern"
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
pio_test_runner::send_data("cal_offset", &offset, sizeof(offset));

// Register a restore handler (called before test runs on wake)
pio_test_runner::on_restore("cal_offset", [](const void* data, size_t len) {
    memcpy(&offset, data, len);
});
```

Per-test handlers mean no intermediate firmware storage — data flows directly
from the protocol stream into the test's variables.

---

## Phase 4: Platform Expansion

### Zephyr

Zephyr has its own test framework (`ztest`) with features that overlap with ETST:
- Test suites and test cases
- Setup/teardown hooks
- Assertion macros

**Investigation needed**: does Zephyr's test infrastructure handle sleep/wake
cycles, serial disconnects, and multi-phase tests? If not, PTR's orchestration
layer adds value. A `ztest_adapter.h` would emit PTR markers from ztest's
reporter hooks.

### Catch2

Catch2 has `IStreamingReporter` which maps well to PTR's listener pattern.
A `catch_adapter.h` could emit PTR markers from Catch2's reporter events.
Filter syntax would differ (Catch2 uses `[tags]` not `--tc`/`--ts`).

### Arduino IDE (No Build System)

With the host abstraction (Phase 2), users could:
1. Flash firmware via Arduino IDE
2. Run tests via `python -m pio_test_runner --port COM3`

The firmware side already has zero PIO dependency — only the host side needs
the standalone CLI.

---

## What's NOT Changing

These are stable and will remain as-is:

- **PTR wire protocol**: markers, CRC format, command structure
- **C++ marker API**: `signal_ready()`, `signal_done()`, `signal_sleep()`,
  `is_test_wake()`, `print_test_start()`, memory markers
- **Environment variables**: `PTR_TEST_CASE`, `PTR_POST_TEST`, etc.
- **test_custom_runner.py shim**: copy-to-project bootstrapper for PIO
- **Weak function hooks**: `ptr_board_init()`, `ptr_after_cycle()`
