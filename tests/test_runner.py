"""Tests for EmbeddedTestRunner."""

import threading
import time
from io import BytesIO
from unittest.mock import MagicMock, PropertyMock

from conftest import (
    MockProjectConfig,
    MockTestRunnerOptions,
    MockTestStatus,
    MockTestSuite,
)

from pio_test_runner.protocol import format_crc
from pio_test_runner.ready_run_protocol import ProtocolState
from pio_test_runner.runner import EmbeddedTestRunner


def _crc(content: str) -> str:
    return format_crc(content)


def make_runner(**kwargs):
    """Create a runner with mock PIO objects."""
    suite = kwargs.pop("test_suite", None) or MockTestSuite()
    config = kwargs.pop("project_config", None) or MockProjectConfig()
    options = kwargs.pop("options", None) or MockTestRunnerOptions()
    runner = EmbeddedTestRunner(suite, config, options)
    return runner


class TestCrashHandling:
    def test_crash_adds_errored_case(self):
        runner = make_runner()

        runner.on_testing_line_output("Guru Meditation Error: Core 0 panic\n")
        # Feed enough lines to finalize crash
        for i in range(20):
            runner.on_testing_line_output(f"backtrace line {i}\n")

        assert runner.test_suite._finished
        errored = [c for c in runner.test_suite.cases if c.status == MockTestStatus.ERRORED]
        assert len(errored) == 1
        assert "Crash" in errored[0].message
        assert isinstance(errored[0].exception, RuntimeError)

    def test_crash_finishes_suite(self):
        runner = make_runner()
        runner.on_testing_line_output("Backtrace: 0x400d1234\n")
        for i in range(20):
            runner.on_testing_line_output(f"line {i}\n")
        assert runner.test_suite._finished

    def test_no_crash_does_not_finish(self):
        runner = make_runner()
        runner.on_testing_line_output("Normal output\n")
        assert not runner.test_suite._finished


class TestDisconnectSuppression:
    def test_disconnect_tracked(self):
        runner = make_runner()
        runner.on_testing_line_output(_crc("ETST:DISCONNECT ms=5000") + "\n")

        assert runner.disconnect_handler.active

    def test_reconnect_clears_disconnect(self):
        runner = make_runner()
        runner.on_testing_line_output(_crc("ETST:DISCONNECT ms=1000") + "\n")
        assert runner.disconnect_handler.active

        runner.on_testing_line_output(_crc("ETST:RECONNECT") + "\n")
        assert not runner.disconnect_handler.active


class TestLifecycle:
    def test_teardown_checks_timeout(self):
        runner = make_runner()
        # Simulate some output, then silence
        runner.crash_detector._last_feed_time = 0.0  # long ago
        runner.crash_detector._silent_timeout = 0.001

        runner.teardown()

        errored = [c for c in runner.test_suite.cases if c.status == MockTestStatus.ERRORED]
        assert len(errored) == 1
        assert "hang" in errored[0].name
        assert isinstance(errored[0].exception, RuntimeError)

    def test_teardown_no_hang_when_runner_finished(self):
        runner = make_runner()
        # Simulate orchestrated mode finishing normally
        runner._finished_by_runner = True

        runner.crash_detector._last_feed_time = 0.0
        runner.crash_detector._silent_timeout = 0.001

        runner.teardown()
        # Should not add a hang case since runner already finished normally
        errored = [c for c in runner.test_suite.cases if c.status == MockTestStatus.ERRORED]
        assert len(errored) == 0

    def test_teardown_hang_when_pio_timed_out(self):
        runner = make_runner()
        # Simulate PIO's serial reader timing out — runner never finished
        runner.crash_detector._last_feed_time = 0.0
        runner.crash_detector._silent_timeout = 0.001
        # PIO calls on_finish() before teardown, but our flag is False
        runner.test_suite.on_finish()
        assert not runner._finished_by_runner

        runner.teardown()
        # Should detect the hang since runner didn't explicitly finish
        errored = [c for c in runner.test_suite.cases if c.status == MockTestStatus.ERRORED]
        assert len(errored) == 1
        assert "hang" in errored[0].name
        assert isinstance(errored[0].exception, RuntimeError)


