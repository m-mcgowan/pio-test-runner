# Changelog

All notable changes to this project will be documented in this file.
Follows [Keep a Changelog](https://keepachangelog.com/) conventions.

## [Unreleased]

## [0.2.0] — 2026-03-31

### Added
- **Runtime skip control** — `--unskip-tc`, `--unskip-ts`, `--skip-tc`,
  `--skip-ts` flags modify doctest's `m_skip` on the test registry before
  the filter chain runs. Compose with `--tc`/`--ts` for selective unskipping
  of crash/stress tests without running all skipped tests.
  Environment variables: `PTR_UNSKIP_TEST_CASE`, `PTR_UNSKIP_TEST_SUITE`,
  `PTR_SKIP_TEST_CASE`, `PTR_SKIP_TEST_SUITE`.
- **`--no-skip` passthrough** — doctest's global `--no-skip` flag now works
  via `-a "--no-skip"` or `PTR_NO_SKIP=1` environment variable.
- **Full doctest CLI passthrough** — `apply_run_filters()` now tokenizes
  the RUN command body into argv and passes it to `applyCommandLine()`,
  supporting all doctest native flags (comma-separated patterns, multiple
  instances of the same flag, `--no-skip`, etc.).
- **`PTR_CONFIGURE_CONTEXT` hook** — define as a function
  `void fn(doctest::Context&)` to configure the context before test execution
  (e.g., set custom options, add filters).
- **`PTR:BUSY` protocol message** — firmware signals it will be busy for
  a specified duration; host extends hang timeout accordingly.
- **`PTR:RESTART` protocol message** — firmware signals an imminent software
  restart; host handles reconnection like a sleep cycle.
- **Per-test timeout annotation** — `PTR:TEST:START` now includes
  `timeout=N` from `doctest::timeout(N)` decorators for host-side
  enforcement.
- **Test count reporting** — `PTR:TESTS total=N skip=N run=N` emitted
  before test execution begins.
- **SLEEP command** — host sends SLEEP after test completion to prevent
  battery drain on idle devices.
- **Largest contiguous block** — `PTR:MEM:BEFORE/AFTER` markers now include
  `largest=N` (largest free heap block) on ESP-IDF builds.
- **`PTR_AFTER_CYCLE` hook** — called after each test cycle completes.

### Changed
- **Default READY timeout**: Device now waits indefinitely for runner
  instead of timing out after 5s. The old timeout predated pio-test-runner
  and caused race conditions with USB-CDC reconnection after upload.
  Set `PTR_READY_TIMEOUT_MS` to restore a finite timeout.
- **Skip control flags processed left-to-right** — later flags override
  earlier ones on the same test (`--skip-tc *foo* --unskip-tc *foo*` leaves
  foo unskipped).

### Removed
- **`pio_test_runner::wait_for_command()`** — legacy function in
  `test_runner.h` without CRC validation or READY signalling. Use
  `ptr_doctest::wait_for_command()` from `doctest_runner.h` instead.

### Fixed
- **`wait_for_command(0)`**: Now correctly waits forever instead of returning
  immediately (was: `millis() < millis()` = false)
- **`print_mem_after`**: Use `#else` instead of `return` before `#endif`

## [0.1.1] — 2026-03-18

### Added
- **Runtime test filtering** — firmware-side `apply_run_filters()` parses
  `--tc`/`--ts`/`--tce`/`--tse` flags from the `RUN:` command, enabling
  `run_tests.sh` to filter by suite or case name without rebuilding
- Backwards compatible: bare pattern (e.g. `RUN: *foo*`) still works as
  a test-case filter

## [0.1.0] — 2026-03-17

First release. PlatformIO test runner with crash detection, deep sleep
orchestration, and auto-install from GitHub.

### Added
- **EmbeddedTestRunner** — PlatformIO test runner plugin with crash
  detection, disconnect handling, and framework-agnostic result parsing
  via embedded-bridge receivers
- **PTR protocol** — bidirectional test protocol with CRC-8 checksums,
  repeated READY handshake, and host→device CRC validation
- **Ready/run protocol** — READY/RUN/DONE state machine for device
  readiness and test execution
- **RESUME_AFTER deep sleep orchestration** — multi-cycle support for
  tests that survive deep sleep, with automatic RESTART and resume
- **Disconnect handler** — manages disconnect/reconnect windows for
  devices that sleep, reset, or crash
- **Test result receiver** — parses device output for test results
- **Robust doctest parser** — fixes PIO doctest parser crash on
  lines ending with ':'
- **Timing tracker** — per-test duration measurement with slow test
  reporting
- **Firmware headers** — `doctest_runner.h`, `test_runner.h`, and
  `protocol.h` for on-device test integration with CRC-8
- **Auto-install shim** — example `test_custom_runner.py` that
  pip-installs pio-test-runner and embedded-bridge from GitHub on first
  use (opt out via `PIO_TEST_RUNNER_NO_AUTO_INSTALL=1`)
- **On-device integration tests** — ESP32-S3 test project exercising
  protocol handshake, memory tracking, timing, disconnect, and deep
  sleep with two sleep cycles
- **Release script** — `scripts/release.sh` automates version bump,
  changelog dating, tagging, pushing, and GitHub release for both repos
- Design documentation

### Fixed
- Pass exception to PIO TestCase for ERRORED status
- Teardown hang detection
- Drain stale serial bytes before waiting for runner commands
- Wait for device ack before closing serial after RESTART
- Strip leading garbage bytes before CRC validation on device
- Retry command when device re-sends READY after CRC failure
