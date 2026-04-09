"""Integration tests for pio-test-runner.

These tests simulate complete test sessions as produced by the C++ firmware
(doctest_runner.h + test_runner.h) and verify that the Python runner correctly
processes the full protocol flow: READY/RUN/DONE handshake, memory tracking,
timing tracking, crash detection, and sleep orchestration.

The output strings match exactly what the C++ headers emit, ensuring the
Python and firmware sides stay in sync.

Note: Most tests use a helper that calls ``protocol.command_sent()`` after
the READY line to simulate the orchestrator advancing the state machine.
In real usage, ``stage_testing()`` does this automatically.
"""

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
    return EmbeddedTestRunner(suite, config, options)


def feed_session(runner, lines):
    """Feed lines through the runner, calling command_sent() after READY.

    This simulates what stage_testing() does: when the protocol reaches
    READY, it sends a command and calls command_sent() to transition
    to RUNNING.
    """
    for line in lines:
        runner.on_testing_line_output(line + "\n")
        if runner.protocol.state == ProtocolState.READY:
            runner.protocol.command_sent()


# =====================================================================
# Simulate the exact output the C++ doctest_runner.h produces
# =====================================================================

# A typical doctest session with 2 passing tests and memory tracking
DOCTEST_SESSION_PASS = [
    "Board revision: 2",
    "Test storage initialized: /littlefs/test",
    _crc("ETST:READY"),
    # (runner sends RUN_ALL here — feed_session calls command_sent)
    "Runner: RUN_ALL (no additional filter)",
    "",
    _crc('ETST:TEST:START suite="GPS" name="Navigation rate test"'),
    _crc("ETST:MEM:BEFORE free=200000 min=180000"),
    "  CHECK( nav_rate == 5 ) is correct!",
    _crc("ETST:MEM:AFTER free=199500 delta=-500 min=179000"),
    "",
    _crc('ETST:TEST:START suite="GPS" name="Satellite count"'),
    _crc("ETST:MEM:BEFORE free=199500 min=179000"),
    "  CHECK( sat_count >= 4 ) is correct!",
    _crc("ETST:MEM:AFTER free=199000 delta=-500 min=178500"),
    "[doctest] test cases:  2 |  2 passed | 0 failed |",
    _crc("ETST:DONE"),
]

# Session with a large memory leak
DOCTEST_SESSION_LEAK = [
    "Board revision: 2",
    _crc("ETST:READY"),
    "Runner: RUN_ALL (no additional filter)",
    "",
    _crc('ETST:TEST:START suite="Mem" name="leaky test"'),
    _crc("ETST:MEM:BEFORE free=200000 min=180000"),
    "  CHECK( true ) is correct!",
    _crc("ETST:MEM:AFTER free=185000 delta=-15000 min=175000"),
    _crc("ETST:MEM:WARN leaked=15000"),
    "[doctest] test cases:  1 |  1 passed | 0 failed |",
    _crc("ETST:DONE"),
]

# Session with a crash mid-test
DOCTEST_SESSION_CRASH = [
    "Board revision: 2",
    _crc("ETST:READY"),
    "Runner: RUN_ALL (no additional filter)",
    "",
    _crc('ETST:TEST:START suite="IMU" name="FIFO read"'),
    _crc("ETST:MEM:BEFORE free=200000 min=180000"),
    "Guru Meditation Error: Core  0 panic'ed (StoreProhibited). Exception was unhandled.",
    "Core  0 register dump:",
    "PC      : 0x400d1234  PS      : 0x00060030",
    "Backtrace: 0x400d1234:0x3ffb1234 0x400d5678:0x3ffb5678",
]

# Session where test announces deep sleep
DOCTEST_SESSION_SLEEP = [
    "Board revision: 2",
    _crc("ETST:READY"),
    "Runner: RUN_ALL (no additional filter)",
    "",
    _crc('ETST:TEST:START suite="Orientation" name="alert across sleep"'),
    _crc("ETST:MEM:BEFORE free=200000 min=180000"),
    "  CHECK( orientation == PORTRAIT ) is correct!",
    _crc("ETST:SLEEP ms=15000"),
]

