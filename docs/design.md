# embedded-test-runner — Design

PlatformIO test orchestration for embedded devices. Handles what
`pio test` can't: devices that sleep, reset, disconnect, or crash
during test execution.

## Motivation

PlatformIO's built-in test runner assumes a stable serial connection
from upload through test completion. Real embedded testing breaks this
assumption constantly:

- **Deep sleep** — the device enters deep sleep mid-test; USB-CDC
  disappears; PIO declares the test failed
- **Reset** — a watchdog reset or deliberate reboot loses the serial
  connection; PIO can't recover
- **Long operations** — a GPS fix or cellular connection takes minutes;
  PIO times out
- **Crashes** — a backtrace scrolls past; PIO doesn't distinguish
  "crash" from "test output"

embedded-test-runner extracts these patterns into a standalone PlatformIO
plugin with reusable firmware headers.

## Architecture

```
Host (Python)                          Device (C++ firmware)
─────────────                          ────────────────────
EmbeddedTestRunner                     etst/doctest/runner.h
  ├─ ReadyRunProtocol                    ├─ wait_for_command()
  │    state machine:                    │    sends ETST:READY
  │    READY→RUN→DONE                    │    receives RUN:/RUN_ALL
  ├─ CrashDetector                       ├─ run_cycle()
  │    backtrace, WDT, panic             │    apply filters
  ├─ MemoryTracker                       │    modify_skip (unskip/skip)
  │    ETST:MEM:BEFORE/AFTER              │    context.run()
  ├─ TimingTracker                       │    signal_done()
  │    ETST:CASE:START                    ├─ idle_loop()
  ├─ RobustDoctestParser                 │    SLEEP/RESTART/re-run
  │    doctest output → results          └─ etst/test_runner.h
  └─ DisconnectHandler                       ETST: protocol emit helpers
       ETST:DISCONNECT/RECONNECT
```

### How it works with PlatformIO

PlatformIO manages build/upload. The runner is selected via:

```ini
[env:esp32s3]
test_framework = custom
lib_deps =
    https://github.com/m-mcgowan/embedded-test-runner.git
```

A `test_custom_runner.py` shim imports `EmbeddedTestRunner` and PIO
calls its `stage_testing()` method. The runner opens the serial port,
runs the READY/RUN/DONE handshake, processes output through the
receiver pipeline, and reports results back to PIO.

### Where embedded-bridge fits

The runner uses **embedded-bridge** for:
- CRC-8 checksums on protocol lines (transport integrity)
- `Router` for dispatching serial lines to multiple receivers
- `CrashDetector` patterns (backtrace, guru meditation, WDT, abort)

The runner does NOT create a `Bridge` instance — PIO owns the serial
connection (or the runner opens it directly for the custom framework).

## Core Components

### ETST Protocol (`protocol.h`, `protocol.py`)

All protocol messages use the `ETST:` prefix with CRC-8 checksums.
The firmware emits via `etst::emit()`, the host validates
via `validate_crc()`.

| Message | Direction | Purpose |
|---------|-----------|---------|
| `ETST:READY` | Device→Host | Device ready for commands |
| `RUN_ALL` | Host→Device | Run all tests |
| `RUN: <flags>` | Host→Device | Run with filters |
| `RESUME_AFTER: <name>` | Host→Device | Skip tests up to name |
| `ETST:COUNTS total=N skip=N run=N` | Device→Host | Test count before execution |
| `ETST:CASE:START suite=".." name=".."` | Device→Host | Test timing marker |
| `ETST:MEM:BEFORE free=N min=N largest=N` | Device→Host | Heap before test |
| `ETST:MEM:AFTER free=N delta=N min=N largest=N` | Device→Host | Heap after test |
| `ETST:DONE` | Device→Host | All tests complete |
| `ETST:SLEEP ms=N` | Device→Host | Entering deep sleep |
| `ETST:RESTART` | Device→Host | Software restart imminent |
| `ETST:BUSY ms=N` | Device→Host | Busy, extend hang timeout |
| `ETST:DISCONNECT ms=N` | Device→Host | Serial going away |
| `ETST:RECONNECT` | Device→Host | Serial restored |
| `SLEEP` | Host→Device | Enter deep sleep (idle) |
| `RESTART` | Host→Device | Restart device (idle) |
| `LIST` | Host→Device | List registered tests |

### ReadyRunProtocol (`ready_run_protocol.py`)

State machine for the READY/RUN/DONE handshake:

1. Device boots, sends `ETST:READY` periodically
2. Host sends `RUN_ALL`, `RUN: <filters>`, or `RESUME_AFTER: <name>`
3. Device runs tests, may emit `ETST:SLEEP` for deep sleep
4. Device sends `ETST:DONE` when finished

The state machine handles:
- CRC validation on host→device commands
- Garbage byte stripping (USB-CDC DTR assertion noise)
- Timeout detection with configurable hang threshold
- SLEEP sentinel detection + device reconnection

### EmbeddedTestRunner (`runner.py`)

PlatformIO test runner plugin. Key methods:

- `stage_testing()` — main entry: opens serial, runs test cycles,
  handles sleep/wake loops, reports results
- `_build_initial_command()` — combines `-a` program args with
  `ETST_*` environment variables into a `RUN:` command
- `_run_test_cycle()` — single READY→RUN→DONE cycle with crash
  detection and hang monitoring

### DisconnectHandler (`disconnect.py`)

Manages disconnect/reconnect windows for devices that sleep, reset,
or reconfigure during tests. The firmware controls the timing:

```cpp
etst::request_disconnect(5000);  // going away for 5s
Serial.end();
// ... sleep / reset / reflash ...
Serial.begin(115200);
etst::signal_reconnect();        // back
```

