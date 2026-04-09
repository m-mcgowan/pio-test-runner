#!/usr/bin/env python3
"""Acceptance tests: environment variable filtering via pio test.

Validates the full pipeline: env var → Python runner → serial → firmware → doctest.
Each test invokes `pio test` as a subprocess with specific PTR_* env vars
and validates which tests ran from the output.

Prerequisites:
  Integration firmware project at tests/integration/ with esp32s3 env.
  Device connected — pass port via --port.

Usage:
  pytest tests/acceptance/test_env_vars.py -v --port /dev/cu.usbmodem1433101
"""

import os
import re
import subprocess

import pytest


@pytest.fixture(scope="session")
def pio_project_dir():
    """Path to the integration test PIO project."""
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "integration")


@pytest.fixture(scope="session")
def pio_env():
    return "esp32s3"


def run_pio_test(project_dir, env, port, extra_env=None, program_args=None, timeout=180):
    """Run `pio test` and return parsed results.

    Returns a dict with:
      tests_run:   list of test names from ETST:CASE:START markers
      total:       total from doctest summary
      passed:      passed from doctest summary
      failed:      failed from doctest summary
      skipped:     skipped from doctest summary
      returncode:  process return code
      stdout:      raw stdout
    """
    cmd = [
        "pio", "test",
        "-e", env,
        "--upload-port", port,
        "--test-port", port,
        "--without-building",
        "--without-uploading",
        "-v",
    ]
    if program_args:
        for arg in program_args:
            cmd.extend(["-a", arg])

    env_vars = os.environ.copy()
    # Clear any stale PTR_ vars from the parent environment
    for key in list(env_vars.keys()):
        if key.startswith("PTR_"):
            del env_vars[key]
    if extra_env:
        env_vars.update(extra_env)

    result = subprocess.run(
        cmd,
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env_vars,
    )

    stdout = result.stdout + result.stderr

    # Parse test starts
    tests_run = re.findall(r'ETST:CASE:START.*?name="([^"]*)"', stdout)

    # Parse doctest summary
    total = passed = failed = skipped = 0
    m = re.search(
        r"test cases:\s*(\d+)\s*\|\s*(\d+)\s*passed\s*\|\s*(\d+)\s*failed\s*\|\s*(\d+)\s*skipped",
        stdout,
    )
    if m:
        total = int(m.group(1))
        passed = int(m.group(2))
        failed = int(m.group(3))
        skipped = int(m.group(4))

    return {
        "tests_run": tests_run,
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "returncode": result.returncode,
        "stdout": stdout,
    }


# =========================================================================
# Environment variable tests
# =========================================================================


class TestEnvVarSuiteFilter:
    """PTR_TEST_SUITE env var filters to matching suites."""

    def test_ptr_test_suite(self, pio_project_dir, pio_env, port):
        result = run_pio_test(
            pio_project_dir, pio_env, port,
            extra_env={"PTR_TEST_SUITE": "*Protocol*"},
        )
        assert "basic arithmetic" in result["tests_run"]
        assert "skip target active" not in result["tests_run"]


class TestEnvVarCaseFilter:
    """PTR_TEST_CASE env var filters to matching test cases."""

    def test_ptr_test_case(self, pio_project_dir, pio_env, port):
        result = run_pio_test(
            pio_project_dir, pio_env, port,
            extra_env={"PTR_TEST_CASE": "*basic*arithmetic*"},
        )
        assert result["tests_run"] == ["basic arithmetic"]


class TestEnvVarExclude:
    """PTR_TEST_CASE_EXCLUDE and PTR_TEST_SUITE_EXCLUDE env vars."""

    def test_ptr_test_case_exclude(self, pio_project_dir, pio_env, port):
        result = run_pio_test(
            pio_project_dir, pio_env, port,
            extra_env={
                "PTR_TEST_SUITE": "*Protocol*",
                "PTR_TEST_CASE_EXCLUDE": "*string*",
            },
        )
        assert "basic arithmetic" in result["tests_run"]
        assert "string operations" not in result["tests_run"]

    def test_ptr_test_suite_exclude(self, pio_project_dir, pio_env, port):
        result = run_pio_test(
            pio_project_dir, pio_env, port,
            extra_env={"PTR_TEST_SUITE_EXCLUDE": "*Protocol*"},
        )
        assert "basic arithmetic" not in result["tests_run"]
        assert "skip target active" in result["tests_run"]


