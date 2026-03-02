"""Tests for EmbeddedTestRunner."""

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
        runner.on_testing_line_output(_crc("PTR:DISCONNECT ms=5000") + "\n")

        assert runner.disconnect_handler.active

    def test_reconnect_clears_disconnect(self):
        runner = make_runner()
        runner.on_testing_line_output(_crc("PTR:DISCONNECT ms=1000") + "\n")
        assert runner.disconnect_handler.active

        runner.on_testing_line_output(_crc("PTR:RECONNECT") + "\n")
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


class TestIntegration:
    def test_receivers_process_full_session(self):
        """All receivers correctly process a complete test session."""
        runner = make_runner()

        lines = [
            _crc("PTR:READY"),
            _crc('PTR:TEST:START suite="Suite" name="test1"'),
            _crc("PTR:MEM:BEFORE free=200000 min=180000"),
            "  CHECK( true ) is correct!",
            _crc("PTR:MEM:AFTER free=199800 delta=-200 min=179800"),
            _crc('PTR:TEST:START suite="Suite" name="test2"'),
            _crc("PTR:MEM:BEFORE free=199800 min=179800"),
            "  CHECK( true ) is correct!",
            _crc("PTR:MEM:AFTER free=199600 delta=-200 min=179600"),
            _crc("PTR:DONE"),
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
            _crc("PTR:READY"),
            _crc("PTR:DISCONNECT ms=5000"),
            "garbage during disconnect",
            _crc("PTR:RECONNECT"),
            _crc("PTR:DONE"),
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