class TestHangTimeout:
    def test_default_hang_timeout(self):
        runner = make_runner()
        assert runner.configure_hang_timeout() == 30.0

    def test_hang_timeout_from_env(self, monkeypatch):
        monkeypatch.setenv("PTR_HANG_TIMEOUT", "120")
        runner = make_runner()
        assert runner.configure_hang_timeout() == 120.0

    def test_effective_timeout_uses_per_test(self):
        runner = make_runner()
        runner.protocol._current_test_timeout = 60
        assert runner._effective_hang_timeout() == 60.0

    def test_effective_timeout_falls_back_to_global(self):
        runner = make_runner()
        runner.protocol._current_test_timeout = 0
        assert runner._effective_hang_timeout() == 30.0

    def test_effective_timeout_env_override(self, monkeypatch):
        monkeypatch.setenv("PTR_HANG_TIMEOUT", "90")
        runner = make_runner()
        runner.protocol._current_test_timeout = 0
        assert runner._effective_hang_timeout() == 90.0

    def test_per_test_overrides_env(self, monkeypatch):
        monkeypatch.setenv("PTR_HANG_TIMEOUT", "90")
        runner = make_runner()
        runner.protocol._current_test_timeout = 15
        assert runner._effective_hang_timeout() == 15.0


class TestLineCallbackHangDetection:
    """Hang detection in line callback mode (PIO owns serial).

    Bug: on_testing_line_output() has no hang detection. When PIO owns
    the serial port and calls on_testing_line_output() for each line,
    a silent hang blocks until PIO's overall timeout (minutes), not the
    runner's 30s hang timeout. See BUG_hang_detector_inactive_in_pio_mode.md.
    """

    def test_hang_detected_after_silence(self, monkeypatch):
        """If no output arrives for longer than the hang timeout,
        the runner should detect and report the hang."""
        monkeypatch.setenv("PTR_HANG_TIMEOUT", "0.1")  # 100ms
        runner = make_runner()

        # Simulate a test starting
        runner.on_testing_line_output(_crc("ETST:READY") + "\n")
        runner.protocol.command_sent()
        runner.on_testing_line_output(
            _crc('ETST:CASE:START suite="Suite" name="hangs"') + "\n"
        )

        # Wait longer than the hang timeout
        time.sleep(0.2)

        # Feed another line — runner should notice the gap
        runner.on_testing_line_output("some output after silence\n")

        # The hang should have been detected
        errored = [
            c for c in runner.test_suite.cases
            if c.status == MockTestStatus.ERRORED
        ]
        assert len(errored) == 1, \
            f"Expected hang detection, got cases: {runner.test_suite.cases}"
        assert "hang" in errored[0].message.lower()

    def test_no_false_hang_with_continuous_output(self, monkeypatch):
        """Continuous output within the timeout should not trigger hang."""
        monkeypatch.setenv("PTR_HANG_TIMEOUT", "0.5")
        runner = make_runner()

        runner.on_testing_line_output(_crc("ETST:READY") + "\n")
        runner.protocol.command_sent()
        runner.on_testing_line_output(
            _crc('ETST:CASE:START suite="Suite" name="fast"') + "\n"
        )

        # Rapid output — no hang
        for i in range(5):
            runner.on_testing_line_output(f"progress {i}\n")
            time.sleep(0.05)

        runner.on_testing_line_output(_crc("ETST:DONE") + "\n")

        errored = [
            c for c in runner.test_suite.cases
            if c.status == MockTestStatus.ERRORED
        ]
        assert len(errored) == 0, \
            f"False hang detection: {errored}"


class TestProgramArgs:
    def test_no_args_returns_run_all(self):
        runner = make_runner()
        assert runner._build_initial_command() == "RUN_ALL"

    def test_program_args_forwarded(self):
        opts = MockTestRunnerOptions()
        opts.program_args = ["--ts", "*BHI385*"]
        runner = make_runner(options=opts)
        cmd = runner._build_initial_command()
        assert cmd.startswith("RUN: ")
        assert "--ts" in cmd
        assert "*BHI385*" in cmd

    def test_program_args_combined_with_env(self, monkeypatch):
        monkeypatch.setenv("PTR_TEST_CASE", "*watermark*")
        opts = MockTestRunnerOptions()
        opts.program_args = ["--ts", "*BHI385*"]
        runner = make_runner(options=opts)
        cmd = runner._build_initial_command()
        assert "--ts" in cmd
        assert "--tc" in cmd

    def test_single_combined_arg(self):
        opts = MockTestRunnerOptions()
        opts.program_args = ["--ts *Suite*"]
        runner = make_runner(options=opts)
        cmd = runner._build_initial_command()
        assert "--ts *Suite*" in cmd

    def test_env_only(self, monkeypatch):
        monkeypatch.setenv("PTR_TEST_SUITE", "*GPS*")
        runner = make_runner()
        cmd = runner._build_initial_command()
        assert cmd == "RUN: --ts *GPS*"


