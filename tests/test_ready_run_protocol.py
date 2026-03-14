"""Tests for ReadyRunProtocol."""

from pio_test_runner.protocol import format_crc
from pio_test_runner.ready_run_protocol import ProtocolState, ReadyRunProtocol


def _crc(content: str) -> str:
    """Shorthand for format_crc."""
    return format_crc(content)


class TestReadyRunProtocol:
    def test_initial_state(self):
        p = ReadyRunProtocol()
        assert p.state == ProtocolState.WAITING_FOR_READY

    def test_ready_transition(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        assert p.state == ProtocolState.READY

    def test_ready_ignores_non_ready_lines(self):
        p = ReadyRunProtocol()
        p.feed("Board revision: 2")
        p.feed("some boot message")
        assert p.state == ProtocolState.WAITING_FOR_READY

    def test_ready_with_whitespace(self):
        p = ReadyRunProtocol()
        p.feed(f"  {_crc('PTR:READY')}  \n")
        assert p.state == ProtocolState.READY

    def test_command_sent_transitions_to_running(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        assert p.state == ProtocolState.RUNNING

    def test_done_transitions_to_finished(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc("PTR:DONE"))
        assert p.state == ProtocolState.FINISHED

    def test_sleep_transitions_to_sleeping(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc("PTR:SLEEP ms=15000"))
        assert p.state == ProtocolState.SLEEPING
        assert p.sleep_duration_ms == 15000

    def test_sleeping_test_name_tracked(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc('PTR:TEST:START suite="OrientationSleep" name="Orientation alert across sleep"'))
        p.feed(_crc("PTR:SLEEP ms=15000"))
        assert p.sleeping_test_name == "Orientation alert across sleep"

    def test_test_start_tracking(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc('PTR:TEST:START suite="GPS" name="Navigation rate test"'))
        assert p.current_test_suite == "GPS"
        assert p.current_test_name == "Navigation rate test"
        assert p.current_test_full == "GPS/Navigation rate test"

    def test_test_start_with_timeout(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc('PTR:TEST:START suite="GPS" name="Navigation rate test" timeout=30'))
        assert p.current_test_suite == "GPS"
        assert p.current_test_name == "Navigation rate test"

    def test_reset_for_wake(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc("PTR:SLEEP ms=15000"))
        assert p.state == ProtocolState.SLEEPING
        p.reset_for_wake()
        assert p.state == ProtocolState.WAITING_FOR_READY

    def test_full_sleep_wake_cycle(self):
        p = ReadyRunProtocol()
        # Cold boot
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc('PTR:TEST:START suite="Suite" name="sleep test"'))
        p.feed(_crc("PTR:SLEEP ms=5000"))
        assert p.state == ProtocolState.SLEEPING

        # Wake cycle
        p.reset_for_wake()
        p.feed(_crc("PTR:READY"))
        assert p.state == ProtocolState.READY
        p.command_sent()
        p.feed(_crc("PTR:DONE"))
        assert p.state == ProtocolState.FINISHED

    def test_lines_ignored_when_not_running(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:DONE"))  # Should be ignored in WAITING_FOR_READY
        assert p.state == ProtocolState.WAITING_FOR_READY
        p.feed(_crc("PTR:SLEEP ms=1000"))
        assert p.state == ProtocolState.WAITING_FOR_READY

    def test_bytes_input(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY").encode())
        assert p.state == ProtocolState.READY

    def test_reset_clears_all(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc('PTR:TEST:START suite="Suite" name="test"'))
        p.feed(_crc("PTR:SLEEP ms=5000"))
        p.reset()
        assert p.state == ProtocolState.WAITING_FOR_READY
        assert p.sleep_duration_ms == 0
        assert p.current_test_full == ""
        assert p.sleeping_test_name == ""

    def test_current_test_full_empty_before_any_test(self):
        p = ReadyRunProtocol()
        assert p.current_test_full == ""

    def test_invalid_crc_ignored(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        # Feed a line with bad CRC
        p.feed("PTR:DONE *00")
        assert p.state == ProtocolState.RUNNING  # not FINISHED

    def test_no_crc_accepted(self):
        p = ReadyRunProtocol()
        p.feed("PTR:READY")  # no CRC
        assert p.state == ProtocolState.READY

    def test_completed_tests_tracked(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc('PTR:TEST:START suite="S" name="test_a"'))
        p.feed(_crc('PTR:TEST:START suite="S" name="test_b"'))
        p.feed(_crc('PTR:TEST:START suite="S" name="test_c"'))
        assert p.completed_tests == ["test_a", "test_b", "test_c"]

    def test_completed_tests_no_duplicates(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc('PTR:TEST:START suite="S" name="test_a"'))
        p.feed(_crc('PTR:TEST:START suite="S" name="test_a"'))
        assert p.completed_tests == ["test_a"]

    def test_completed_tests_persist_across_reset(self):
        """reset() preserves completed_tests for resume-after exclude."""
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc('PTR:TEST:START suite="S" name="test_a"'))
        p.feed(_crc('PTR:TEST:START suite="S" name="sleep_test"'))
        p.feed(_crc("PTR:SLEEP ms=5000"))
        p.reset()
        assert p.completed_tests == ["test_a", "sleep_test"]

    def test_reset_all_clears_completed_tests(self):
        p = ReadyRunProtocol()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc('PTR:TEST:START suite="S" name="test_a"'))
        p.reset_all()
        assert p.completed_tests == []
        assert p.state == ProtocolState.WAITING_FOR_READY

    def test_completed_tests_across_sleep_wake_cycle(self):
        """Full cycle: run → sleep → resume → remaining."""
        p = ReadyRunProtocol()
        # Cycle 1: run until sleep
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc('PTR:TEST:START suite="S" name="test_a"'))
        p.feed(_crc('PTR:TEST:START suite="S" name="sleep_test"'))
        p.feed(_crc("PTR:SLEEP ms=5000"))
        assert p.completed_tests == ["test_a", "sleep_test"]

        # Cycle 2: resume sleep test
        p.reset_for_wake()
        p.reset()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc('PTR:TEST:START suite="S" name="sleep_test"'))
        p.feed(_crc("PTR:DONE"))
        # sleep_test seen again but not duplicated
        assert p.completed_tests == ["test_a", "sleep_test"]

        # Cycle 3: remaining tests (would use EXCLUDE with completed list)
        p.reset()
        p.feed(_crc("PTR:READY"))
        p.command_sent()
        p.feed(_crc('PTR:TEST:START suite="S" name="test_b"'))
        p.feed(_crc('PTR:TEST:START suite="S" name="test_c"'))
        p.feed(_crc("PTR:DONE"))
        assert p.completed_tests == ["test_a", "sleep_test", "test_b", "test_c"]
