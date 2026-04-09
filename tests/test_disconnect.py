"""Tests for DisconnectHandler."""

from embedded_bridge.receivers.base import Receiver

from pio_test_runner.disconnect import DisconnectHandler
from pio_test_runner.protocol import format_crc


def _crc(content: str) -> str:
    return format_crc(content)


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestProtocolParsing:
    def test_disconnect_message_parsed(self):
        handler = DisconnectHandler()
        handler.feed(_crc("ETST:DISCONNECT ms=5000"))
        assert handler.active is True
        assert handler.pending_duration == 5.0

    def test_reconnect_message_parsed(self):
        handler = DisconnectHandler()
        handler.feed(_crc("ETST:DISCONNECT ms=5000"))
        handler.feed(_crc("ETST:RECONNECT"))
        assert handler.active is False

    def test_non_protocol_message_ignored(self):
        handler = DisconnectHandler()
        handler.feed("Hello world")
        handler.feed("[doctest] test cases: 1 | 1 passed")
        handler.feed("Backtrace: 0x400d1234")
        assert handler.active is False

    def test_disconnect_duration_milliseconds_to_seconds(self):
        handler = DisconnectHandler()
        handler.feed(_crc("ETST:DISCONNECT ms=500"))
        assert handler.pending_duration == 0.5

    def test_disconnect_zero_duration(self):
        handler = DisconnectHandler()
        handler.feed(_crc("ETST:DISCONNECT ms=0"))
        assert handler.active is True
        assert handler.pending_duration == 0.0

    def test_bytes_input(self):
        handler = DisconnectHandler()
        handler.feed(_crc("ETST:DISCONNECT ms=3000").encode())
        assert handler.active is True
        assert handler.pending_duration == 3.0

    def test_line_with_whitespace_stripped(self):
        handler = DisconnectHandler()
        handler.feed(f"  {_crc('ETST:DISCONNECT ms=1000')}  \n")
        assert handler.active is True

    def test_invalid_crc_rejected(self):
        handler = DisconnectHandler()
        handler.feed("ETST:DISCONNECT ms=5000 *00")
        assert handler.active is False

    def test_no_crc_accepted(self):
        handler = DisconnectHandler()
        handler.feed("ETST:DISCONNECT ms=5000")
        assert handler.active is True


class TestStateTransitions:
    def test_idle_to_disconnected_to_idle(self):
        handler = DisconnectHandler()
        assert handler.active is False

        handler.feed(_crc("ETST:DISCONNECT ms=2000"))
        assert handler.active is True

        handler.feed(_crc("ETST:RECONNECT"))
        assert handler.active is False

    def test_disconnect_count_increments(self):
        handler = DisconnectHandler()
        assert handler.disconnect_count == 0

        handler.feed(_crc("ETST:DISCONNECT ms=1000"))
        handler.feed(_crc("ETST:RECONNECT"))
        assert handler.disconnect_count == 1

        handler.feed(_crc("ETST:DISCONNECT ms=2000"))
        handler.feed(_crc("ETST:RECONNECT"))
        assert handler.disconnect_count == 2

    def test_pending_duration_updates(self):
        handler = DisconnectHandler()
        handler.feed(_crc("ETST:DISCONNECT ms=1000"))
        assert handler.pending_duration == 1.0

        handler.feed(_crc("ETST:RECONNECT"))
        assert handler.pending_duration == 1.0

        handler.feed(_crc("ETST:DISCONNECT ms=5000"))
        assert handler.pending_duration == 5.0


class TestCallbacks:
    def test_on_disconnect_called(self):
        durations = []
        handler = DisconnectHandler(on_disconnect=durations.append)
        handler.feed(_crc("ETST:DISCONNECT ms=3000"))
        assert durations == [3.0]

    def test_on_reconnect_called(self):
        reconnects = []
        handler = DisconnectHandler(on_reconnect=lambda: reconnects.append(True))
        handler.feed(_crc("ETST:DISCONNECT ms=1000"))
        handler.feed(_crc("ETST:RECONNECT"))
        assert reconnects == [True]

    def test_on_disconnect_not_called_for_non_protocol(self):
        durations = []
        handler = DisconnectHandler(on_disconnect=durations.append)
        handler.feed("normal output")
        assert durations == []

    def test_on_reconnect_not_called_without_disconnect(self):
        reconnects = []
        handler = DisconnectHandler(on_reconnect=lambda: reconnects.append(True))
        handler.feed(_crc("ETST:RECONNECT"))
        assert reconnects == []

    def test_multiple_cycle_callbacks(self):
        disconnects = []
        reconnects = []
        handler = DisconnectHandler(
            on_disconnect=disconnects.append,
            on_reconnect=lambda: reconnects.append(True),
        )

        handler.feed(_crc("ETST:DISCONNECT ms=1000"))
        handler.feed(_crc("ETST:RECONNECT"))
        handler.feed(_crc("ETST:DISCONNECT ms=2000"))
        handler.feed(_crc("ETST:RECONNECT"))

        assert disconnects == [1.0, 2.0]
        assert reconnects == [True, True]


class TestEdgeCases:
    def test_reconnect_without_disconnect_ignored(self):
        handler = DisconnectHandler()
        handler.feed(_crc("ETST:RECONNECT"))
        assert handler.active is False
        assert handler.disconnect_count == 0

    def test_double_disconnect_overwrites(self):
        handler = DisconnectHandler()
        handler.feed(_crc("ETST:DISCONNECT ms=1000"))
        handler.feed(_crc("ETST:DISCONNECT ms=5000"))
        assert handler.active is True
        assert handler.pending_duration == 5.0

    def test_reset_clears_state(self):
        handler = DisconnectHandler()
        handler.feed(_crc("ETST:DISCONNECT ms=1000"))
        handler.feed(_crc("ETST:RECONNECT"))
        assert handler.disconnect_count == 1

        handler.reset()
        assert handler.active is False
        assert handler.pending_duration == 0.0
        assert handler.disconnect_count == 0

    def test_non_protocol_during_disconnect(self):
        handler = DisconnectHandler()
        handler.feed(_crc("ETST:DISCONNECT ms=1000"))
        handler.feed("some output during disconnect")
        assert handler.active is True  # unchanged


class TestReceiverProtocol:
    def test_satisfies_receiver_protocol(self):
        handler = DisconnectHandler()
        assert isinstance(handler, Receiver)
