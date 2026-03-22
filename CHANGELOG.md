# Changelog

All notable changes to this project will be documented in this file.
Follows [Keep a Changelog](https://keepachangelog.com/) conventions.

## [Unreleased]

### Changed
- **Default READY timeout**: Device now waits indefinitely for runner
  instead of timing out after 5s. The old timeout predated pio-test-runner
  and caused race conditions with USB-CDC reconnection after upload.
  Set `PTR_READY_TIMEOUT_MS` to restore a finite timeout.
- **wait_for_command(0)**: Now correctly waits forever instead of returning
  immediately (was: `millis() < millis()` = false)

### Planned
- **RESUME_FROM** — resume test run from a named test (rerun after transient
  errors without re-running already-passed tests)
- **Rerun failed tests** — collect failed test names, send `RUN:` filter
  matching only those tests on next cycle

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
