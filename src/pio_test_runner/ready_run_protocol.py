"""READY/RUN/DONE bidirectional test orchestration protocol.

Parses protocol sentinels from device serial output. The protocol
supports test filtering and deep sleep orchestration:

1. Device boots, prints ``ETST:READY``
2. Host sends ``RUN_ALL``, ``RUN: <filter>``, or ``RESUME_AFTER: <name>``
3. Device runs tests, may print ``ETST:SLEEP ms=<N>`` for deep sleep
4. Device prints ``ETST:DONE`` when finished

The handler is a pure receiver — it parses state but does not send.
The test runner reads its state to decide when to send commands.
"""

import enum
import logging
import time

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
    """Parses ETST:READY/DONE/SLEEP/TEST:START protocol from device output.

    State transitions::

        WAITING_FOR_READY → READY (on "ETST:READY" line)
        READY → RUNNING (on ``command_sent()`` call)
        RUNNING → SLEEPING (on "ETST:SLEEP" line)
        RUNNING → FINISHED (on "ETST:DONE" line)
        SLEEPING → WAITING_FOR_READY (on ``reset_for_wake()``)
    """

    def __init__(self) -> None:
        self._state = ProtocolState.WAITING_FOR_READY
        self._sleep_duration_ms: int = 0
        self._current_test_suite: str = ""
        self._current_test_name: str = ""
        self._current_test_timeout: int = 0  # per-test timeout from doctest annotation
        self._sleeping_test_name: str = ""
        self._completed_tests: list[str] = []  # test names seen across all cycles
        self._test_total: int = 0
        self._test_skip: int = 0
        self._test_run: int = 0
        self._busy_until: float = 0  # monotonic time when busy period ends

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

        # If device sends READY while we think we're RUNNING, it means
        # our command was lost (e.g. stale byte corrupted CRC). Transition
        # back to READY so the runner re-sends.
        if parsed.tag == "READY" and not self._current_test_name:
            self._state = ProtocolState.READY
            logger.info("Device re-sent READY — command was lost, retrying")
            return

        # Track test names for sleep attribution and resume-after
        if parsed.tag == "CASE:START":
            payload = parse_payload(parsed.payload_str)
            suite = payload.get("suite", "")
            name = payload.get("name", "")
            timeout = payload.get("timeout", "")
            if suite and isinstance(suite, str):
                self._current_test_suite = suite
            if name and isinstance(name, str):
                self._current_test_name = name
                full = f"{self._current_test_suite}/{name}" if self._current_test_suite else name
                if full not in self._completed_tests:
                    self._completed_tests.append(full)
            # Per-test timeout from doctest::timeout(N) annotation
            self._current_test_timeout = int(timeout) if timeout else 0

        # Test count report
        elif parsed.tag == "COUNTS":
            payload = parse_payload(parsed.payload_str)
            self._test_total = int(payload.get("total", 0))
            self._test_skip = int(payload.get("skip", 0))
            self._test_run = int(payload.get("run", 0))

        # Check for sleep or restart sentinel
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

        elif parsed.tag == "BUSY":
            payload = parse_payload(parsed.payload_str)
            ms_str = payload.get("ms", "0")
            self._busy_until = time.monotonic() + int(ms_str) / 1000.0
            logger.info("Device busy for %sms", ms_str)

        elif parsed.tag == "RESTART":
            self._sleep_duration_ms = 0  # No sleep, just restart
            self._sleeping_test_name = self._current_test_name
            self._state = ProtocolState.SLEEPING  # Reuse sleep flow
            logger.info(
                "Restart requested (test: %s)",
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
        """Current test suite name from ETST:CASE:START markers."""
        return self._current_test_suite

    @property
    def current_test_name(self) -> str:
        """Current test name from ETST:CASE:START markers."""
        return self._current_test_name

    @property
    def current_test_timeout(self) -> int:
        """Per-test timeout in seconds from doctest::timeout(N), or 0 if unset."""
        return self._current_test_timeout

    @property
    def current_test_full(self) -> str:
        """Full test identifier (suite/name)."""
        if self._current_test_suite and self._current_test_name:
            return f"{self._current_test_suite}/{self._current_test_name}"
        return ""

    @property
    def is_busy(self) -> bool:
        """True if device signalled ETST:BUSY and the period hasn't expired."""
        return time.monotonic() < self._busy_until

    @property
    def test_total(self) -> int:
        """Total registered tests reported by device."""
        return self._test_total

    @property
    def test_skip(self) -> int:
        """Tests skipped (by RESUME_AFTER)."""
        return self._test_skip

    @property
    def test_run(self) -> int:
        """Tests to run this cycle."""
        return self._test_run

    @property
    def completed_tests(self) -> list[str]:
        """Test names seen across all cycles (for resume-after exclude)."""
        return list(self._completed_tests)

    def reset(self) -> None:
        """Reset all state for a fresh test cycle.

        Preserves completed_tests across cycles so the runner can build
        an EXCLUDE list for remaining-tests cycles after sleep resume.
        """
        self._state = ProtocolState.WAITING_FOR_READY
        self._sleep_duration_ms = 0
        self._current_test_suite = ""
        self._current_test_name = ""
        self._current_test_timeout = 0
        self._sleeping_test_name = ""
        self._test_total = 0
        self._test_skip = 0
        self._test_run = 0

    def reset_all(self) -> None:
        """Full reset including completed test history."""
        self.reset()
        self._completed_tests.clear()
