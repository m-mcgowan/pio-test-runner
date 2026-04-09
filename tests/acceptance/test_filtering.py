#!/usr/bin/env python3
"""Acceptance tests: test filtering on real hardware.

Validates every documented filter mechanism end-to-end:
  --tc, --ts, --tce, --tse, --no-skip,
  --unskip-tc, --unskip-ts, --skip-tc, --skip-ts,
  and their combinations.

Prerequisites:
  Flash integration firmware:
    cd tests/integration && pio run -e esp32s3 -t upload --upload-port PORT
  Then run:
    tests/acceptance/run.sh "1.9"
"""

import pytest

from helpers import open_device, wait_for_ready, send_command, send_sleep


@pytest.fixture
def device(port, baud):
    """Open serial and ensure device is in READY state.

    Waits for two consecutive READY signals to ensure the device has
    fully booted and isn't mid-restart from a previous PTR_POST_TEST=restart.
    """
    ser = open_device(port, baud)
    # Wait for first READY (device may still be booting)
    assert wait_for_ready(ser, timeout=15), \
        f"Device not ready on {port}. Is it awake? Try: usb-device reset <name>"
    yield ser
    ser.close()


# =========================================================================
# Baseline
# =========================================================================


class TestRunAll:
    """RUN_ALL runs all non-skipped tests."""

    def test_run_all_executes_non_skipped(self, device):
        # Exclude DeepSleep suite — deep sleep tests use the ETST:SLEEP
        # protocol which requires the full runner's sleep/wake orchestration.
        # Our send_command() helper talks directly to the serial port and
        # doesn't handle sleep cycles. Deep sleep is tested in test_sleep.py.
        result = send_command(device, "RUN: --tse *DeepSleep*")
        assert result["passed"] > 0
        assert "basic arithmetic" in result["tests_run"]
        assert "string operations" in result["tests_run"]
        assert "skip target active" in result["tests_run"]
        assert "no suite standalone" in result["tests_run"]
        # Skip-decorated tests should NOT run
        assert "unskip target simple" not in result["tests_run"]
        assert "suite unskip target" not in result["tests_run"]
        send_sleep(device)


# =========================================================================
# Suite filter (--ts)
# =========================================================================


class TestSuiteFilter:
    """--ts restricts execution to matching suites."""

    def test_ts_protocol_only(self, device):
        result = send_command(device, "RUN: --ts *Protocol*")
        assert "basic arithmetic" in result["tests_run"]
        assert "string operations" in result["tests_run"]
        # Other suites excluded
        assert "skip target active" not in result["tests_run"]
        send_sleep(device)

    def test_ts_skipcontrol_only(self, device):
        result = send_command(device, "RUN: --ts *SkipControl*")
        assert "basic arithmetic" not in result["tests_run"]
        assert "skip target active" in result["tests_run"]
        send_sleep(device)

    def test_ts_no_match_runs_nothing(self, device):
        result = send_command(device, "RUN: --ts *NonExistentSuite*")
        assert len(result["tests_run"]) == 0
        send_sleep(device)


# =========================================================================
# Non-suite tests and --ts interaction
# =========================================================================


class TestNoSuiteFiltering:
    """Tests not in a TEST_SUITE() interact with filters."""

    def test_no_suite_runs_unfiltered(self, device):
        """Without filters, non-suite tests run normally."""
        result = send_command(device, "RUN: --tse *DeepSleep*")
        assert "no suite standalone" in result["tests_run"]
        send_sleep(device)

    def test_ts_excludes_no_suite_tests(self, device):
        """--ts *Protocol* should exclude tests not in any suite.

        doctest's --ts filter matches against the suite name. Tests with
        no suite have a null/empty suite name, which shouldn't match a
        specific pattern. If this test fails (the non-suite test runs),
        then --ts leaks non-suite tests through the filter.
        """
        result = send_command(device, "RUN: --ts *Protocol*")
        assert "basic arithmetic" in result["tests_run"]
        assert "no suite standalone" not in result["tests_run"]
        send_sleep(device)

    def test_ts_excludes_no_suite_negative(self, device):
        """Verify with a different suite — non-suite tests still excluded."""
        result = send_command(device, "RUN: --ts *SkipControl*")
        assert "skip target active" in result["tests_run"]
        assert "no suite standalone" not in result["tests_run"]
        send_sleep(device)

    def test_tc_matches_no_suite_test(self, device):
        """--tc can select a non-suite test by name."""
        result = send_command(device, "RUN: --tc *no*suite*standalone*")
        assert result["tests_run"] == ["no suite standalone"]
        send_sleep(device)

    def test_tse_does_not_affect_no_suite(self, device):
        """--tse excludes matching suites but non-suite test still runs."""
        result = send_command(device, "RUN: --tse *Protocol*,*DeepSleep*")
        assert "basic arithmetic" not in result["tests_run"]
        assert "no suite standalone" in result["tests_run"]
        send_sleep(device)


