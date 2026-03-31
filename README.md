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
pio test -e my_board -a "--ts *SensorTests*"

# Filter by test case
pio test -e my_board -a "--tc *timeout*"

# Exclude a suite
pio test -e my_board -a "--tse *slow*"

# Combine filters
pio test -e my_board -a "--ts *Network*" -a "--tce *stress*"
```

Alternatively, set environment variables:

```bash
PTR_TEST_SUITE="*SensorTests*" pio test -e my_board
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
