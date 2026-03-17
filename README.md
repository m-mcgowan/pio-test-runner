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