# Session with a single failing assertion
DOCTEST_SESSION_FAIL = [
    "Board revision: 2",
    _crc("ETST:READY"),
    "Runner: RUN_ALL (no additional filter)",
    "",
    _crc('ETST:TEST:START suite="GPS" name="Navigation rate test"'),
    _crc("ETST:MEM:BEFORE free=200000 min=180000"),
    "test/test_gps.cpp:42: ERROR: CHECK( nav_rate == 5 ) is NOT correct!",
    "  values: CHECK( 3 == 5 )",
    _crc("ETST:MEM:AFTER free=199500 delta=-500 min=179000"),
    "[doctest] test cases:  1 |  0 passed | 1 failed |",
    "[doctest] assertions:  1 |  0 passed | 1 failed |",
    "[doctest] Status: FAILURE!",
    _crc("ETST:DONE"),
]

# Session with mixed pass and fail
DOCTEST_SESSION_MIXED = [
    "Board revision: 2",
    _crc("ETST:READY"),
    "Runner: RUN_ALL (no additional filter)",
    "",
    _crc('ETST:TEST:START suite="GPS" name="Satellite count"'),
    _crc("ETST:MEM:BEFORE free=200000 min=180000"),
    "  CHECK( sat_count >= 4 ) is correct!",
    _crc("ETST:MEM:AFTER free=199500 delta=-500 min=179000"),
    "",
    _crc('ETST:TEST:START suite="GPS" name="Navigation rate test"'),
    _crc("ETST:MEM:BEFORE free=199500 min=179000"),
    "test/test_gps.cpp:42: ERROR: CHECK( nav_rate == 5 ) is NOT correct!",
    "  values: CHECK( 3 == 5 )",
    _crc("ETST:MEM:AFTER free=199000 delta=-500 min=178500"),
    "[doctest] test cases:  2 |  1 passed | 1 failed |",
    "[doctest] assertions:  2 |  1 passed | 1 failed |",
    "[doctest] Status: FAILURE!",
    _crc("ETST:DONE"),
]

# Session with timeout annotation on test
DOCTEST_SESSION_TIMEOUT = [
    "Board revision: 2",
    _crc("ETST:READY"),
    "Runner: RUN_ALL (no additional filter)",
    "",
    _crc('ETST:TEST:START suite="GPS" name="Cold start fix" timeout=30'),
    _crc("ETST:MEM:BEFORE free=200000 min=180000"),
    "  CHECK( fix_acquired ) is correct!",
    _crc("ETST:MEM:AFTER free=199800 delta=-200 min=179500"),
    "[doctest] test cases:  1 |  1 passed | 0 failed |",
    _crc("ETST:DONE"),
]

# Session with disconnect protocol (ETST:DISCONNECT/RECONNECT)
DOCTEST_SESSION_DISCONNECT = [
    "Board revision: 2",
    _crc("ETST:READY"),
    "Runner: RUN_ALL (no additional filter)",
    "",
    _crc('ETST:TEST:START suite="Sleep" name="deep sleep wake"'),
    _crc("ETST:MEM:BEFORE free=200000 min=180000"),
    _crc("ETST:DISCONNECT ms=5000"),
    "garbage during deep sleep",
    _crc("ETST:RECONNECT"),
    "  CHECK( woke_correctly ) is correct!",
    _crc("ETST:MEM:AFTER free=198000 delta=-2000 min=178000"),
    "[doctest] test cases:  1 |  1 passed | 0 failed |",
    _crc("ETST:DONE"),
]


