# Changelog

All notable changes to this project will be documented in this file.
Follows [Keep a Changelog](https://keepachangelog.com/) conventions.

## [Unreleased]

### Added
- Test runner plugin with PlatformIO integration
- PTR protocol with CRC-8 checksums and repeated READY handshake
- Ready/run protocol for device readiness and test execution
- Test result receiver for parsing device output
- Robust doctest output parser
- Timing tracker for test duration measurements
- Disconnect handler for devices that sleep, reset, or crash
- Firmware library header `doctest_runner.h` for on-device test integration
- Example custom runner script
- Design documentation
- On-device integration test project

### Fixed
- Pass exception to PIO TestCase for ERRORED status
- Teardown hang detection
