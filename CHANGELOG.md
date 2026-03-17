# Changelog

All notable changes to this project will be documented in this file.
Follows [Keep a Changelog](https://keepachangelog.com/) conventions.

## [Unreleased]

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