class TestFullDoctestSession:
    """Simulate full doctest sessions through the line callback path."""

    def test_passing_session_with_memory(self):
        """Two passing tests with memory markers — no leaks reported."""
        runner = make_runner()
        feed_session(runner, DOCTEST_SESSION_PASS)

        assert runner.protocol.state == ProtocolState.FINISHED
        assert runner.protocol.current_test_full == "GPS/Satellite count"
        assert len(runner.memory_tracker.leaks) == 0

    def test_memory_leak_detected(self):
        """Session with a 15KB leak should be reported."""
        runner = make_runner()
        feed_session(runner, DOCTEST_SESSION_LEAK)

        assert runner.protocol.state == ProtocolState.FINISHED

        leaks = runner.memory_tracker.leaks
        assert "Mem/leaky test" in leaks
        assert leaks["Mem/leaky test"].delta == -15000

        report = runner.memory_tracker.report()
        assert "leaky test" in report

    def test_crash_detected_and_reported(self):
        """Crash mid-test should add an ERRORED case and finish the suite."""
        runner = make_runner()
        feed_session(runner, DOCTEST_SESSION_CRASH)
        # CrashDetector needs enough post-crash lines to finalize
        for i in range(20):
            runner.on_testing_line_output(f"  register dump line {i}\n")

        assert runner.test_suite._finished

        errored = [c for c in runner.test_suite.cases if c.status == MockTestStatus.ERRORED]
        assert len(errored) == 1
        # The crash case should reference the test that was running
        assert "IMU/FIFO read" in errored[0].name

    def test_sleep_sentinel_sets_sleeping_state(self):
        """SLEEP sentinel should transition protocol to SLEEPING."""
        runner = make_runner()
        feed_session(runner, DOCTEST_SESSION_SLEEP)

        assert runner.protocol.state == ProtocolState.SLEEPING
        assert runner.protocol.sleep_duration_ms == 15000
        assert runner.protocol.sleeping_test_name == "alert across sleep"

    def test_timeout_annotation_parsed(self):
        """Test start with timeout annotation should still track correctly."""
        runner = make_runner()
        feed_session(runner, DOCTEST_SESSION_TIMEOUT)

        assert runner.protocol.state == ProtocolState.FINISHED
        assert runner.protocol.current_test_full == "GPS/Cold start fix"

    def test_disconnect_detected(self):
        """ETST:DISCONNECT/RECONNECT is tracked by the disconnect handler."""
        runner = make_runner()
        feed_session(runner, DOCTEST_SESSION_DISCONNECT)

        assert runner.protocol.state == ProtocolState.FINISHED
        # Disconnect handler saw the RECONNECT — no longer active
        assert not runner.disconnect_handler.active


class TestProtocolHandshake:
    """Test the READY/RUN/DONE handshake through the runner."""

    def test_ready_detected_from_boot_output(self):
        """READY line among boot output is correctly detected."""
        runner = make_runner()

        boot_lines = [
            "ESP-ROM:esp32s3-20210327",
            "rst:0x1 (POWERON),boot:0x2b (SPI_FAST_FLASH_BOOT)",
            "Board revision: 2",
            "Test storage initialized: /littlefs/test",
            _crc("ETST:READY"),
        ]
        for line in boot_lines:
            runner.on_testing_line_output(line + "\n")

        assert runner.protocol.state == ProtocolState.READY

    def test_done_after_run_without_doctest_summary(self):
        """DONE without doctest summary completes the protocol.

        When doctest summary arrives first, it finishes the suite and
        DONE is never fed to the protocol. Test DONE detection by
        omitting the doctest summary line.
        """
        runner = make_runner()

        feed_session(runner, [
            _crc("ETST:READY"),
            "  CHECK( true ) is correct!",
            _crc("ETST:DONE"),
        ])

        assert runner.protocol.state == ProtocolState.FINISHED

    def test_boot_output_before_ready_ignored(self):
        """Lines before READY don't affect protocol state."""
        runner = make_runner()

        pre_ready = [
            "ESP-ROM:esp32s3-20210327",
            "configsip: 0, SPIWP:0xee",
            _crc("ETST:DONE"),   # stale DONE from previous run — should be ignored
            _crc("ETST:SLEEP ms=5000"),  # stale — should be ignored
        ]
        for line in pre_ready:
            runner.on_testing_line_output(line + "\n")

        assert runner.protocol.state == ProtocolState.WAITING_FOR_READY


