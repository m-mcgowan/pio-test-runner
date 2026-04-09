# pio-test-runner

PlatformIO test orchestration for embedded devices. Handles what `pio test` can't: devices that sleep, reset, disconnect, or crash during test execution.

See [docs/design.md](docs/design.md) for architecture and design.

## Installation

1. Add pio-test-runner to `lib_deps` for the C++ headers:

```ini
; platformio.ini
[env:my_board]
test_framework = custom
lib_deps =
    https://github.com/m-mcgowan/pio-test-runner.git
```

2. Copy [examples/test_custom_runner.py](examples/test_custom_runner.py) to `test/test_custom_runner.py`. The shim auto-installs the Python packages (`pio-test-runner` and [embedded-bridge](https://github.com/m-mcgowan/embedded-bridge)) from GitHub on first run.

3. Run tests with `pio test`.

Set `PIO_TEST_RUNNER_NO_AUTO_INSTALL=1` to disable auto-installation (e.g. when using editable installs for development).

## Test Filtering

Runtime test filtering for embedded targets uses the same `-a` flags as native doctest tests:

```bash
# Filter by test suite
pio test -e my_board -a '--ts *SensorTests*'

# Filter by test case
pio test -e my_board -a '--tc *timeout*'

# Exclude a suite
pio test -e my_board -a '--tse *slow*'

# Combine filters
pio test -e my_board -a '--ts *Network*' -a '--tce *stress*'
```

> **Shell quoting:** Always quote filter patterns to prevent shell glob expansion. Single quotes are safest (`'*pattern*'`). Double quotes also work but require care with special characters. Without quotes, the shell may expand `*` against filenames in the current directory.

Alternatively, set environment variables (no quoting issues — the shell doesn't expand inside variable values):

```bash
PTR_TEST_SUITE='*SensorTests*' pio test -e my_board
```

### Supported filters

| Flag | Env var | Description |
|------|---------|-------------|
| `--ts` | `PTR_TEST_SUITE` | Run only suites matching pattern |
| `--tc` | `PTR_TEST_CASE` | Run only cases matching pattern |
| `--tse` | `PTR_TEST_SUITE_EXCLUDE` | Exclude suites matching pattern |
| `--tce` | `PTR_TEST_CASE_EXCLUDE` | Exclude cases matching pattern |
| `--no-skip` | `PTR_NO_SKIP` | Run all tests including `skip()`-decorated ones |
| `--unskip-tc` | `PTR_UNSKIP_TEST_CASE` | Clear `skip()` on matching test cases |
| `--unskip-ts` | `PTR_UNSKIP_TEST_SUITE` | Clear `skip()` on matching test suites |
| `--skip-tc` | `PTR_SKIP_TEST_CASE` | Force-skip matching test cases |
| `--skip-ts` | `PTR_SKIP_TEST_SUITE` | Force-skip matching test suites |

Patterns support `*` wildcards (doctest globbing). Filters from `-a` and environment variables are combined. All doctest native flags (`--no-skip`, comma-separated patterns, etc.) are passed through via `applyCommandLine()`.

### Filtering notes

- **`--ts` excludes tests without a suite.** The `--ts` filter matches against the `TEST_SUITE` name. Tests not wrapped in a `TEST_SUITE()` block have an empty suite name and are excluded by any `--ts` pattern (they won't match `*Foo*`). Conversely, `--tse` won't exclude them either. Put all tests in a `TEST_SUITE()` for predictable filtering.
- **No-match filters succeed silently.** If a filter matches zero tests, the run completes with 0 tests and no error. There is no "filter matched nothing" warning. Double-check your patterns if you get an unexpectedly empty run.
- **Multiple exclude flags.** Use comma-separated patterns within a single flag (`--tse *Foo*,*Bar*`) rather than repeating the flag (`--tse *Foo* --tse *Bar*`). Repeated flags may only apply the last value.

### Skip control

Tests decorated with `doctest::skip()` are skipped by default. Use `--unskip-tc` to selectively enable specific skipped tests without affecting others:

```bash
# Run a specific skip-decorated crash test
PTR_UNSKIP_TEST_CASE="*TWDT*fires*" PTR_TEST_SUITE="*Service/WDT*" \
    pio test -e my_board

# Force-skip slow tests without modifying source
PTR_SKIP_TEST_CASE="*stress*" pio test -e my_board

# Unskip via -a flag
pio test -e my_board -a "--unskip-tc *crash_test*" -a "--ts *WDT*"
```

The `--unskip-tc`/`--unskip-ts` and `--skip-tc`/`--skip-ts` flags modify the doctest test registry (`m_skip` flag) before the filter chain runs. This means they compose with `--tc`/`--ts` filters: unskip a test, then use `--ts` to restrict which suite runs it in.

Flags are processed left-to-right, so later flags override earlier ones on the same test: `--skip-tc *foo* --unskip-tc *foo*` leaves foo unskipped.

## Configuration

The firmware-side doctest runner (`doctest_runner.h`) supports these configuration macros. Define them before including the header:

```cpp
#define DOCTEST_CONFIG_IMPLEMENT
#include <doctest.h>

// Optional: board-specific initialization
static bool my_board_init(Print& log) {
    // mount filesystem, detect board revision, etc.
    return true;  // false halts tests
}
#define PTR_BOARD_INIT my_board_init

// Optional: configure doctest context before test execution
static void my_configure(doctest::Context& ctx) {
    ctx.setOption("order-by", "name");
}
#define PTR_CONFIGURE_CONTEXT my_configure

#include <pio_test_runner/doctest_runner.h>

void setup() { DOCTEST_SETUP(); }
void loop()  { DOCTEST_LOOP(); }
```

| Macro | Type | Default | Description |
|-------|------|---------|-------------|
| `PTR_BOARD_INIT` | `bool fn(Print&)` | none | Board setup before tests. Return false to halt. |
| `PTR_CONFIGURE_CONTEXT` | `void fn(doctest::Context&)` | none | Configure doctest context before each cycle. |
| `PTR_AFTER_CYCLE` | `void fn()` | none | Called after each test cycle completes. |
| `PTR_READY_TIMEOUT_MS` | `uint32_t` | `0` (forever) | Max time to wait for host runner. Set to e.g. `30000` for standalone operation without a host. |
| `PTR_PLATFORM_RESTART` | `void fn()` | `esp_restart()` | Platform-specific restart. |
| `PTR_PLATFORM_SLEEP` | `void fn()` | `esp_deep_sleep_start()` | Platform-specific deep sleep. |
| `PTR_PLATFORM_LIGHTSLEEP` | `void fn()` | `esp_light_sleep_start()` | Platform-specific light sleep (should return on wake). |

### Host-side environment variables

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `PTR_POST_TEST` | `sleep`, `lightsleep`, `restart`, `wait`, `none` | `sleep` | Command sent after tests complete. `sleep` = deep sleep (saves battery, port disappears). `lightsleep` = light sleep (low power, port stays alive). `restart` = reboot (immediately available). `wait` = idle loop (fully active). `none` = close without command. |
| `PTR_RESUME_AFTER` | test name | none | Skip all tests up to and including the named test, then run the rest. Useful for resuming after a failure. Combines with filters (`PTR_TEST_SUITE`, etc.) which apply to the remaining tests. |

## Writing Deep Sleep Tests

Tests can trigger deep sleep mid-execution. The runner handles the sleep/wake cycle automatically:

1. **Phase 1** (first boot): test runs pre-sleep checks, calls `signal_sleep()`, enters deep sleep
2. **Phase 2** (after wake): runner reconnects, sends the test name — test detects wake via `is_test_wake()` and verifies post-sleep state
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
- `pio_test_runner::signal_sleep(ms)` — emit `ETST:SLEEP ms=N` so the runner knows to wait and reconnect
- `pio_test_runner::is_test_wake()` — returns true if this boot is a wake from `signal_sleep()` (uses RTC memory)
- Always use `doctest::timeout(N)` on sleep tests to prevent hangs

**Why use deep sleep tests?** Deep sleep resets the heap entirely. In large test suites (700+ tests), heap fragmentation accumulates across tests. A deep sleep test mid-suite acts as a clean reset point, preventing out-of-memory failures in later tests.

## Status

| Feature | Design | Docs | Impl | Tests | Examples | Since | Updated |
|---------|--------|------|------|-------|----------|-------|---------|
| **Test runner plugin** | [design.md](docs/design.md) | [design.md](docs/design.md) | [runner.py](src/pio_test_runner/runner.py) | [test_runner](tests/test_runner.py) | [example](examples/test_custom_runner.py) | | |
| **PTR protocol** | [design.md](docs/design.md) | [design.md](docs/design.md) | [protocol.py](src/pio_test_runner/protocol.py) | [test_protocol](tests/test_protocol.py) | | | |
| **Ready/run protocol** | [design.md](docs/design.md) | [design.md](docs/design.md) | [ready_run_protocol.py](src/pio_test_runner/ready_run_protocol.py) | [test_ready_run](tests/test_ready_run_protocol.py) | | | |
| **Test result receiver** | [design.md](docs/design.md) | [design.md](docs/design.md) | [result_receiver.py](src/pio_test_runner/result_receiver.py) | [test_result](tests/test_result_receiver.py) | | | |
| **Doctest parser** | | | [robust_doctest_parser.py](src/pio_test_runner/robust_doctest_parser.py) | [test_doctest](tests/test_robust_doctest_parser.py) | | | |
| **Timing tracker** | | | [timing_tracker.py](src/pio_test_runner/timing_tracker.py) | [test_timing](tests/test_timing_tracker.py) | | | |
| **Disconnect handler** | [design.md](docs/design.md) | [design.md](docs/design.md) | [disconnect.py](src/pio_test_runner/disconnect.py) | [test_disconnect](tests/test_disconnect.py) | | | |
| **Firmware library** | | | [doctest_runner.h](include/pio_test_runner/doctest_runner.h) | [integration](tests/integration/) | | | |