### CrashDetector (from embedded-bridge)

Detects device crashes from serial output patterns:
- `Backtrace:` — ESP32 backtrace
- `Guru Meditation` — ESP32 panic
- `abort()` / `assert failed`
- `E (NNNN) task_wdt:` — Task watchdog timeout
- `Rebooting...` — Post-crash reboot

### Doctest Runner (`etst/doctest/runner.h`)

Firmware-side test harness for doctest. Provides:

- `DOCTEST_SETUP()` / `DOCTEST_LOOP()` — call from Arduino setup/loop
- `EtstDoctestListener` — doctest reporter emitting ETST markers
- `wait_for_command()` — READY/RUN handshake with CRC validation
- `run_cycle()` — apply filters, run tests, signal done
- `idle_loop()` — post-test command loop (SLEEP, RESTART, re-run)

**Configuration:**

Framework-agnostic (`etst::config`):

| Field | Signature | Purpose |
|-------|-----------|---------|
| `board_init` | `bool fn(Print&)` | Board setup before tests (return false to halt) |
| `after_cycle` | `void fn()` | Called after each test cycle completes |
| `ready_timeout_ms` | `uint32_t` | Max wait for host (default: 0 = forever) |
| `platform_restart` | `void fn()` | Custom restart (default: `esp_restart()`) |
| `platform_sleep` | `void fn()` | Custom deep sleep (default: `esp_deep_sleep_start()`) |
| `platform_lightsleep` | `void fn()` | Custom light sleep (default: `esp_light_sleep_start()`) |

Doctest-specific (`etst::doctest::config`):

| Field | Signature | Purpose |
|-------|-----------|---------|
| `configure` | `void fn(doctest::Context&)` | Configure doctest context before run |

### Test Filtering

Two-phase filter processing in `apply_run_filters()`:

1. **ETST-specific flags** (`--unskip-tc`, `--skip-tc`, etc.) modify
   `m_skip` on the doctest test registry. Processed left-to-right
   so later flags override earlier ones.
2. **Remaining flags** passed to `context.applyCommandLine()` for
   doctest's native filter processing (`--tc`, `--ts`, `--tce`,
   `--tse`, `--no-skip`, comma-separated patterns, etc.).

Compile-time filters (`TEST_FILTER_SUITE`, etc.) are applied first
and compose additively with runtime filters.

### Sleep/Wake Orchestration

When a test enters deep sleep:

1. **First cycle**: `RUN_ALL` — tests run until one calls
   `signal_sleep()`.
2. **Sleep resume**: Host waits, reconnects, sends
   `RUN: --tc "<sleeping_test>"` — runs Phase 2 only.
3. **Remaining cycle**: `RESUME_AFTER: <sleeping_test>` — device
   uses doctest's `first` option to skip past completed tests.
4. **Repeat**: If another test sleeps during step 3, the loop
   continues.

## Project Structure

```
embedded-test-runner/
├── pyproject.toml               # Python package config (setuptools_scm)
├── library.json                 # PlatformIO library metadata
├── LICENSE
├── README.md
├── CHANGELOG.md
├── docs/
│   └── design.md                # this file
├── include/
│   └── etst/
│       ├── protocol.h           # CRC-8 wire format, emit() helper
│       ├── test_runner.h        # firmware protocol API (disconnect, sleep, memory)
│       └── doctest/
│           └── runner.h         # doctest integration (filters, READY/RUN, idle loop)
├── src/
│   └── etst/
│       ├── __init__.py          # exports EmbeddedTestRunner
│       ├── runner.py            # PIO plugin: EmbeddedTestRunner
│       ├── protocol.py          # CRC-8 format/validate, line parsing
│       ├── ready_run_protocol.py # READY/RUN/DONE state machine
│       ├── disconnect.py        # DisconnectHandler
│       ├── result_receiver.py   # TestResultReceiver (multi-framework)
│       ├── robust_doctest_parser.py  # fixes PIO doctest parser crash
│       └── timing_tracker.py    # per-test duration + slow test report
├── examples/
│   └── test_custom_runner.py    # copy to project; auto-installs deps
├── scripts/
│   └── release.sh               # version bump, tag, push, GH release
└── tests/
    ├── conftest.py              # PIO mock infrastructure
    ├── test_runner.py           # EmbeddedTestRunner tests
    ├── test_protocol.py         # CRC-8 format/validate tests
    ├── test_ready_run_protocol.py
    ├── test_result_receiver.py
    ├── test_robust_doctest_parser.py
    ├── test_timing_tracker.py
    ├── test_disconnect.py
    ├── test_skip_control.py     # env var + command building tests
    ├── test_doctest_internals.cpp  # native C++ tests (glob, tokenize, modify_skip)
    └── integration/             # on-device ESP32-S3 test project
        ├── platformio.ini
        ├── test/
        │   ├── main.cpp
        │   ├── test_custom_runner.py
        │   ├── test_protocol.cpp
        │   ├── test_memory_tracking.cpp
        │   ├── test_timing.cpp
        │   ├── test_skip_control.cpp
        │   └── test_z_deep_sleep.cpp
        └── boards/
            └── esp32s3.ini
```

## Dependencies

**Runtime (Python):**
- `embedded-bridge` — CRC-8, crash detection, message routing

**Runtime (C++):**
- `doctest` — test framework (provided by consumer project)
- Arduino framework — Serial, GPIO, delay

**Optional:**
- `platformio` — only needed when used as a PIO test runner plugin.
  Graceful ImportError fallback allows standalone use.
- `click` — colored output (falls back to plain print)