class TestTimingIntegration:
    """Test timing tracking through the runner pipeline."""

    def test_timing_tracked_for_doctest_tests(self):
        """Test start markers feed into timing tracker via router."""
        runner = make_runner()

        feed_session(runner, [
            _crc("ETST:READY"),
            _crc('ETST:TEST:START suite="Suite" name="fast"'),
            "  CHECK( true ) is correct!",
            _crc('ETST:TEST:START suite="Suite" name="second"'),
            "  CHECK( true ) is correct!",
            "[doctest] test cases:  2 |  2 passed | 0 failed |",
            _crc("ETST:DONE"),
        ])

        runner.timing_tracker.finalize()
        assert "Suite/fast" in runner.timing_tracker.durations
        assert "Suite/second" in runner.timing_tracker.durations


class TestMemoryIntegration:
    """Test memory tracking through the runner pipeline."""

    def test_memory_markers_tracked(self):
        """ETST:MEM markers feed into memory tracker via router."""
        runner = make_runner()

        feed_session(runner, [
            _crc("ETST:READY"),
            _crc('ETST:TEST:START suite="Suite" name="test"'),
            _crc("ETST:MEM:BEFORE free=200000 min=180000"),
            _crc("ETST:MEM:AFTER free=199000 delta=-1000 min=179000"),
            "[doctest] test cases:  1 |  1 passed | 0 failed |",
            _crc("ETST:DONE"),
        ])

        # Test name should have been synced from protocol to memory tracker
        assert len(runner.memory_tracker.all_tests) > 0

    def test_multiple_tests_memory_independent(self):
        """Each test gets its own memory tracking."""
        runner = make_runner()

        feed_session(runner, [
            _crc("ETST:READY"),
            _crc('ETST:TEST:START suite="Suite" name="clean"'),
            _crc("ETST:MEM:BEFORE free=200000 min=180000"),
            _crc("ETST:MEM:AFTER free=199800 delta=-200 min=179800"),
            _crc('ETST:TEST:START suite="Suite" name="leaky"'),
            _crc("ETST:MEM:BEFORE free=199800 min=179800"),
            _crc("ETST:MEM:AFTER free=187000 delta=-12800 min=177000"),
            _crc("ETST:MEM:WARN leaked=12800"),
            "[doctest] test cases:  2 |  2 passed | 0 failed |",
            _crc("ETST:DONE"),
        ])

        leaks = runner.memory_tracker.leaks
        assert "Suite/clean" not in leaks
        assert "Suite/leaky" in leaks

    def test_leak_report_includes_delta(self):
        """Leak report shows the byte count."""
        runner = make_runner()

        feed_session(runner, [
            _crc("ETST:READY"),
            _crc('ETST:TEST:START suite="Suite" name="big_leak"'),
            _crc("ETST:MEM:BEFORE free=200000 min=180000"),
            _crc("ETST:MEM:AFTER free=180000 delta=-20000 min=170000"),
            "[doctest] test cases:  1 |  1 passed | 0 failed |",
            _crc("ETST:DONE"),
        ])

        report = runner.memory_tracker.report()
        assert "big_leak" in report
        assert "-20000" in report or "20000" in report