class TestEnvVarUnskip:
    """PTR_UNSKIP_TEST_CASE and PTR_UNSKIP_TEST_SUITE env vars."""

    def test_ptr_unskip_test_case(self, pio_project_dir, pio_env, port):
        result = run_pio_test(
            pio_project_dir, pio_env, port,
            extra_env={
                "PTR_UNSKIP_TEST_CASE": "*unskip*target*simple*",
                "PTR_TEST_SUITE": "*SkipControl*",
            },
        )
        assert "unskip target simple" in result["tests_run"]
        assert "unskip target with spaces in name" not in result["tests_run"]

    def test_ptr_unskip_test_suite(self, pio_project_dir, pio_env, port):
        result = run_pio_test(
            pio_project_dir, pio_env, port,
            extra_env={
                "PTR_UNSKIP_TEST_SUITE": "*SubSuite*",
                "PTR_TEST_SUITE": "*SubSuite*",
            },
        )
        assert "suite unskip target" in result["tests_run"]


class TestEnvVarSkip:
    """PTR_SKIP_TEST_CASE and PTR_SKIP_TEST_SUITE env vars."""

    def test_ptr_skip_test_case(self, pio_project_dir, pio_env, port):
        result = run_pio_test(
            pio_project_dir, pio_env, port,
            extra_env={
                "PTR_SKIP_TEST_CASE": "*skip*target*active*",
                "PTR_TEST_SUITE": "*SkipControl*",
            },
        )
        assert "skip target active" not in result["tests_run"]


class TestEnvVarNoSkip:
    """PTR_NO_SKIP env var."""

    def test_ptr_no_skip(self, pio_project_dir, pio_env, port):
        result = run_pio_test(
            pio_project_dir, pio_env, port,
            extra_env={
                "PTR_NO_SKIP": "1",
                "PTR_TEST_SUITE": "*SkipControl*",
            },
        )
        assert "unskip target simple" in result["tests_run"]
        assert "skip target active" in result["tests_run"]
        assert "unskip target with spaces in name" in result["tests_run"]


# =========================================================================
# Program args (-a) tests
# =========================================================================


class TestProgramArgs:
    """pio test -a '...' passes flags to the device."""

    def test_dash_a_ts(self, pio_project_dir, pio_env, port):
        result = run_pio_test(
            pio_project_dir, pio_env, port,
            program_args=["--ts *Protocol*"],
        )
        assert "basic arithmetic" in result["tests_run"]
        assert "skip target active" not in result["tests_run"]

    def test_dash_a_tc(self, pio_project_dir, pio_env, port):
        result = run_pio_test(
            pio_project_dir, pio_env, port,
            program_args=["--tc *basic*arithmetic*"],
        )
        assert result["tests_run"] == ["basic arithmetic"]

    def test_dash_a_unskip(self, pio_project_dir, pio_env, port):
        result = run_pio_test(
            pio_project_dir, pio_env, port,
            program_args=[
                "--unskip-tc *unskip*target*simple*",
                "--ts *SkipControl*",
            ],
        )
        assert "unskip target simple" in result["tests_run"]

    def test_dash_a_combined_with_env(self, pio_project_dir, pio_env, port):
        """Program args and env vars combine."""
        result = run_pio_test(
            pio_project_dir, pio_env, port,
            extra_env={"PTR_TEST_SUITE": "*Protocol*"},
            program_args=["--tc *millis*"],
        )
        assert result["tests_run"] == ["Arduino millis is running"]
