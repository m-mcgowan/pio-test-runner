# pio-test-runner

Test orchestration for embedded [doctest](https://github.com/doctest/doctest) suites running on ESP32 with PlatformIO.

Writing embedded tests is usually fairly straightforward. Running them reliably is the hard part — the serial port vanishes during deep sleep, firmware reboots mid-test to verify wake behavior, and large test suites fragment the heap so later tests that work in isolation suddenly crash. `pio-test-runner` handles the hard part: it reconnects after sleep, resumes where it left off, tracks per-test memory, and catches crashes and hangs — so you write tests, not infrastructure.

- Reconnects automatically after deep sleep, restart, or USB disconnect
- Hang detection with configurable timeouts
- Multi-phase tests across sleep/wake cycles (sleep → wake → verify → resume remaining)
- Per-test memory tracking with leak detection
- Per-test timing with slow test reporting
- Crash detection (guru meditation, stack trace, watchdog)
- Runtime test filtering without rebuilding firmware

Currently supports **doctest** on **ESP32** via **PlatformIO**, with a [roadmap](docs/ROADMAP.md) toward additional frameworks (Unity, Catch2), platforms (Zephyr), and standalone operation.

## Quick Start

**1.** Add to `platformio.ini`:

```ini
[env:my_board]
test_framework = custom
build_flags =
    -DDOCTEST_CONFIG_SUPER_FAST_ASSERTS   ; reduces flash usage
    -DDOCTEST_CONFIG_NO_POSIX_SIGNALS     ; ESP32 has no POSIX signals
    -DDOCTEST_THREAD_LOCAL=               ; newlib thread_local workaround
lib_deps =
    https://github.com/m-mcgowan/pio-test-runner.git
    doctest/doctest@^2.4.11
```

See [tests/integration/platformio.ini](tests/integration/platformio.ini) for a complete working example with board config, shared sections, and multi-board support.

**2.** Copy [examples/test_custom_runner.py](examples/test_custom_runner.py) to `test/test_custom_runner.py`. This shim auto-installs the Python packages on first `pio test` — no pip install needed.

**3.** Create `test/main.cpp` (entry point for the test firmware):

```cpp
#define DOCTEST_CONFIG_IMPLEMENT
#include <doctest.h>
#include <pio_test_runner/doctest_runner.h>

void setup() { ptr_doctest::run_tests(); }
void loop()  { ptr_doctest::idle_loop(); }
```

**4.** Write tests:

```cpp
// test/test_example.cpp
#include <doctest.h>

TEST_SUITE("MyTests") {
TEST_CASE("basic check") {
    CHECK(1 + 1 == 2);
}
}
```

**5.** Run:

```bash
pio test -e my_board
```

## Test Filtering

Runtime test filtering uses the same `-a` flags as native doctest:

```bash
pio test -e my_board -a '--ts *SensorTests*'     # filter by suite
pio test -e my_board -a '--tc *timeout*'          # filter by case
pio test -e my_board -a '--tse *slow*'            # exclude a suite
pio test -e my_board -a '--ts *Net*' -a '--tce *stress*'  # combine
```

> **Shell quoting:** Always quote filter patterns to prevent shell glob expansion. Single quotes are safest (`'*pattern*'`).

Or set environment variables:

```bash
ETST_SUITE='*SensorTests*' pio test -e my_board
```

### Supported filters

| Flag | Env var | Description |
|------|---------|-------------|
| `--ts` | `ETST_SUITE` | Run only suites matching pattern |
| `--tc` | `ETST_CASE` | Run only cases matching pattern |
| `--tse` | `ETST_SUITE_EXCLUDE` | Exclude suites matching pattern |
| `--tce` | `ETST_CASE_EXCLUDE` | Exclude cases matching pattern |
| `--no-skip` | `ETST_NO_SKIP` | Run all tests including `skip()`-decorated ones |
| `--unskip-tc` | `ETST_UNSKIP_CASE` | Clear `skip()` on matching test cases |
| `--unskip-ts` | `ETST_UNSKIP_SUITE` | Clear `skip()` on matching test suites |
| `--skip-tc` | `ETST_SKIP_CASE` | Force-skip matching test cases |
| `--skip-ts` | `ETST_SKIP_SUITE` | Force-skip matching test suites |

Patterns support `*` wildcards (doctest globbing). Filters from `-a` and environment variables are combined.

> **Note:** The old `PTR_*` environment variable names still work but log a deprecation warning. Migrate to `ETST_*` when convenient.

### Multiple patterns and quoting

Use **commas** to match multiple patterns in a single flag — this works for all filter types:

```bash
# Multiple test cases
pio test -e my_board -a '--tc *timeout*,*retry*,*reconnect*'

# Multiple suite excludes
pio test -e my_board -a '--tse *Stress*,*Slow*,*DeepSleep*'

# Via environment variable
ETST_CASE='*timeout*,*retry*' pio test -e my_board
```

Use **quotes** for test names containing spaces:

```bash
# -a flag: single quotes for shell, double quotes for the firmware tokenizer
pio test -e my_board -a '--tc "Arduino millis is running"'

# Environment variable: no inner quotes needed
ETST_CASE='Arduino millis is running' pio test -e my_board
```

> **Shell quoting:** Always quote `-a` values to prevent shell glob expansion. Single quotes are safest (`'*pattern*'`). Without quotes, the shell may expand `*` against filenames in the current directory.

### Filtering notes

- **`--ts` (test suite filer) excludes tests without a suite.** Tests not in a `TEST_SUITE()` block have an empty suite name and won't match any `--ts` pattern. Put all tests in a `TEST_SUITE()` for predictable filtering.
- **No-match filters succeed silently.** A filter matching zero tests completes with 0 tests and no error. Double-check patterns if you get an unexpectedly empty run.
- **Don't repeat flags.** Use comma-separated patterns (`--tse *Foo*,*Bar*`) rather than repeating the flag (don't write `--tse *Foo* --tse *Bar*`). Repeated flags may only apply the last value.

