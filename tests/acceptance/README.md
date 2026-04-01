# Acceptance Tests

End-to-end tests that validate pio-test-runner features on real hardware.
Each test communicates directly with a device over serial, exercising the
full READY/RUN/DONE protocol round-trip.

## Prerequisites

1. **Flash the integration firmware** (once, or after firmware changes):

```bash
cd tests/integration
pio run -e esp32s3 -t upload --upload-port $(usb-device port "1.9")
```

2. **Device connected** — pass the device name or port.

## Running

```bash
# Via the run script (creates venv, installs deps):
tests/acceptance/run.sh "1.9"

# Filter to specific tests:
tests/acceptance/run.sh "1.9" -k "unskip"

# Direct pytest (if deps already installed):
pytest tests/acceptance/ -v --port $(usb-device port "1.9")
```

## Test Coverage

| Feature | Test file | What's validated |
|---------|-----------|-----------------|
| Filtering (`--tc`, `--ts`, `--tce`, `--tse`) | test_filtering.py | Correct tests run/excluded for each filter |
| Skip control (`--unskip-tc`, `--skip-tc`) | test_filtering.py | Skip-decorated tests enabled/disabled selectively |
| `--no-skip` | test_filtering.py | All skipped tests run |
| Skip flag ordering | test_filtering.py | Later flags override earlier on same test |
| Combined filters | test_filtering.py | Multiple filters compose correctly |
| Memory tracking | test_protocol_features.py | `PTR:MEM:BEFORE/AFTER` markers parsed |
| Timing tracking | test_protocol_features.py | `PTR:TEST:START` with suite/name/timeout |
| Deep sleep | test_sleep.py | Sleep → reconnect → resume → remaining tests |
| Crash detection | test_crash.py | Backtrace/WDT patterns detected as ERRORED |

## Adding Tests

Acceptance tests communicate via the PTR protocol. Use the `send_command()`
helper to send a `RUN:` command and collect results:

```python
def test_my_feature(device):
    result = send_command(device, "RUN: --ts *MySuite*")
    assert "my test name" in result["tests_run"]
    send_sleep(device)  # reset device for next test
```

Each test should call `send_sleep(device)` at the end to put the device
in the idle state for the next test.
