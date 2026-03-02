"""READY/RUN/DONE bidirectional test orchestration protocol.

Parses protocol sentinels from device serial output. The protocol
supports test filtering and deep sleep orchestration:

1. Device boots, prints ``PTR:READY``
2. Host sends ``RUN_ALL`` or ``RUN: <filter>``
3. Device runs tests, may print ``PTR:SLEEP ms=<N>`` for deep sleep
4. Device prints ``PTR:DONE`` when finished

The handler is a pure receiver — it parses state but does not send.
The test runner reads its state to decide when to send commands.
"""

import enum
import logging

from .protocol import parse_line, parse_payload

logger = logging.getLogger(__name__)


class ProtocolState(enum.Enum):
    """Protocol state machine states."""

    WAITING_FOR_READY = enum.auto()
    READY = enum.auto()
    RUNNING = enum.auto()
    SLEEPING = enum.auto()
    FINISHED = enum.auto()


class ReadyRunProtocol:
    """Parses PTR:READY/DONE/SLEEP/TEST:START protocol from device output.

    State transitions::

        WAITING_FOR_READY → READY (on "PTR:READY" line)
        READY → RUNNING (on ``command_sent()`` call)
        RUNNING → SLEEPING (on "PTR:SLEEP" line)
        RUNNING → FINISHED (on "PTR:DONE" line)
        SLEEPING → WAITING_FOR_READY (on ``reset_for_wake()``)
    """

    def __init__(self) -> None:
        self._state = ProtocolState.WAITING_FOR_READY
        self._sleep_duration_ms: int = 0
        self._current_test_suite: str = ""
        self._current_test_name: str = ""
        self._sleeping_test_name: str = ""

    def feed(self, message: bytes | str) -> None:
        """Feed a line of device output."""
        line = (
            message.decode("utf-8", errors="replace")
            if isinstance(message, bytes)
            else message
        )
        line_stripped = line.strip()

        parsed = parse_line(line_stripped)

        if self._state == ProtocolState.WAITING_FOR_READY:
            if parsed and parsed.tag == "READY" and parsed.crc_valid is not False:
                self._state = ProtocolState.READY
                logger.info("Device ready")
            return

        if self._state != ProtocolState.RUNNING:
            return

        if not parsed:
            return
        if parsed.crc_valid is False:
            logger.warning("CRC mismatch, ignoring: %s", parsed.raw)
            return

        # Track test names for sleep attribution
        if parsed.tag == "TEST:START":
            payload = parse_payload(parsed.payload_str)
            suite = payload.get("suite", "")
            name = payload.get("name", "")
            if suite and isinstance(suite, str):
                self._current_test_suite = suite
            if name and isinstance(name, str):
                self._current_test_name = name

        # Check for sleep sentinel
        elif parsed.tag == "SLEEP":
            payload = parse_payload(parsed.payload_str)
            ms_str = payload.get("ms", "0")
            self._sleep_duration_ms = int(ms_str) if isinstance(ms_str, str) else 0
            self._sleeping_test_name = self._current_test_name
            self._state = ProtocolState.SLEEPING
            logger.info(
                "Sleep requested: %dms (test: %s)",
                self._sleep_duration_ms,
                self._sleeping_test_name,
            )

        # Check for completion
        elif parsed.tag == "DONE":
            self._state = ProtocolState.FINISHED
            logger.info("Device reported DONE")

    def command_sent(self) -> None:
        """Signal that the host has sent a RUN command.

        Transitions from READY to RUNNING.
        """
        if self._state == ProtocolState.READY:
            self._state = ProtocolState.RUNNING

    def reset_for_wake(self) -> None:
        """Reset protocol state for a wake cycle after sleep.

        Transitions from SLEEPING back to WAITING_FOR_READY.
        """
        if self._state == ProtocolState.SLEEPING:
            self._state = ProtocolState.WAITING_FOR_READY

    @property
    def state(self) -> ProtocolState:
        """Current protocol state."""
        return self._state

    @property
    def sleep_duration_ms(self) -> int:
        """Requested sleep duration in milliseconds."""
        return self._sleep_duration_ms

    @property
    def sleeping_test_name(self) -> str:
        """Name of the test that requested sleep."""
        return self._sleeping_test_name

    @property
    def current_test_suite(self) -> str:
        """Current test suite name from PTR:TEST:START markers."""
        return self._current_test_suite

    @property
    def current_test_name(self) -> str:
        """Current test name from PTR:TEST:START markers."""
        return self._current_test_name

    @property
    def current_test_full(self) -> str:
        """Full test identifier (suite/name)."""
        if self._current_test_suite and self._current_test_name:
            return f"{self._current_test_suite}/{self._current_test_name}"
        return ""

    def reset(self) -> None:
        """Reset all state."""
        self._state = ProtocolState.WAITING_FOR_READY
        self._sleep_duration_ms = 0
        self._current_test_suite = ""
        self._current_test_name = ""
        self._sleeping_test_name = ""
