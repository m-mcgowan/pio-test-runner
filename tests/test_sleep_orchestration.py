"""Integration tests for deep sleep orchestration.

These tests exercise stage_testing() -> _run_test_cycle() ->
_handle_sleep_resume() with mocked serial I/O. They prove the runner
correctly orchestrates the multi-phase sleep cycle:

  Phase 1: RUN_ALL -> tests run -> PTR:SLEEP (device sleeps)
  Phase 2: RUN: *sleeping_test* -> sleeping test Phase 2 -> PTR:DONE
  Phase 3: RESUME_AFTER: sleeping_test -> remaining tests -> PTR:DONE

Each test documents the exact protocol exchange between host and device.
"""

import os
from unittest.mock import patch

from conftest import MockProjectConfig, MockTestRunnerOptions, MockTestSuite
from pio_test_runner.protocol import format_crc
from pio_test_runner.ready_run_protocol import ProtocolState
from pio_test_runner.runner import EmbeddedTestRunner


def _crc(content: str) -> str:
    return format_crc(content)


class MockSerial:
    """Simulates a serial port for sleep orchestration tests.

    Feed it a sequence of readline responses (CRC-formatted protocol lines).
    It records all write() calls for assertion.

    Multiple phases are supported: call add_phase() to queue responses for
    after a close/reopen cycle (simulating sleep/wake).
    """

    def __init__(self):
        self._phases = []       # list of line lists
        self._current_phase = 0
        self._line_index = 0
        self._is_open = True
        self.written = []       # all bytes written
        self._in_waiting = 0

    def add_phase(self, lines):
        """Add a phase of serial responses.

        Each phase represents a serial open/close cycle. Lines are
        CRC-formatted strings that will be returned by read().
        """
        encoded = []
        for line in lines:
            encoded.append((line + "\n").encode())
        self._phases.append(encoded)

    @property
    def is_open(self):
        return self._is_open

    @property
    def in_waiting(self):
        return self._in_waiting

    def read(self, size=1):
        if not self._is_open:
            raise OSError("Port closed")
        if self._current_phase >= len(self._phases):
            return b""
        phase = self._phases[self._current_phase]
        if self._line_index >= len(phase):
            return b""
        data = phase[self._line_index]
        self._line_index += 1
        self._in_waiting = 0
        return data

    def write(self, data):
        if not self._is_open:
            raise OSError("Port closed")
        self.written.append(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def close(self):
        self._is_open = False

    def reopen(self):
        """Simulate serial port reopen after sleep/restart."""
        self._is_open = True
        self._current_phase += 1
        self._line_index = 0

    def get_commands(self):
        """Return all commands written as decoded strings."""
        cmds = []
        for data in self.written:
            text = data.decode("utf-8", errors="replace").strip()
            if text:
                cmds.append(text)
        return cmds


class MockSleepWakeMonitor:
    """Simulates USB-CDC port disappearance and reappearance."""

    def __init__(self, port_path=None):
        self.port_path = port_path
        self._check_count = 0
        self.state = "awake"

    def check_port(self):
        self._check_count += 1
        if self._check_count == 1:
            self.state = "sleeping"
        elif self._check_count >= 2:
            self.state = "waking"


class FastClock:
    """Mock clock that advances quickly so deadline-based loops exit fast.

    Each call advances by `step` seconds. Used to mock both time.time()
    and time.monotonic() so the runner's deadline checks pass without
    real wall-clock delays.
    """

    def __init__(self, start=1000.0, step=100.0):
        self._value = start
        self._step = step

    def __call__(self):
        val = self._value
        self._value += self._step
        return val


def make_orchestrated_runner(mock_serial):
    """Create a runner configured for orchestrated mode with a mock serial."""
    suite = MockTestSuite()
    config = MockProjectConfig()
    options = MockTestRunnerOptions()
    options.without_testing = False
    runner = EmbeddedTestRunner(suite, config, options)
    return runner


class TestSingleSleepCycle:
    """Phase 1 -> PTR:SLEEP -> reconnect -> Phase 2 -> RESUME_AFTER -> PTR:DONE.

    Protocol exchange:

      Phase 1 (first boot):
        Device: PTR:READY
        Runner: RUN_ALL
        Device: PTR:TEST:START suite="DeepSleep" name="sleep test"
        Device: PTR:SLEEP ms=3000
        Runner: (closes serial)

      Phase 2 (after wake):
        Device: PTR:READY
        Runner: RUN: *sleep test*
        Device: PTR:TEST:START suite="DeepSleep" name="sleep test"
        Device: PTR:DONE

      Restart (between Phase 2 and Phase 3):
        Runner: RESTART command
        Device: (reboots)

      Phase 3 (remaining tests via RESUME_AFTER):
        Device: PTR:READY
        Runner: RESUME_AFTER: sleep test
        Device: PTR:DONE  (no remaining tests)
    """

    def test_single_sleep_cycle(self):
        mock_ser = MockSerial()

        # Phase 1: boot -> run -> sleep
        mock_ser.add_phase([
            _crc("PTR:READY"),
            _crc('PTR:TEST:START suite="DeepSleep" name="sleep test"'),
            _crc("PTR:SLEEP ms=3000"),
        ])

        # Phase 2: wake -> resume sleeping test -> done
        mock_ser.add_phase([
            _crc("PTR:READY"),
            _crc('PTR:TEST:START suite="DeepSleep" name="sleep test"'),
            _crc("PTR:DONE"),
        ])

        # Phase 2.5: _restart_device opens serial briefly to send RESTART.
        # FastClock makes its read deadline expire immediately.
        mock_ser.add_phase([])

        # Phase 3: RESUME_AFTER -> device says no remaining tests -> done
        mock_ser.add_phase([
            _crc("PTR:READY"),
            _crc("PTR:DONE"),
        ])

        runner = make_orchestrated_runner(mock_ser)

        open_count = 0

        def mock_open_serial(reset=True):
            nonlocal open_count
            if open_count > 0:
                mock_ser.reopen()
            runner._ser = mock_ser
            runner._port_path = "/dev/mock"
            open_count += 1

        # FastClock advances by 100s per call. This makes time.time()
        # deadlines expire immediately (drain loop, hang detection) while
        # time.monotonic() stays at 0 (sleep monitor loops use monotonic).
        fast_time = FastClock(start=1000.0, step=100.0)

        with patch.object(runner, "configure_orchestrated", return_value=True), \
             patch.object(runner, "configure_sleep_padding", return_value=0), \
             patch.object(runner, "_open_serial", side_effect=mock_open_serial), \
             patch.object(runner, "_close_serial", side_effect=lambda: mock_ser.close()), \
             patch.dict(os.environ, {"PTR_POST_TEST": "none"}, clear=True), \
             patch("pio_test_runner.runner.SleepWakeMonitor", MockSleepWakeMonitor), \
             patch("time.sleep"), \
             patch("time.monotonic", return_value=0), \
             patch("time.time", side_effect=fast_time):
            runner.stage_testing()

        assert runner.protocol.state == ProtocolState.FINISHED

        # Verify the three-phase command sequence
        cmds = mock_ser.get_commands()
        assert any("RUN_ALL" in c for c in cmds), f"Expected RUN_ALL in {cmds}"
        assert any("RUN:" in c and "sleep test" in c for c in cmds), \
            f"Expected RUN: *sleep test* in {cmds}"
        assert any("RESUME_AFTER" in c and "sleep test" in c for c in cmds), \
            f"Expected RESUME_AFTER: sleep test in {cmds}"