# =========================================================================
# Case filter (--tc)
# =========================================================================


class TestCaseFilter:
    """--tc restricts execution to matching test cases."""

    def test_tc_exact_name(self, device):
        result = send_command(device, "RUN: --tc *basic*arithmetic*")
        assert result["tests_run"] == ["basic arithmetic"]
        send_sleep(device)

    def test_tc_wildcard_partial(self, device):
        result = send_command(device, "RUN: --tc *string*")
        assert "string operations" in result["tests_run"]
        assert "basic arithmetic" not in result["tests_run"]
        send_sleep(device)

    def test_tc_comma_separated(self, device):
        """Comma-separated patterns (doctest native feature)."""
        result = send_command(device, "RUN: --tc *basic*arithmetic*,*string*")
        assert "basic arithmetic" in result["tests_run"]
        assert "string operations" in result["tests_run"]
        assert "Arduino millis is running" not in result["tests_run"]
        send_sleep(device)

    def test_tc_quoted_name_with_spaces(self, device):
        """Quoted test name with spaces selects exactly one test."""
        result = send_command(device, 'RUN: --tc "Arduino millis is running"')
        assert result["tests_run"] == ["Arduino millis is running"]
        send_sleep(device)

    def test_tse_comma_separated(self, device):
        """Comma-separated --tse excludes multiple suites."""
        result = send_command(device, "RUN: --tse *Protocol*,*DeepSleep*")
        assert "basic arithmetic" not in result["tests_run"]
        assert "skip target active" in result["tests_run"]
        send_sleep(device)


# =========================================================================
# Exclude filters (--tce, --tse)
# =========================================================================


class TestExcludeFilter:
    """--tce and --tse exclude matching tests."""

    def test_tce_excludes_case(self, device):
        result = send_command(device, "RUN: --ts *Protocol* --tce *string*")
        assert "basic arithmetic" in result["tests_run"]
        assert "string operations" not in result["tests_run"]
        send_sleep(device)

    def test_tse_excludes_suite(self, device):
        result = send_command(device, "RUN: --tse *Protocol*")
        assert "basic arithmetic" not in result["tests_run"]
        assert "skip target active" in result["tests_run"]
        send_sleep(device)


# =========================================================================
# Skip control (--unskip-tc, --unskip-ts, --skip-tc, --skip-ts)
# =========================================================================


class TestUnskipControl:
    """--unskip-tc and --unskip-ts selectively enable skipped tests."""

    def test_unskip_tc_enables_one_skipped_test(self, device):
        result = send_command(
            device,
            "RUN: --unskip-tc *unskip*target*simple* --ts *SkipControl*",
        )
        assert "unskip target simple" in result["tests_run"]
        assert "skip target active" in result["tests_run"]
        # Other skipped test stays skipped
        assert "unskip target with spaces in name" not in result["tests_run"]
        send_sleep(device)

    def test_unskip_ts_enables_skipped_suite(self, device):
        result = send_command(
            device, "RUN: --unskip-ts *SubSuite* --ts *SubSuite*"
        )
        assert "suite unskip target" in result["tests_run"]
        send_sleep(device)

    def test_unskip_with_tc_restricts_to_unskipped(self, device):
        """Unskip + --tc: only the unskipped test matching --tc runs."""
        result = send_command(
            device,
            "RUN: --unskip-tc *unskip*target*simple* --tc *unskip*target*simple*",
        )
        assert result["tests_run"] == ["unskip target simple"]
        send_sleep(device)


class TestForceSkip:
    """--skip-tc force-skips non-skipped tests."""

    def test_skip_tc_forces_skip(self, device):
        result = send_command(
            device,
            "RUN: --skip-tc *skip*target*active* --ts *SkipControl*",
        )
        assert "skip target active" not in result["tests_run"]
        send_sleep(device)


