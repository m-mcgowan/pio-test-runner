"""Tests for TestTimingTracker."""

from pio_test_runner.protocol import format_crc
from pio_test_runner.timing_tracker import TestTimingTracker


def _crc(content: str) -> str:
    return format_crc(content)


class FakeClock:
    """Controllable clock for testing."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestTimingTrackerBehavior:
    def test_tracks_duration(self):
        clock = FakeClock()
        tt = TestTimingTracker(clock=clock)

        tt.feed(_crc('ETST:CASE:START suite="Suite" name="test_one"'))
        clock.advance(2.5)
        tt.feed(_crc('ETST:CASE:START suite="Suite" name="test_two"'))
        clock.advance(1.0)
        tt.finalize()

        assert tt.durations["Suite/test_one"] == 2.5
        assert tt.durations["Suite/test_two"] == 1.0

    def test_slow_tests_filtered(self):
        clock = FakeClock()
        tt = TestTimingTracker(slow_threshold=5.0, clock=clock)

        tt.feed(_crc('ETST:CASE:START suite="Suite" name="fast"'))
        clock.advance(1.0)
        tt.feed(_crc('ETST:CASE:START suite="Suite" name="slow"'))
        clock.advance(10.0)
        tt.finalize()

        assert "Suite/fast" not in tt.slow_tests
        assert "Suite/slow" in tt.slow_tests
        assert tt.slow_tests["Suite/slow"] == 10.0

    def test_report_empty_when_no_slow(self):
        clock = FakeClock()
        tt = TestTimingTracker(slow_threshold=5.0, clock=clock)
        tt.feed(_crc('ETST:CASE:START suite="Suite" name="fast"'))
        clock.advance(1.0)
        tt.finalize()
        assert tt.report() == ""

    def test_report_formats_slow_tests(self):
        clock = FakeClock()
        tt = TestTimingTracker(slow_threshold=5.0, clock=clock)
        tt.feed(_crc('ETST:CASE:START suite="Suite" name="slow"'))
        clock.advance(8.0)
        tt.finalize()

        report = tt.report()
        assert "Slow Tests" in report
        assert "Suite/slow" in report
        assert "8.0s" in report

    def test_test_start_with_timeout_annotation(self):
        clock = FakeClock()
        tt = TestTimingTracker(clock=clock)
        tt.feed(_crc('ETST:CASE:START suite="GPS" name="Nav rate" timeout=30'))
        clock.advance(3.0)
        tt.finalize()
        assert tt.durations["GPS/Nav rate"] == 3.0

    def test_non_test_lines_ignored(self):
        tt = TestTimingTracker()
        tt.feed("some random output")
        tt.feed(_crc("ETST:MEM:BEFORE free=200000 min=180000"))
        assert tt.durations == {}

    def test_bytes_input(self):
        clock = FakeClock()
        tt = TestTimingTracker(clock=clock)
        tt.feed(_crc('ETST:CASE:START suite="Suite" name="test"').encode())
        clock.advance(1.0)
        tt.finalize()
        assert "Suite/test" in tt.durations

    def test_reset_clears_all(self):
        clock = FakeClock()
        tt = TestTimingTracker(clock=clock)
        tt.feed(_crc('ETST:CASE:START suite="Suite" name="test"'))
        clock.advance(1.0)
        tt.finalize()
        tt.reset()
        assert tt.durations == {}