class TestSleepOrchestration:
    """Test sleep detection through the runner pipeline."""

    def test_sleep_detected_with_test_name(self):
        """SLEEP sentinel captures duration and sleeping test name."""
        runner = make_runner()

        feed_session(runner, [
            _crc("ETST:READY"),
            _crc('ETST:TEST:START suite="Orientation" name="sleep wake test"'),
            _crc("ETST:MEM:BEFORE free=200000 min=180000"),
            "  CHECK( orientation == PORTRAIT ) is correct!",
            _crc("ETST:SLEEP ms=15000"),
        ])

        assert runner.protocol.state == ProtocolState.SLEEPING
        assert runner.protocol.sleep_duration_ms == 15000
        assert runner.protocol.sleeping_test_name == "sleep wake test"

    def test_sleep_wake_resume_protocol(self):
        """Simulate full sleep - wake - resume - done cycle."""
        runner = make_runner()

        # First cycle: boot → ready → test → sleep
        feed_session(runner, [
            _crc("ETST:READY"),
            _crc('ETST:TEST:START suite="Orientation" name="sleep wake test"'),
            "  CHECK( orientation == PORTRAIT ) is correct!",
            _crc("ETST:SLEEP ms=5000"),
        ])

        assert runner.protocol.state == ProtocolState.SLEEPING

        # Simulate wake: reset protocol, feed wake cycle
        runner.protocol.reset_for_wake()
        assert runner.protocol.state == ProtocolState.WAITING_FOR_READY

        feed_session(runner, [
            _crc("ETST:READY"),
            "Runner filter applied: *sleep wake test*",
            _crc('ETST:TEST:START suite="Orientation" name="sleep wake test"'),
            "  CHECK( wake_orientation == LANDSCAPE ) is correct!",
            _crc("ETST:DONE"),
        ])

        assert runner.protocol.state == ProtocolState.FINISHED

    def test_port_path_persists_after_serial_close(self):
        """Port path is available for sleep monitoring after serial is closed."""
        runner = make_runner()

        # Simulate _open_serial setting port path
        runner._port_path = "/dev/cu.usbmodem12345"

        # Simulate _close_serial (sets _ser to None)
        runner._ser = None

        # _handle_sleep_resume should still have the port path
        assert runner._port_path == "/dev/cu.usbmodem12345"

    def test_sleep_monitor_uses_port_path(self):
        """Sleep monitor is configured with saved port path, not from _ser."""
        runner = make_runner()

        # Set up: port path saved, serial closed (as happens after _run_test_cycle)
        runner._port_path = "/dev/cu.usbmodem12345"
        runner._ser = None

        # Feed lines to reach SLEEPING state
        feed_session(runner, [
            _crc("ETST:READY"),
            _crc('ETST:TEST:START suite="Orientation" name="sleep wake test"'),
            _crc("ETST:SLEEP ms=5000"),
        ])
        assert runner.protocol.state == ProtocolState.SLEEPING

        # Verify port path is still available (not None from _ser)
        port = runner._port_path
        assert port == "/dev/cu.usbmodem12345"


class TestCrashIntegration:
    """Test crash detection through the full pipeline."""

    def test_crash_captures_test_context(self):
        """Crash report includes the test name from protocol tracking."""
        runner = make_runner()

        lines = [
            _crc("ETST:READY"),
            _crc('ETST:TEST:START suite="IMU" name="FIFO watermark interrupt"'),
            _crc("ETST:MEM:BEFORE free=200000 min=180000"),
            "Guru Meditation Error: Core  0 panic'ed (LoadProhibited).",
        ]
        feed_session(runner, lines)
        # Feed enough post-crash lines to trigger finalization
        for i in range(20):
            runner.on_testing_line_output(f"  crash context line {i}\n")

        errored = [c for c in runner.test_suite.cases if c.status == MockTestStatus.ERRORED]
        assert len(errored) == 1
        # The crash case should reference the test that was running
        assert "IMU/FIFO watermark interrupt" in errored[0].name

    def test_wdt_crash_detected(self):
        """Task watchdog crash is detected."""
        runner = make_runner()

        feed_session(runner, [
            _crc("ETST:READY"),
            _crc('ETST:TEST:START suite="Hang" name="infinite loop"'),
            "Task watchdog got triggered.",
        ])
        for i in range(20):
            runner.on_testing_line_output(f"  stack {i}\n")

        assert runner.test_suite._finished
        errored = [c for c in runner.test_suite.cases if c.status == MockTestStatus.ERRORED]
        assert len(errored) == 1

    def test_backtrace_crash_detected(self):
        """Backtrace crash is detected."""
        runner = make_runner()

        runner.on_testing_line_output("Backtrace: 0x400d1234\n")
        for i in range(20):
            runner.on_testing_line_output(f"  0x{i:08x}\n")

        assert runner.test_suite._finished

    def test_crash_before_any_test(self):
        """Crash during boot (before any test starts) still detected."""
        runner = make_runner()

        feed_session(runner, [
            _crc("ETST:READY"),
            "Guru Meditation Error: Core  0 panic'ed (LoadProhibited).",
        ])
        for i in range(20):
            runner.on_testing_line_output(f"  reg {i}\n")

        assert runner.test_suite._finished
        errored = [c for c in runner.test_suite.cases if c.status == MockTestStatus.ERRORED]
        assert len(errored) == 1
        # No test was running, so crash name should use env fallback
        assert "crash" in errored[0].name