class MockSerial:
    """Mock serial port that feeds pre-scripted data with timing."""

    def __init__(self):
        self._chunks: list[tuple[float, bytes]] = []  # (delay_s, data)
        self._idx = 0
        self._start = time.time()
        self._written: list[bytes] = []
        self.is_open = True

    def schedule(self, delay: float, data: str):
        """Schedule data to be returned after delay seconds."""
        self._chunks.append((delay, data.encode()))
        return self

    def read(self, size=1):
        if self._idx >= len(self._chunks):
            time.sleep(0.01)
            return b""
        delay, data = self._chunks[self._idx]
        elapsed = time.time() - self._start
        if elapsed < delay:
            remaining = delay - elapsed
            if remaining > 0.01:
                time.sleep(min(remaining, 0.05))
            return b""
        self._idx += 1
        return data

    @property
    def in_waiting(self):
        if self._idx >= len(self._chunks):
            return 0
        delay, data = self._chunks[self._idx]
        if time.time() - self._start >= delay:
            return len(data)
        return 0

    def write(self, data):
        self._written.append(data)

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    @property
    def written_strings(self):
        return [d.decode() for d in self._written]


class TestOrchestratedProtocol:
    """Test the orchestrated mode serial protocol handling.

    These simulate _run_test_cycle behavior by feeding data through
    _on_serial_data and checking protocol state transitions.
    """

    def test_done_received_during_main_loop(self):
        """ETST:DONE in the normal serial stream is detected."""
        runner = make_runner()
        # Simulate: READY → command sent → tests → DONE
        lines = [
            _crc("ETST:READY") + "\n",
            _crc('ETST:CASE:START suite="S" name="t1"') + "\n",
            "test output\n",
            '[doctest] test cases: 1 | 1 passed | 0 failed\n',
            _crc("ETST:DONE") + "\n",
        ]
        for line in lines:
            runner._on_serial_data(line.encode())
            if runner.protocol.state == ProtocolState.READY:
                runner.protocol.command_sent()

        assert runner.protocol.state == ProtocolState.FINISHED

    def test_done_after_doctest_summary(self):
        """ETST:DONE arrives after doctest summary — must not be missed.

        This reproduces the bug where PIO's is_finished() fires on the
        doctest summary line, causing the runner to exit before ETST:DONE.
        """
        runner = make_runner()
        lines = [
            _crc("ETST:READY") + "\n",
            _crc('ETST:CASE:START suite="S" name="t1"') + "\n",
            "test output\n",
        ]
        for line in lines:
            runner._on_serial_data(line.encode())
            if runner.protocol.state == ProtocolState.READY:
                runner.protocol.command_sent()

        # At this point, protocol is RUNNING
        assert runner.protocol.state == ProtocolState.RUNNING

        # PIO's parser sees doctest summary and marks suite finished
        runner.test_suite._finished = True

        # ETST:DONE arrives after PIO declares finished
        runner._on_serial_data((_crc("ETST:DONE") + "\n").encode())

        # Protocol must still detect DONE
        assert runner.protocol.state == ProtocolState.FINISHED

    def test_done_not_received_leaves_running(self):
        """Without ETST:DONE, protocol stays in RUNNING state."""
        runner = make_runner()
        lines = [
            _crc("ETST:READY") + "\n",
            _crc('ETST:CASE:START suite="S" name="t1"') + "\n",
            "test output\n",
            '[doctest] test cases: 1 | 1 passed | 0 failed\n',
        ]
        for line in lines:
            runner._on_serial_data(line.encode())
            if runner.protocol.state == ProtocolState.READY:
                runner.protocol.command_sent()

        # No ETST:DONE — should still be RUNNING
        assert runner.protocol.state == ProtocolState.RUNNING

    def test_sleep_command_sent_after_done(self):
        """Runner sends SLEEP to the device after ETST:DONE."""
        runner = make_runner()
        mock_ser = MockSerial()
        runner._ser = mock_ser

        # Simulate completion
        runner.protocol.feed(_crc("ETST:READY"))
        runner.protocol.command_sent()
        runner.protocol.feed(_crc("ETST:DONE"))
        assert runner.protocol.state == ProtocolState.FINISHED

        # The close-serial path should send SLEEP
        # (simulating what happens at end of _run_test_cycle)
        if runner._ser and runner._ser.is_open:
            runner._ser.write(b"SLEEP\n")
            runner._ser.flush()

        assert b"SLEEP\n" in mock_ser._written

    def test_partial_line_reassembly(self):
        """ETST:DONE split across two serial reads is handled correctly."""
        runner = make_runner()
        runner._line_buf = ""

        # Feed READY and start running
        runner._on_serial_data((_crc("ETST:READY") + "\n").encode())
        runner.protocol.command_sent()

        # ETST:DONE arrives split across two reads
        done_line = _crc("ETST:DONE") + "\n"
        mid = len(done_line) // 2
        runner._on_serial_data(done_line[:mid].encode())
        assert runner.protocol.state == ProtocolState.RUNNING  # not yet

        runner._on_serial_data(done_line[mid:].encode())
        assert runner.protocol.state == ProtocolState.FINISHED  # now complete

    def test_main_loop_catches_done_in_stream(self):
        """The main read loop must detect ETST:DONE mixed with test output.

        Reproduces the bug where the runner exits the main loop without
        seeing ETST:DONE because the doctest summary line causes PIO to
        mark the suite as finished, breaking out before DONE arrives.
        """
        runner = make_runner()
        runner._line_buf = ""

        # Simulate full test session including doctest summary
        session = [
            _crc("ETST:READY") + "\n",
            _crc('ETST:CASE:START suite="S" name="t1" timeout=5') + "\n",
            "test/foo.cpp:10: SUCCESS: CHECK( true )\n",
            # doctest summary — this is what PIO parses to mark finished
            "[doctest] test cases: 1 | 1 passed | 0 failed\n",
            "[doctest] assertions: 1 | 1 passed | 0 failed\n",
            "[doctest] Status: SUCCESS!\n",
            # ETST:DONE comes AFTER the doctest summary
            _crc("ETST:DONE") + "\n",
        ]

        for line in session:
            runner._on_serial_data(line.encode())
            if runner.protocol.state == ProtocolState.READY:
                runner.protocol.command_sent()

        # The critical check: protocol must reach FINISHED
        assert runner.protocol.state == ProtocolState.FINISHED, (
            f"Protocol state is {runner.protocol.state.name}, expected FINISHED. "
            "ETST:DONE was missed — the runner would exit without clean completion."
        )

    def test_done_missed_when_pio_finishes_first(self):
        """BUG REPRODUCTION: PIO declares finished before ETST:DONE arrives.

        When PIO's test_suite.is_finished() returns True (from doctest
        summary), the old runner would break out of the main loop. If
        ETST:DONE hasn't arrived yet, it gets lost.

        This test feeds the doctest summary, marks PIO as finished, then
        feeds ETST:DONE. The protocol must still reach FINISHED.
        """
        runner = make_runner()
        runner._line_buf = ""

        # Phase 1: normal test output
        runner._on_serial_data((_crc("ETST:READY") + "\n").encode())
        runner.protocol.command_sent()
        runner._on_serial_data(
            (_crc('ETST:CASE:START suite="S" name="t1"') + "\n").encode()
        )
        runner._on_serial_data(b"test output\n")

        assert runner.protocol.state == ProtocolState.RUNNING

        # Phase 2: PIO marks suite as finished (doctest summary seen)
        runner.test_suite._finished = True

        # Phase 3: ETST:DONE arrives — must still be processed
        runner._on_serial_data((_crc("ETST:DONE") + "\n").encode())

        assert runner.protocol.state == ProtocolState.FINISHED, (
            "ETST:DONE was not processed after PIO declared finished. "
            "The runner's _on_serial_data must process lines regardless "
            "of PIO's is_finished() state."
        )

    def test_run_cycle_waits_for_done(self):
        """_run_test_cycle must not exit until ETST:DONE is received.

        Simulates the real serial read loop with a mock serial port.
        The doctest summary arrives, then ETST:DONE comes 0.5s later.
        The runner must wait for DONE rather than exiting early.
        """
        runner = make_runner()
        runner._line_buf = ""

        # Build the serial data stream
        ready = _crc("ETST:READY") + "\n"
        test_start = _crc('ETST:CASE:START suite="S" name="t1"') + "\n"
        test_output = "test/foo.cpp:10: SUCCESS: CHECK( true )\n"
        doctest_summary = (
            "[doctest] test cases: 1 | 1 passed | 0 failed\n"
            "[doctest] Status: SUCCESS!\n"
        )
        done = _crc("ETST:DONE") + "\n"

        # Mock serial that delivers DONE 0.2s after doctest summary
        mock = MockSerial()
        mock.schedule(0.0, ready)
        mock.schedule(0.1, test_start + test_output)
        mock.schedule(0.2, doctest_summary)
        # Gap here — runner must not exit during this gap
        mock.schedule(0.5, done)

        runner._ser = mock
        runner._open_serial = lambda reset=True: None  # skip real serial
        runner._close_serial = lambda: None
        runner._send_command = lambda cmd: runner.protocol.command_sent()

        # Run the cycle in a thread with timeout
        result = {"state": None, "error": None}
        def run():
            try:
                runner._run_test_cycle(command="RUN_ALL", reset=False)
                result["state"] = runner.protocol.state
            except Exception as e:
                result["error"] = e

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=5)

        assert not t.is_alive(), "runner hung — _run_test_cycle did not return"
        assert result["error"] is None, f"runner raised: {result['error']}"
        assert result["state"] == ProtocolState.FINISHED, (
            f"Protocol state is {result['state']}, expected FINISHED. "
            "The runner exited _run_test_cycle before receiving ETST:DONE."
        )

    def test_done_never_arrives_protocol_stays_running(self):
        """If ETST:DONE never arrives, protocol stays RUNNING.

        The runner should detect this (via drain timeout) and report
        an incomplete run rather than silently declaring success.
        """
        runner = make_runner()
        runner._line_buf = ""

        runner._on_serial_data((_crc("ETST:READY") + "\n").encode())
        runner.protocol.command_sent()
        runner._on_serial_data(b"test output\n")
        runner._on_serial_data(
            b"[doctest] test cases: 1 | 1 passed | 0 failed\n"
        )
        # No ETST:DONE sent

        assert runner.protocol.state == ProtocolState.RUNNING
        # PIO thinks it's done, but protocol doesn't
        runner.test_suite._finished = True
        assert runner.protocol.state == ProtocolState.RUNNING


