# Changelog

All notable changes to this project will be documented in this file.
Follows [Keep a Changelog](https://keepachangelog.com/) conventions.

## [Unreleased]

## [0.2.0] — 2026-04-09

### Breaking Changes
- **Protocol prefix renamed** `PTR:` → `ETST:` (Embedded Test). All protocol
  markers updated: `ETST:READY`, `ETST:DONE`, `ETST:SLEEP`, etc. Firmware
  must be rebuilt to emit the new prefix.
- **Protocol tag renamed** `ETST:TEST:START` → `ETST:CASE:START` (avoids
  "test test" stutter). `ETST:TESTS` → `ETST:COUNTS`.
- **Environment variables renamed** `PTR_*` → `ETST_*`. Old names still work
  with a deprecation warning. `ETST_TEST_CASE`, `ETST_POST_TEST`, etc.

### Added
- **`--wake` protocol flag** — host tells firmware this is a Phase 2 wake
  cycle via `RUN: --wake --tc "test name"`. Replaces RTC memory flag — no
  sleep memory consumed. `is_test_wake()` reads the protocol flag instead
  of hardware wake cause register.
- **Accurate skip/run counts** — `ETST:COUNTS` now reflects `--tc`, `--ts`,
  `--tce`, `--tse` filters, not just RESUME_AFTER skips. Uses
  `count_passing_filters()` to replicate doctest's filter matching.
- **Hang detection in line callback mode** — `on_testing_line_output()` now
  tracks the gap between lines and reports a hang if it exceeds the timeout.
  Previously only worked in orchestrated mode.
- **Aggregate run summary** — `[runner] N ran | N passed | N failed` printed
  after all sleep/wake cycles complete, counting unique tests across cycles.
- **Protocol message builders** — `msg_ready()`, `msg_done()`,
  `msg_case_start()`, `msg_counts()`, etc. in `protocol.py` so the prefix
  is defined once.
- **Deep sleep test documentation** — README section with complete example
  of two-phase sleep/wake pattern using `signal_sleep()` / `is_test_wake()`.
- **Non-suite test coverage** — integration firmware includes a test outside
  any `TEST_SUITE()` to verify filter behavior with empty suite names.
- **Roadmap** — `docs/ROADMAP.md` covering framework abstraction, host
  abstraction, bidirectional data channel, and platform expansion.

### Fixed
- **Phase 2 resume command** — send `RUN: --wake --tc "exact name"` instead
  of `RUN: *name*`. Fixes tokenizer splitting multi-word test names (all
  tests re-ran instead of just the sleeping test). Fixes wildcard substring
  collisions between similarly-named tests.
- **`is_test_wake()` across multiple sleep tests** — flag cleared after each
  `run_cycle()` so RESUME_AFTER tests don't see stale wake state from a
  previous sleep test.
- **Integration test build** — added `test/main.cpp` for header-only library
  mode (after `default_main.cpp` was excluded from build in 0.2.0).
- **PlatformIO environment isolation** — `.envrc` with separate
  `PLATFORMIO_CORE_DIR` prevents esptoolpy package conflicts.
- **CI** — install embedded-bridge from git, ignore acceptance tests
  (require hardware).

## [0.2.0-unreleased] — 2026-03-31

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
- **`ETST:BUSY` protocol message** — firmware signals it will be busy for
  a specified duration; host extends hang timeout accordingly.
- **`ETST:RESTART` protocol message** — firmware signals an imminent software
  restart; host handles reconnection like a sleep cycle.
- **Per-test timeout annotation** — `ETST:CASE:START` now includes
  `timeout=N` from `doctest::timeout(N)` decorators for host-side
  enforcement.
- **Test count reporting** — `ETST:COUNTS total=N skip=N run=N` emitted
  before test execution begins.
- **SLEEP command** — host sends SLEEP after test completion to prevent
  battery drain on idle devices.
- **Largest contiguous block** — `ETST:MEM:BEFORE/AFTER` markers now include
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
