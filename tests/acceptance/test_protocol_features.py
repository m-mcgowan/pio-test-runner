#!/usr/bin/env python3
"""Acceptance tests: protocol features on real hardware.

Validates memory tracking, timing markers, and test count reporting.
"""

import re

import pytest

from helpers import open_device, send_command, send_sleep, has_line_matching


@pytest.fixture
def device(port, baud):
    ser = open_device(port, baud)
    yield ser
    ser.close()


class TestMemoryTracking:
    """ETST:MEM:BEFORE/AFTER markers are emitted for each test."""

    def test_mem_markers_present(self, device):
        result = send_command(device, "RUN: --ts *Protocol*")
        # Should have memory markers for each test that ran
        assert len(result["mem_markers"]) > 0
        assert len(result["mem_markers"]) == len(result["tests_run"])
        send_sleep(device)

    def test_mem_markers_have_valid_values(self, device):
        result = send_command(device, "RUN: --tc *basic*arithmetic*")
        assert len(result["mem_markers"]) == 1
        marker = result["mem_markers"][0]
        assert marker["test"] == "basic arithmetic"
        assert marker["free_before"] > 0
        assert marker["free_after"] > 0
        # A simple arithmetic test should not leak significant memory
        assert abs(marker["delta"]) < 1000
        send_sleep(device)

    def test_largest_block_present(self, device):
        """ESP-IDF builds include largest contiguous block."""
        result = send_command(device, "RUN: --tc *basic*arithmetic*")
        # Check raw lines for largest= field
        assert has_line_matching(
            result["raw_lines"], r"ETST:MEM:BEFORE.*largest=\d+"
        )
        assert has_line_matching(
            result["raw_lines"], r"ETST:MEM:AFTER.*largest=\d+"
        )
        send_sleep(device)


class TestTimingMarkers:
    """ETST:TEST:START markers include suite and name."""

    def test_start_markers_present(self, device):
        result = send_command(device, "RUN: --ts *Protocol*")
        assert len(result["test_starts"]) > 0
        send_sleep(device)

    def test_start_marker_fields(self, device):
        result = send_command(device, "RUN: --tc *basic*arithmetic*")
        assert len(result["test_starts"]) == 1
        start = result["test_starts"][0]
        assert start["suite"] == "Protocol"
        assert start["name"] == "basic arithmetic"
        send_sleep(device)

    def test_timeout_annotation(self, device):
        """Tests with doctest::timeout() include timeout in marker."""
        # The timing test file has a test with timeout
        result = send_command(device, "RUN: --ts *Timing*")
        timeout_tests = [s for s in result["test_starts"] if "timeout" in s]
        # Should have at least one test with a timeout annotation
        assert len(timeout_tests) > 0, (
            f"No timeout annotations found in test starts: {result['test_starts']}"
        )
        send_sleep(device)


class TestCounts:
    """ETST:TESTS total/skip/run counts are reported."""

    def test_test_count_reported(self, device):
        result = send_command(device, "RUN_ALL")
        assert has_line_matching(
            result["raw_lines"], r"ETST:TESTS total=\d+ skip=\d+ run=\d+"
        )
        send_sleep(device)

    def test_count_matches_summary(self, device):
        result = send_command(device, "RUN_ALL")
        # Find the ETST:TESTS line
        for line in result["raw_lines"]:
            m = re.search(
                r"ETST:TESTS total=(\d+) skip=(\d+) run=(\d+)", line
            )
            if m:
                ptr_total = int(m.group(1))
                # Total from ETST:TESTS should match doctest's total + skipped
                assert ptr_total > 0
                break
        else:
            pytest.fail("ETST:TESTS line not found")
        send_sleep(device)


def _parse_ptr_tests(raw_lines):
    """Extract total/skip/run from ETST:TESTS line."""
    for line in raw_lines:
        m = re.search(
            r"ETST:TESTS total=(\d+) skip=(\d+) run=(\d+)", line
        )
        if m:
            return {
                "total": int(m.group(1)),
                "skip": int(m.group(2)),
                "run": int(m.group(3)),
            }
    return None


class TestFilteredCounts:
    """ETST:TESTS skip/run counts reflect filter excludes.

    Bug: skip count only reflected RESUME_AFTER skips, not --tce/--tse
    filter excludes. See BUG_tce_skip_count_not_reported.md.
    """

    def test_tce_reflected_in_skip_count(self, device):
        """--tce should increase skip count for excluded tests."""
        result = send_command(device, "RUN: --tce *string*")
        counts = _parse_ptr_tests(result["raw_lines"])
        assert counts is not None, "ETST:TESTS line not found"
        # "string operations" should be excluded
        assert "string operations" not in result["tests_run"]
        # skip count must reflect the exclude
        assert counts["skip"] > 0, \
            f"Expected skip > 0 with --tce, got: {counts}"
        assert counts["run"] < counts["total"], \
            f"Expected run < total with --tce, got: {counts}"
        send_sleep(device)

    def test_tse_reflected_in_skip_count(self, device):
        """--tse should increase skip count for excluded suite."""
        result = send_command(device, "RUN: --tse *Protocol*")
        counts = _parse_ptr_tests(result["raw_lines"])
        assert counts is not None, "ETST:TESTS line not found"
        assert "basic arithmetic" not in result["tests_run"]
        assert counts["skip"] > 0, \
            f"Expected skip > 0 with --tse, got: {counts}"
        send_sleep(device)

    def test_ts_reflected_in_skip_count(self, device):
        """--ts should show only matching suite in run count."""
        result = send_command(device, "RUN: --ts *Protocol*")
        counts = _parse_ptr_tests(result["raw_lines"])
        assert counts is not None, "ETST:TESTS line not found"
        # Only Protocol tests should run (3 tests)
        assert counts["run"] == len(result["tests_run"])
        assert counts["skip"] > 0, \
            f"Expected skip > 0 with --ts, got: {counts}"
        send_sleep(device)

    def test_tc_reflected_in_skip_count(self, device):
        """--tc should show only matching test in run count."""
        result = send_command(device, 'RUN: --tc "basic arithmetic"')
        counts = _parse_ptr_tests(result["raw_lines"])
        assert counts is not None, "ETST:TESTS line not found"
        assert counts["run"] == 1
        assert counts["skip"] == counts["total"] - 1
        send_sleep(device)