class TestIntegration:
    def test_receivers_process_full_session(self):
        """All receivers correctly process a complete test session."""
        runner = make_runner()

        lines = [
            _crc("ETST:READY"),
            _crc('ETST:CASE:START suite="Suite" name="test1"'),
            _crc("ETST:MEM:BEFORE free=200000 min=180000"),
            "  CHECK( true ) is correct!",
            _crc("ETST:MEM:AFTER free=199800 delta=-200 min=179800"),
            _crc('ETST:CASE:START suite="Suite" name="test2"'),
            _crc("ETST:MEM:BEFORE free=199800 min=179800"),
            "  CHECK( true ) is correct!",
            _crc("ETST:MEM:AFTER free=199600 delta=-200 min=179600"),
            _crc("ETST:DONE"),
        ]

        for line in lines:
            runner.on_testing_line_output(line + "\n")
            if runner.protocol.state == ProtocolState.READY:
                runner.protocol.command_sent()

        assert runner.protocol.state == ProtocolState.FINISHED
        assert len(runner.memory_tracker.all_tests) == 2

    def test_disconnect_tracked_during_session(self):
        """Disconnect/reconnect events are tracked through the session."""
        runner = make_runner()

        lines = [
            _crc("ETST:READY"),
            _crc("ETST:DISCONNECT ms=5000"),
            "garbage during disconnect",
            _crc("ETST:RECONNECT"),
            _crc("ETST:DONE"),
        ]

        for line in lines:
            runner.on_testing_line_output(line + "\n")
            if runner.protocol.state == ProtocolState.READY:
                runner.protocol.command_sent()

        assert runner.protocol.state == ProtocolState.FINISHED
        assert not runner.disconnect_handler.active

    def test_crash_during_test(self):
        runner = make_runner()

        runner.on_testing_line_output("test/main.c:10:test_init:PASS\n")
        runner.on_testing_line_output("Guru Meditation Error: Core 0 panic\n")
        for i in range(20):
            runner.on_testing_line_output(f"  0x{i:08x}\n")

        assert runner.test_suite._finished
        errored = [c for c in runner.test_suite.cases if c.status == MockTestStatus.ERRORED]
        assert len(errored) == 1
        assert isinstance(errored[0].exception, RuntimeError)
