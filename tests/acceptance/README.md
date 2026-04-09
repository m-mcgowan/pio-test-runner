# Acceptance Tests

End-to-end tests that validate pio-test-runner features on real hardware.
Each test communicates directly with a device over serial, exercising the
full READY/RUN/DONE protocol round-trip.

## Prerequisites

1. **Device connected** — pass the device name or port.

## Running

```bash
# Via the run script (flashes firmware, creates venv, runs tests):
tests/acceptance/run.sh "1.9"

# Filter to specific tests:
tests/acceptance/run.sh "1.9" -k "unskip"

# Manual: flash with PTR_POST_TEST=restart so device stays awake:
PORT=$(usb-device port "1.9")
PTR_POST_TEST=restart pio test -e esp32s3 \
    --upload-port $PORT --test-port $PORT \
    -d tests/integration
pytest tests/acceptance/ -v --port $PORT --ignore=tests/acceptance/test_sleep.py
```

**Important:** Use `PTR_POST_TEST=restart` when flashing. The default
`SLEEP` puts the device into deep sleep after tests, making the port
disappear. `restart` reboots the device so it's immediately available
for acceptance tests.

## Test Coverage

### Serial protocol (direct serial communication)

| Feature | Test file | What's validated |
|---------|-----------|-----------------|
| Filtering (`--tc`, `--ts`, `--tce`, `--tse`) | test_filtering.py | Correct tests run/excluded for each filter |
| Comma-separated patterns | test_filtering.py | `--tc *foo*,*bar*` matches both |
| Skip control (`--unskip-tc`, `--skip-tc`) | test_filtering.py | Skip-decorated tests enabled/disabled selectively |
| `--no-skip` | test_filtering.py | All skipped tests run |
| Skip flag ordering | test_filtering.py | Later flags override earlier on same test |
| Combined filters | test_filtering.py | Multiple filters compose correctly |
| Memory tracking | test_protocol_features.py | `ETST:MEM:BEFORE/AFTER` markers parsed |
| Timing tracking | test_protocol_features.py | `ETST:CASE:START` with suite/name/timeout |
| Test counts | test_protocol_features.py | `ETST:COUNTS total=N skip=N run=N` |
| Deep sleep | test_sleep.py | Sleep → reconnect → resume → remaining tests |

### Full pipeline (via `pio test` subprocess)

| Feature | Test file | What's validated |
|---------|-----------|-----------------|
| `PTR_TEST_SUITE` env var | test_env_vars.py | Suite filter via env → correct tests run |
| `PTR_TEST_CASE` env var | test_env_vars.py | Case filter via env |
| `PTR_TEST_CASE_EXCLUDE` env var | test_env_vars.py | Case exclusion via env |
| `PTR_TEST_SUITE_EXCLUDE` env var | test_env_vars.py | Suite exclusion via env |
| `PTR_UNSKIP_TEST_CASE` env var | test_env_vars.py | Selective unskip via env |
| `PTR_UNSKIP_TEST_SUITE` env var | test_env_vars.py | Suite unskip via env |
| `PTR_SKIP_TEST_CASE` env var | test_env_vars.py | Force-skip via env |
| `PTR_NO_SKIP` env var | test_env_vars.py | Global unskip via env |
| `pio test -a "..."` args | test_env_vars.py | Flags passed through to device |
| `-a` combined with env vars | test_env_vars.py | Both sources compose correctly |

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
