"""Per-test duration tracking from ``ETST:CASE:START`` markers.

Parses test start markers emitted by the test framework's listener
and tracks wall-clock duration for each test case.
"""

import logging
import time
from typing import Callable

from .protocol import parse_line, parse_payload

logger = logging.getLogger(__name__)


class TestTimingTracker:
    """Tracks per-test duration from ``ETST:CASE:START`` markers.

    Each ``ETST:CASE:START suite=... name=...`` line starts a timer for
    that test. The timer stops when the next test starts or when
    ``finalize()`` is called.

    Args:
        slow_threshold: Tests longer than this (seconds) are flagged
            as slow. Default 5.0.
        clock: Callable returning monotonic time. Injectable for testing.
    """

    def __init__(
        self,
        slow_threshold: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._slow_threshold = slow_threshold
        self._clock = clock
        self._current_test: str = ""
        self._test_start_time: float | None = None
        self._durations: dict[str, float] = {}

    def feed(self, message: bytes | str) -> None:
        """Feed a line of device output."""
        line = (
            message.decode("utf-8", errors="replace")
            if isinstance(message, bytes)
            else message
        )

        parsed = parse_line(line)
        if parsed and parsed.tag == "CASE:START" and parsed.crc_valid is not False:
            payload = parse_payload(parsed.payload_str)
            suite = payload.get("suite", "")
            name = payload.get("name", "")
            if suite and name and isinstance(suite, str) and isinstance(name, str):
                self._finalize_current()
                self._current_test = f"{suite}/{name}"
                self._test_start_time = self._clock()

    def finalize(self) -> None:
        """Finalize the current test's duration (call at end of run)."""
        self._finalize_current()

    def _finalize_current(self) -> None:
        if self._current_test and self._test_start_time is not None:
            duration = self._clock() - self._test_start_time
            self._durations[self._current_test] = duration
        self._current_test = ""
        self._test_start_time = None

    @property
    def durations(self) -> dict[str, float]:
        """All tracked test durations (test_full_name -> seconds)."""
        return dict(self._durations)

    @property
    def slow_tests(self) -> dict[str, float]:
        """Tests exceeding the slow threshold."""
        return {
            name: duration
            for name, duration in self._durations.items()
            if duration > self._slow_threshold
        }

    def report(self) -> str:
        """Formatted slow test summary. Empty string if none."""
        slow = sorted(self.slow_tests.items(), key=lambda x: x[1], reverse=True)
        if not slow:
            return ""
        lines = [f"Slow Tests (>{self._slow_threshold:.0f}s):"]
        for name, duration in slow:
            lines.append(f"  {name}: {duration:.1f}s")
        return "\n".join(lines)

    def reset(self) -> None:
        """Clear all tracked data."""
        self._current_test = ""
        self._test_start_time = None
        self._durations.clear()
