"""Integration tests for deep sleep orchestration.

These tests exercise stage_testing() -> _run_test_cycle() ->
_handle_sleep_resume() with mocked serial I/O. They prove the runner
correctly orchestrates the multi-phase sleep cycle:

  Phase 1: RUN_ALL -> tests run -> ETST:SLEEP (device sleeps)
  Phase 2: RUN: --tc "sleeping_test" -> sleeping test Phase 2 -> ETST:DONE
  Phase 3: RESUME_AFTER: sleeping_test -> remaining tests -> ETST:DONE

After Phase 2, the runner sends RESUME_AFTER directly through the
device's idle_loop — no restart needed. The host stays in control.

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
    """Phase 1 -> ETST:SLEEP -> reconnect -> Phase 2 -> RESUME_AFTER -> ETST:DONE.

    Protocol exchange:

      Phase 1 (first boot):
        Device: ETST:READY
        Runner: RUN_ALL
        Device: ETST:CASE:START suite="DeepSleep" name="sleep test"
        Device: ETST:SLEEP ms=3000
        Runner: (closes serial, no post-test command)

      Phase 2 (after wake):
        Device: ETST:READY
        Runner: RUN: --tc "sleep test"
        Device: ETST:CASE:START suite="DeepSleep" name="sleep test"
        Device: ETST:DONE
        Runner: (closes serial, no post-test command — intermediate cycle)

      Phase 3 (remaining tests via RESUME_AFTER, no restart needed):
        Device: ETST:READY
        Runner: RESUME_AFTER: sleep test
        Device: ETST:DONE  (no remaining tests)
    """

    def test_single_sleep_cycle(self):
        mock_ser = MockSerial()

        # Phase 1: boot -> run -> sleep
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="DeepSleep" name="sleep test"'),
            _crc("ETST:SLEEP ms=3000"),
        ])

        # Phase 2: wake -> resume sleeping test -> done
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="DeepSleep" name="sleep test"'),
            _crc("ETST:DONE"),
        ])

        # Phase 3: RESUME_AFTER directly (no restart) -> no remaining -> done
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc("ETST:DONE"),
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

        # Verify the three-phase command sequence (no RESTART)
        cmds = mock_ser.get_commands()
        assert any("RUN_ALL" in c for c in cmds), f"Expected RUN_ALL in {cmds}"
        assert any("RUN:" in c and "sleep test" in c for c in cmds), \
            f"Expected RUN: --tc \"sleep test\" in {cmds}"
        assert any("RESUME_AFTER" in c and "sleep test" in c for c in cmds), \
            f"Expected RESUME_AFTER: sleep test in {cmds}"
        # No RESTART between Phase 2 and RESUME_AFTER
        assert not any("RESTART" in c for c in cmds), \
            f"Expected no RESTART in {cmds}"


class TestSleepWithResumeAfter:
    """Phase 1 -> sleep -> Phase 2 -> RESUME_AFTER with remaining tests.

    Protocol exchange:

      Phase 1 (first boot):
        Device: ETST:READY
        Runner: RUN_ALL
        Device: ETST:CASE:START suite="DeepSleep" name="sleep test"
        Device: ETST:SLEEP ms=3000
        Runner: (closes serial)

      Phase 2 (after wake):
        Device: ETST:READY
        Runner: RUN: --tc "sleep test"
        Device: ETST:CASE:START suite="DeepSleep" name="sleep test"
        Device: ETST:DONE
        Runner: (closes serial, intermediate cycle)

      Phase 3 (RESUME_AFTER directly, no restart):
        Device: ETST:READY
        Runner: RESUME_AFTER: sleep test
        Device: ETST:CASE:START suite="Other" name="normal test"
        Device: ETST:DONE
    """

    def test_sleep_then_resume_remaining(self):
        mock_ser = MockSerial()

        # Phase 1: boot -> run -> sleep
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="DeepSleep" name="sleep test"'),
            _crc("ETST:SLEEP ms=3000"),
        ])

        # Phase 2: wake -> resume sleeping test -> done
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="DeepSleep" name="sleep test"'),
            _crc("ETST:DONE"),
        ])

        # Phase 3: RESUME_AFTER directly -> remaining tests -> done
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="Other" name="normal test"'),
            _crc("ETST:DONE"),
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

        cmds = mock_ser.get_commands()
        assert any("RUN_ALL" in c for c in cmds), f"Expected RUN_ALL in {cmds}"
        assert any("RUN:" in c and "sleep test" in c for c in cmds), \
            f"Expected RUN: --tc \"sleep test\" in {cmds}"
        assert any("RESUME_AFTER" in c and "sleep test" in c for c in cmds), \
            f"Expected RESUME_AFTER: sleep test in {cmds}"
        assert not any("RESTART" in c for c in cmds), \
            f"Expected no RESTART in {cmds}"

        # Verify the "normal test" actually ran in Phase 3
        assert "Other/normal test" in runner.protocol.completed_tests, \
            f"Expected 'Other/normal test' in completed_tests: {runner.protocol.completed_tests}"


class TestTwoConsecutiveSleepTests:
    """Two tests that both sleep, with a normal test after.

    Protocol exchange:

      Phase 1: RUN_ALL -> test_a sleeps
        Device: ETST:READY
        Runner: RUN_ALL
        Device: ETST:CASE:START suite="Sleep" name="test_a"
        Device: ETST:SLEEP ms=2000

      Phase 2: wake -> test_a Phase 2 completes
        Device: ETST:READY
        Runner: RUN: --tc "test_a"
        Device: ETST:CASE:START suite="Sleep" name="test_a"
        Device: ETST:DONE

      RESUME_AFTER test_a -> test_b sleeps (no restart)
        Device: ETST:READY
        Runner: RESUME_AFTER: test_a
        Device: ETST:CASE:START suite="Sleep" name="test_b"
        Device: ETST:SLEEP ms=3000

      Phase 4: wake -> test_b Phase 2 completes
        Device: ETST:READY
        Runner: RUN: --tc "test_b"
        Device: ETST:CASE:START suite="Sleep" name="test_b"
        Device: ETST:DONE

      RESUME_AFTER test_b -> test_c runs normally (no restart)
        Device: ETST:READY
        Runner: RESUME_AFTER: test_b
        Device: ETST:CASE:START suite="Other" name="test_c"
        Device: ETST:DONE
    """

    def test_two_sleep_tests_then_normal(self):
        mock_ser = MockSerial()

        # Phase 1: boot -> RUN_ALL -> test_a sleeps
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="Sleep" name="test_a"'),
            _crc("ETST:SLEEP ms=2000"),
        ])

        # Phase 2: wake -> RUN: --tc "test_a" -> test_a Phase 2 completes
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="Sleep" name="test_a"'),
            _crc("ETST:DONE"),
        ])

        # RESUME_AFTER test_a -> test_b starts and sleeps (no restart)
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="Sleep" name="test_b"'),
            _crc("ETST:SLEEP ms=3000"),
        ])

        # Phase 4: wake -> RUN: --tc "test_b" -> test_b Phase 2 completes
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="Sleep" name="test_b"'),
            _crc("ETST:DONE"),
        ])

        # RESUME_AFTER test_b -> test_c runs normally -> done (no restart)
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="Other" name="test_c"'),
            _crc("ETST:DONE"),
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

        cmds = mock_ser.get_commands()
        assert any("RUN_ALL" in c for c in cmds), f"Expected RUN_ALL in {cmds}"
        assert any("RUN:" in c and "test_a" in c for c in cmds), \
            f"Expected Phase 2 RUN: --tc \"test_a\" in {cmds}"
        assert any("RESUME_AFTER" in c and "test_a" in c for c in cmds), \
            f"Expected RESUME_AFTER: test_a in {cmds}"
        assert any("RUN:" in c and "test_b" in c for c in cmds), \
            f"Expected Phase 2 RUN: --tc \"test_b\" in {cmds}"
        assert any("RESUME_AFTER" in c and "test_b" in c for c in cmds), \
            f"Expected RESUME_AFTER: test_b in {cmds}"
        assert not any("RESTART" in c for c in cmds), \
            f"Expected no RESTART in {cmds}"

        # Verify test_c actually ran in the final RESUME_AFTER phase
        assert "Other/test_c" in runner.protocol.completed_tests, \
            f"Expected 'Other/test_c' in completed_tests: {runner.protocol.completed_tests}"


class TestFailurePropagationAcrossSleepCycles:
    """Assertion failures in RESUME_AFTER phases are reported.

    A test that fails after a sleep cycle should still produce a
    FAILED case in the test suite — the sleep orchestration must not
    swallow failures.
    """

    def test_failure_in_resume_after_phase(self):
        mock_ser = MockSerial()

        # Phase 1: sleep test
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="DeepSleep" name="sleep test"'),
            _crc("ETST:SLEEP ms=3000"),
        ])

        # Phase 2: sleep test Phase 2 passes
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="DeepSleep" name="sleep test"'),
            "  CHECK( cause == ESP_SLEEP_WAKEUP_TIMER ) is correct!",
            _crc("ETST:DONE"),
        ])

        # RESUME_AFTER: remaining test FAILS
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="Sensor" name="calibration check"'),
            "test/test_sensor.cpp:55: ERROR: CHECK( offset < 10 ) is NOT correct!",
            "  values: CHECK( 42 < 10 )",
            _crc("ETST:DONE"),
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

        # The failure in the RESUME_AFTER phase must be tracked
        assert "Sensor/calibration check" in runner._test_failures

        # And reported to the test suite
        from conftest import MockTestStatus
        failed = [c for c in runner.test_suite.cases
                  if c.status == MockTestStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].name == "Sensor/calibration check"

    def test_passing_tests_reported_across_cycles(self):
        """Completed tests from all cycles appear as PASSED in the suite."""
        mock_ser = MockSerial()

        # Phase 1: sleep test
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="DeepSleep" name="sleep test"'),
            _crc("ETST:SLEEP ms=3000"),
        ])

        # Phase 2: sleep test passes
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="DeepSleep" name="sleep test"'),
            "  CHECK( cause == ESP_SLEEP_WAKEUP_TIMER ) is correct!",
            _crc("ETST:DONE"),
        ])

        # RESUME_AFTER: remaining test passes
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="Sensor" name="calibration"'),
            "  CHECK( offset < 10 ) is correct!",
            _crc("ETST:DONE"),
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

        # Both tests should be PASSED in the suite (across cycles)
        from conftest import MockTestStatus
        passed = [c for c in runner.test_suite.cases
                  if c.status == MockTestStatus.PASSED]
        passed_names = {c.name for c in passed}
        assert "sleep test" in passed_names or "DeepSleep/sleep test" in passed_names, \
            f"Expected sleep test in passed: {passed_names}"
        assert "calibration" in passed_names or "Sensor/calibration" in passed_names, \
            f"Expected calibration in passed: {passed_names}"
        assert len(runner._test_failures) == 0


class TestPhase2CommandFormat:
    """The Phase 2 RUN: command must use --tc with a quoted exact name.

    Bug: the runner previously sent `RUN: *test name with spaces*` as a
    bare wildcard-wrapped pattern. The firmware tokenizer splits on spaces,
    producing garbage args that doctest ignores — skip=0, all tests re-run.

    See BUG_resume_loop_prevents_later_suites.md for the full incident.
    """

    def test_multiword_name_must_be_quoted(self):
        """Phase 2 command for a multi-word test must use --tc "name".

        Without quoting, the firmware tokenizer splits "Probe after restart"
        into ["Probe", "after", "restart", ...] — none are valid doctest
        flags, so the filter is silently ignored and all tests re-run.
        """
        mock_ser = MockSerial()

        # Phase 1: test with spaces in name triggers sleep
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="BHI385" name="Probe after restart with full IMU lifecycle"'),
            _crc("ETST:SLEEP ms=0"),
        ])

        # Phase 2: wake -> resume
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="BHI385" name="Probe after restart with full IMU lifecycle"'),
            _crc("ETST:DONE"),
        ])

        # Phase 3: RESUME_AFTER -> done
        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc("ETST:DONE"),
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

        # Find the Phase 2 RUN: command (not RUN_ALL)
        cmds = mock_ser.get_commands()
        run_cmds = [c for c in cmds if "RUN:" in c and "RUN_ALL" not in c
                    and "RESUME_AFTER" not in c]
        assert len(run_cmds) >= 1, f"Expected at least one RUN: command in {cmds}"

        phase2_cmd = run_cmds[0]
        # The command must use --tc with quoting so the firmware tokenizer
        # keeps the multi-word name as a single argument
        assert '--tc' in phase2_cmd, \
            f"Phase 2 must use --tc flag, got: {phase2_cmd}"
        assert '"Probe after restart with full IMU lifecycle"' in phase2_cmd, \
            f"Phase 2 must quote the multi-word test name, got: {phase2_cmd}"

    def test_exact_match_no_wildcards(self):
        """Phase 2 command must not use wildcard wrapping (*name*).

        Wildcards cause substring collisions: if tests "sleep test" and
        "extended sleep test" both exist, `RUN: *sleep test*` matches both.
        The Phase 2 command should use an exact match.
        """
        mock_ser = MockSerial()

        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="Sleep" name="sleep test"'),
            _crc("ETST:SLEEP ms=3000"),
        ])

        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="Sleep" name="sleep test"'),
            _crc("ETST:DONE"),
        ])

        mock_ser.add_phase([
            _crc("ETST:READY"),
            _crc("ETST:DONE"),
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

        cmds = mock_ser.get_commands()
        run_cmds = [c for c in cmds if "RUN:" in c and "RUN_ALL" not in c
                    and "RESUME_AFTER" not in c]
        assert len(run_cmds) >= 1, f"Expected RUN: command in {cmds}"

        phase2_cmd = run_cmds[0]
        # Must not contain wildcards around the test name
        assert "*sleep test*" not in phase2_cmd, \
            f"Phase 2 must not use wildcard wrapping, got: {phase2_cmd}"