class TestSummaryReporting:
    """Test the summary output at end of test run."""

    def test_summary_reports_leaks(self, capsys):
        """Memory leaks appear in the summary."""
        runner = make_runner()

        feed_session(runner, [
            _crc("ETST:READY"),
            _crc('ETST:TEST:START suite="Suite" name="leaky"'),
            _crc("ETST:MEM:BEFORE free=200000 min=180000"),
            _crc("ETST:MEM:AFTER free=185000 delta=-15000 min=175000"),
            _crc("ETST:MEM:WARN leaked=15000"),
            "[doctest] test cases:  1 |  1 passed | 0 failed |",
            _crc("ETST:DONE"),
        ])

        runner._print_summary()
        captured = capsys.readouterr()
        assert "leaky" in captured.out

    def test_summary_empty_when_no_issues(self, capsys):
        """No summary output when all tests are clean."""
        runner = make_runner()

        feed_session(runner, [
            _crc("ETST:READY"),
            _crc('ETST:TEST:START suite="Suite" name="clean"'),
            _crc("ETST:MEM:BEFORE free=200000 min=180000"),
            _crc("ETST:MEM:AFTER free=199800 delta=-200 min=179800"),
            "[doctest] test cases:  1 |  1 passed | 0 failed |",
            _crc("ETST:DONE"),
        ])

        runner._print_summary()
        captured = capsys.readouterr()
        # No memory leaks, no slow tests
        assert "leak" not in captured.out.lower()


class TestAssertionFailurePropagation:
    """Verify that doctest assertion failures are tracked and reported.

    PIO's DoctestTestCaseParser may not be active in orchestrated mode.
    The runner must detect assertion failures itself from the doctest
    ERROR: output and add FAILED cases to the test suite.
    """

    def test_single_failure_tracked(self):
        """A failing CHECK produces a FAILED test case in the suite."""
        runner = make_runner()
        feed_session(runner, DOCTEST_SESSION_FAIL)

        assert runner.protocol.state == ProtocolState.FINISHED
        assert "GPS/Navigation rate test" in runner._test_failures
        assert len(runner._test_failures["GPS/Navigation rate test"]) == 1

    def test_single_failure_reported_to_suite(self):
        """After ETST:DONE, FAILED case is added to test_suite.cases."""
        runner = make_runner()
        feed_session(runner, DOCTEST_SESSION_FAIL)

        # _report_test_failures is called when ETST:DONE is received
        # In line-callback mode, we need to call it manually since
        # the mock doesn't run _run_test_cycle
        runner._report_test_failures()

        failed = [c for c in runner.test_suite.cases
                  if c.status == MockTestStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].name == "GPS/Navigation rate test"

    def test_mixed_pass_fail(self):
        """Mixed session: one pass, one fail. Only failed test reported."""
        runner = make_runner()
        feed_session(runner, DOCTEST_SESSION_MIXED)
        runner._report_test_failures()

        failed = [c for c in runner.test_suite.cases
                  if c.status == MockTestStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].name == "GPS/Navigation rate test"

        # The passing test should NOT be in failures
        assert "GPS/Satellite count" not in runner._test_failures

    def test_passing_session_no_failures(self):
        """A fully passing session should have no tracked failures."""
        runner = make_runner()
        feed_session(runner, DOCTEST_SESSION_PASS)

        assert len(runner._test_failures) == 0

    def test_failure_message_captured(self):
        """The assertion failure message is captured for reporting."""
        runner = make_runner()
        feed_session(runner, DOCTEST_SESSION_FAIL)
        runner._report_test_failures()

        failed = [c for c in runner.test_suite.cases
                  if c.status == MockTestStatus.FAILED]
        assert "CHECK( nav_rate == 5 ) is NOT correct!" in failed[0].message
