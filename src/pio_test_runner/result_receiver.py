"""Framework-agnostic test result extraction.

Parses test output from doctest, Unity, or custom frameworks and
normalizes results into a common ``TestResult`` structure.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    """A single test case result.

    Args:
        name: Test case name.
        passed: True if the test passed.
        message: Failure/error message, if any.
        duration: Test duration in seconds, if available.
        source: Source location string (e.g. "file.cpp:42"), if available.
    """

    name: str
    passed: bool
    message: str | None = None
    duration: float | None = None
    source: str | None = None


# --- Doctest patterns ---

# Summary line: "[doctest] test cases:  1 |  1 passed | ..."
_DOCTEST_SUMMARY_RE = re.compile(
    r"\[doctest\] test cases:\s*(\d+)\s*\|\s*(\d+) passed"
)

# Failure header: "path/file.cpp:123: FAILED:"
_DOCTEST_FAIL_RE = re.compile(
    r"^(.+?:\d+):\s*FAILED:"
)

# Test case declaration: "TEST CASE:  name" (with optional leading whitespace)
_DOCTEST_TESTCASE_RE = re.compile(
    r"^\s*TEST CASE:\s+(.+?)$"
)

# Success line: "path/file.cpp:123: SUCCESS: CHECK( ... ) is correct!"
_DOCTEST_SUCCESS_RE = re.compile(
    r"^.+?:\d+:\s*SUCCESS:"
)

# --- Unity patterns ---

# Result line: "path/file.c:42:test_name:PASS" or "path/file.c:42:test_name:FAIL: message"
_UNITY_RESULT_RE = re.compile(
    r"^(.+?):(\d+):([^:]+):(PASS|FAIL|IGNORE)(?::\s*(.+))?$"
)

# Summary line: "N Tests N Failures N Ignored"
_UNITY_SUMMARY_RE = re.compile(
    r"(\d+) Tests\s+(\d+) Failures\s+(\d+) Ignored"
)


class TestResultReceiver:
    """Framework-agnostic test result extraction.

    Parses serial output from doctest, Unity, or auto-detects the
    framework. Results are buffered and retrieved via ``drain_results()``.

    Args:
        framework: ``"doctest"``, ``"unity"``, or ``"auto"`` (default).
            Auto mode detects the framework from the first matching line.
    """

    def __init__(self, framework: str = "auto") -> None:
        if framework not in ("doctest", "unity", "auto"):
            raise ValueError(f"Unknown framework: {framework!r}")
        self._framework = framework
        self._detected_framework: str | None = (
            None if framework == "auto" else framework
        )
        self._results: list[TestResult] = []
        self._is_complete: bool = False

        # Doctest state
        self._current_test_name: str | None = None
        self._current_test_passed: bool = False
        self._doctest_fail_source: str | None = None
        self._doctest_fail_lines: list[str] = []
        self._in_doctest_failure: bool = False

    def feed(self, message: bytes | str) -> None:
        """Feed a line of test output.

        Args:
            message: A line of device output.
        """
        line = (
            message.decode("utf-8", errors="replace")
            if isinstance(message, bytes)
            else message
        )
        line = line.rstrip("\n\r")

        if self._is_complete:
            return

        if self._detected_framework == "doctest":
            self._feed_doctest(line)
        elif self._detected_framework == "unity":
            self._feed_unity(line)
        elif self._detected_framework is None:
            # Auto-detect: try both
            if self._try_detect(line):
                return
            # No match yet — just buffer

    def drain_results(self) -> list[TestResult]:
        """Return buffered results and clear the buffer."""
        results = list(self._results)
        self._results.clear()
        return results

    @property
    def is_complete(self) -> bool:
        """True when a summary/completion line has been seen."""
        return self._is_complete

    def _try_detect(self, line: str) -> bool:
        """Try to detect framework from line. Returns True if detected."""
        # Check doctest markers
        if _DOCTEST_TESTCASE_RE.match(line) or _DOCTEST_SUMMARY_RE.search(line):
            self._detected_framework = "doctest"
            self._feed_doctest(line)
            return True
        if _DOCTEST_FAIL_RE.match(line):
            self._detected_framework = "doctest"
            self._feed_doctest(line)
            return True

        # Check Unity markers
        if _UNITY_RESULT_RE.match(line) or _UNITY_SUMMARY_RE.search(line):
            self._detected_framework = "unity"
            self._feed_unity(line)
            return True

        return False

    def _feed_doctest(self, line: str) -> None:
        """Parse doctest output."""
        # Check for test case declaration
        tc_match = _DOCTEST_TESTCASE_RE.match(line)
        if tc_match:
            self._finalize_doctest_case()
            self._current_test_name = tc_match.group(1).strip()
            self._current_test_passed = False
            return

        # Check for success assertion
        if _DOCTEST_SUCCESS_RE.match(line):
            self._current_test_passed = True

        # Check for failure
        fail_match = _DOCTEST_FAIL_RE.match(line)
        if fail_match:
            self._finalize_doctest_failure()
            self._doctest_fail_source = fail_match.group(1)
            self._in_doctest_failure = True
            return

        # Accumulate failure detail lines
        if self._in_doctest_failure:
            self._doctest_fail_lines.append(line)

        # Check for summary
        summary_match = _DOCTEST_SUMMARY_RE.search(line)
        if summary_match:
            self._finalize_doctest_case()
            total = int(summary_match.group(1))
            passed = int(summary_match.group(2))
            # If no individual results, synthesize from summary
            if not self._results and total > 0 and passed == total:
                self._results.append(TestResult(
                    name="all tests",
                    passed=True,
                ))
            self._is_complete = True

    def _finalize_doctest_case(self) -> None:
        """Finalize the current test case (pass or fail)."""
        self._finalize_doctest_failure()
        if self._current_test_name and self._current_test_passed:
            self._results.append(TestResult(
                name=self._current_test_name,
                passed=True,
            ))
        self._current_test_name = None
        self._current_test_passed = False

    def _finalize_doctest_failure(self) -> None:
        """Finalize a pending doctest failure."""
        if not self._in_doctest_failure:
            return
        name = self._current_test_name or "unknown"
        message = "\n".join(self._doctest_fail_lines).strip() or None
        self._results.append(TestResult(
            name=name,
            passed=False,
            message=message,
            source=self._doctest_fail_source,
        ))
        self._current_test_passed = False  # failure recorded, don't also record pass
        self._in_doctest_failure = False
        self._doctest_fail_source = None
        self._doctest_fail_lines = []

    def _feed_unity(self, line: str) -> None:
        """Parse Unity output."""
        # Check for result line
        result_match = _UNITY_RESULT_RE.match(line)
        if result_match:
            filename = result_match.group(1)
            lineno = result_match.group(2)
            name = result_match.group(3)
            status = result_match.group(4)
            message = result_match.group(5)

            self._results.append(TestResult(
                name=name,
                passed=status == "PASS",
                message=message,
                source=f"{filename}:{lineno}",
            ))
            return

        # Check for summary
        summary_match = _UNITY_SUMMARY_RE.search(line)
        if summary_match:
            self._is_complete = True