### Skip control

Tests decorated with `doctest::skip()` are skipped by default. Use `--unskip-tc` to selectively enable them:

```bash
# Run a specific skip-decorated test
ETST_UNSKIP_CASE='*TWDT*fires*' ETST_SUITE='*Service/WDT*' \
    pio test -e my_board

# Force-skip slow tests without modifying source
ETST_SKIP_CASE='*stress*' pio test -e my_board
```

Flags are processed left-to-right: `--skip-tc *foo* --unskip-tc *foo*` leaves foo unskipped.

## Writing Deep Sleep Tests

Tests can trigger deep sleep mid-execution. The runner handles the cycle automatically:

1. **Phase 1** (first boot): test runs pre-sleep checks, calls `signal_sleep()`, enters deep sleep
2. **Phase 2** (after wake): runner reconnects, sends the test name with `--wake` — test detects wake via `is_test_wake()` and verifies post-sleep state
3. **Remaining tests**: runner sends `RESUME_AFTER` to skip past the completed test and run the rest

```cpp
#include <doctest.h>
#include <esp_sleep.h>
#include <pio_test_runner/test_runner.h>

TEST_SUITE("MyTests") {

TEST_CASE("survives deep sleep" * doctest::timeout(30)) {
    if (pio_test_runner::is_test_wake()) {
        // Phase 2: woke from deep sleep — verify wake cause
        CHECK(esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER);
    } else {
        // Phase 1: pre-sleep checks, then sleep
        CHECK(some_pre_sleep_condition());

        pio_test_runner::signal_sleep(3000);  // tell runner: sleeping for 3s
        Serial.flush();
        delay(100);

        esp_sleep_enable_timer_wakeup(3 * 1000000ULL);
        esp_deep_sleep_start();
        // never reached — device resets on wake
    }
}

}
```

**Key APIs:**
- `signal_sleep(ms)` — emit `ETST:SLEEP` so the runner knows to wait and reconnect
- `is_test_wake()` — returns true when the host sent `--wake` (Phase 2 cycle)
- Always use `doctest::timeout(N)` on sleep tests to prevent hangs

**Why deep sleep tests?** Deep sleep resets the heap entirely. In large test suites (700+ tests), heap fragmentation accumulates. A deep sleep test mid-suite acts as a clean reset point.

## Configuration

### Firmware callbacks

Set callbacks on `ptr_doctest::config` before calling `run_tests()`:

```cpp
#define DOCTEST_CONFIG_IMPLEMENT
#include <doctest.h>
#include <pio_test_runner/doctest_runner.h>

static bool my_board_init(Print& log) {
    // mount filesystem, detect board revision, etc.
    return true;  // false halts tests
}

void setup() {
    ptr_doctest::config.board_init = my_board_init;
    ptr_doctest::config.ready_timeout_ms = 30000;  // standalone mode
    ptr_doctest::run_tests();
}

void loop() {
    ptr_doctest::idle_loop();
}
```

| Callback | Signature | Description |
|----------|-----------|-------------|
| `board_init` | `bool fn(Print&)` | Board setup before tests. Return false to halt. |
| `configure_context` | `void fn(doctest::Context&)` | Configure doctest options before each cycle. |
| `after_cycle` | `void fn()` | Called after each test cycle completes. |
| `ready_timeout_ms` | `uint32_t` | Max wait for host (0 = forever). Set for standalone operation. |
| `platform_restart` | `void fn()` | Custom restart (default: `esp_restart()`). |
| `platform_sleep` | `void fn()` | Custom deep sleep (default: `esp_deep_sleep_start()`). |
| `platform_lightsleep` | `void fn()` | Custom light sleep (default: `esp_light_sleep_start()`). |

### Host-side environment variables

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `ETST_ON_DONE` | `wait`, `sleep`, `lightsleep`, `restart`, `none` | `wait` | Action when tests complete. `wait` = idle loop (stays online). `sleep` = deep sleep (saves battery, USB disappears). `restart` = reboot. `none` = close serial. |
| `ETST_RESUME_AFTER` | test name | none | Skip tests up to named test, run rest. |
| `ETST_HANG_TIMEOUT` | seconds | `30` | No-output duration before declaring a hang. |


## Hang Detection

If a test produces no serial output for longer than the hang timeout, the runner aborts it and reports an error. This catches firmware deadlocks, blocked I/O, and infinite loops.

**Default timeout:** 30 seconds. Override globally:

```bash
ETST_HANG_TIMEOUT=120 pio test -e my_board
```

**Per-test timeout:** Use `doctest::timeout(N)` to set a per-test limit. This takes precedence over the global timeout:

```cpp
TEST_CASE("slow but bounded" * doctest::timeout(60)) {
    // runner allows 60s of silence for this test
    do_lengthy_operation();
    CHECK(result_ok());
}
```

**Busy signal:** If a test needs to go silent for a known duration (firmware update, filesystem format, long I/O), call `signal_busy()` to extend the timeout:

```cpp
TEST_CASE("formats storage") {
    pio_test_runner::signal_busy(45000);  // 45s — don't kill me
    format_filesystem();                   // no serial output during this
    CHECK(fs_mounted());
}
```

The runner starts checking for hangs once the first test begins (not during boot).

## Documentation

- [Architecture & Design](docs/design.md) — protocol, components, deep sleep orchestration
- [Roadmap](docs/ROADMAP.md) — framework abstraction, platform expansion, data channel