class TestNoSkip:
    """--no-skip runs all tests including skip-decorated ones."""

    def test_no_skip_includes_all(self, device):
        result = send_command(device, "RUN: --no-skip --ts *SkipControl*")
        assert "unskip target simple" in result["tests_run"]
        assert "skip target active" in result["tests_run"]
        assert "unskip target with spaces in name" in result["tests_run"]
        send_sleep(device)


# =========================================================================
# Skip flag ordering (last wins)
# =========================================================================


class TestSkipOrdering:
    """Later skip flags override earlier ones (left-to-right)."""

    def test_skip_then_unskip_leaves_unskipped(self, device):
        result = send_command(
            device,
            "RUN: --skip-tc *skip*target*active* "
            "--unskip-tc *skip*target*active* --ts *SkipControl*",
        )
        assert "skip target active" in result["tests_run"]
        send_sleep(device)

    def test_unskip_then_skip_leaves_skipped(self, device):
        result = send_command(
            device,
            "RUN: --unskip-tc *unskip*target*simple* "
            "--skip-tc *unskip*target*simple* --ts *SkipControl*",
        )
        assert "unskip target simple" not in result["tests_run"]
        send_sleep(device)


# =========================================================================
# Combined filters
# =========================================================================


class TestCombinedFilters:
    """Multiple filter types compose correctly."""

    def test_ts_plus_tc(self, device):
        result = send_command(device, "RUN: --ts *Protocol* --tc *millis*")
        assert result["tests_run"] == ["Arduino millis is running"]
        send_sleep(device)

    def test_unskip_plus_exclude(self, device):
        """Unskip a test but exclude via --tce — exclude wins."""
        result = send_command(
            device,
            "RUN: --unskip-tc *unskip*target*simple* "
            "--tce *unskip*target*simple*",
        )
        assert "unskip target simple" not in result["tests_run"]
        send_sleep(device)

    def test_no_skip_plus_ts(self, device):
        """--no-skip with --ts: all tests in suite including skipped."""
        result = send_command(
            device, "RUN: --no-skip --ts *SkipControl/SubSuite*"
        )
        assert "suite unskip target" in result["tests_run"]
        # Protocol tests excluded by --ts
        assert "basic arithmetic" not in result["tests_run"]
        send_sleep(device)


# =========================================================================
# Bare multi-word patterns (Phase 2 resume command format)
# =========================================================================


class TestBarePatternTokenizing:
    """Bare multi-word patterns are split by the firmware tokenizer.

    The runner's Phase 2 resume command sends test names as bare patterns
    (no --tc flag). The firmware tokenizer splits on whitespace, so
    multi-word names become multiple garbage args that doctest ignores.
    Result: skip=0, all tests re-run instead of just the target.

    See BUG_resume_loop_prevents_later_suites.md.
    """

    def test_bare_multiword_runs_all_tests(self, device):
        """RUN: basic arithmetic — tokenizer splits, all tests run."""
        result = send_command(device, "RUN: basic arithmetic")
        # BUG: should run 1 test, actually runs all non-skipped tests
        assert len(result["tests_run"]) > 1, \
            "Expected all tests to run (tokenizer splits bare multi-word)"
        assert "basic arithmetic" in result["tests_run"]
        assert "string operations" in result["tests_run"]
        send_sleep(device)

    def test_bare_wildcard_multiword_runs_all_tests(self, device):
        """RUN: *basic arithmetic* — tokenizer splits, all tests run."""
        result = send_command(device, "RUN: *basic arithmetic*")
        # BUG: should run 1 test, actually runs all non-skipped tests
        assert len(result["tests_run"]) > 1, \
            "Expected all tests to run (tokenizer splits bare wildcard pattern)"
        assert "skip target active" in result["tests_run"]
        send_sleep(device)

    def test_tc_quoted_runs_one_test(self, device):
        """RUN: --tc "basic arithmetic" — correct format, 1 test runs."""
        result = send_command(device, 'RUN: --tc "basic arithmetic"')
        assert result["tests_run"] == ["basic arithmetic"]
        send_sleep(device)

    def test_tc_exact_no_wildcards_runs_one_test(self, device):
        """RUN: --tc "Arduino millis is running" — exact match works."""
        result = send_command(device, 'RUN: --tc "Arduino millis is running"')
        assert result["tests_run"] == ["Arduino millis is running"]
        send_sleep(device)
