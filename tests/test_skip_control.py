"""Tests for skip control: env vars, quoting, and command building."""

import os
from unittest.mock import patch

from conftest import MockProjectConfig, MockTestRunnerOptions, MockTestSuite
from pio_test_runner.runner import EmbeddedTestRunner


def make_runner(**kwargs):
    suite = kwargs.pop("test_suite", None) or MockTestSuite()
    config = kwargs.pop("project_config", None) or MockProjectConfig()
    options = kwargs.pop("options", None) or MockTestRunnerOptions()
    runner = EmbeddedTestRunner(suite, config, options)
    return runner


class TestBuildInitialCommand:
    """Tests for _build_initial_command() with skip control env vars."""

    def test_no_filters_returns_run_all(self):
        runner = make_runner()
        with patch.dict(os.environ, {}, clear=True):
            cmd = runner._build_initial_command()
        assert cmd == "RUN_ALL"

    def test_test_case_filter(self):
        runner = make_runner()
        with patch.dict(os.environ, {"ETST_CASE": "*foo*"}, clear=True):
            cmd = runner._build_initial_command()
        assert "--tc *foo*" in cmd
        assert cmd.startswith("RUN: ")

    def test_test_suite_filter(self):
        runner = make_runner()
        with patch.dict(os.environ, {"ETST_SUITE": "*WDT*"}, clear=True):
            cmd = runner._build_initial_command()
        assert "--ts *WDT*" in cmd

    def test_unskip_test_case(self):
        runner = make_runner()
        with patch.dict(
            os.environ, {"ETST_UNSKIP_CASE": "*TWDT*"}, clear=True
        ):
            cmd = runner._build_initial_command()
        assert "--unskip-tc *TWDT*" in cmd

    def test_unskip_test_suite(self):
        runner = make_runner()
        with patch.dict(
            os.environ, {"ETST_UNSKIP_SUITE": "*WDT*"}, clear=True
        ):
            cmd = runner._build_initial_command()
        assert "--unskip-ts *WDT*" in cmd

    def test_skip_test_case(self):
        runner = make_runner()
        with patch.dict(
            os.environ, {"ETST_SKIP_CASE": "*slow*"}, clear=True
        ):
            cmd = runner._build_initial_command()
        assert "--skip-tc *slow*" in cmd

    def test_skip_test_suite(self):
        runner = make_runner()
        with patch.dict(
            os.environ, {"ETST_SKIP_SUITE": "*heavy*"}, clear=True
        ):
            cmd = runner._build_initial_command()
        assert "--skip-ts *heavy*" in cmd

    def test_no_skip_flag(self):
        runner = make_runner()
        with patch.dict(os.environ, {"ETST_NO_SKIP": "1"}, clear=True):
            cmd = runner._build_initial_command()
        assert "--no-skip" in cmd
        # Should not have "1" as a value — it's a boolean flag
        assert "--no-skip 1" not in cmd

    def test_value_with_spaces_is_quoted(self):
        runner = make_runner()
        with patch.dict(
            os.environ,
            {"ETST_UNSKIP_CASE": "*TWDT fires on*"},
            clear=True,
        ):
            cmd = runner._build_initial_command()
        # Value with spaces should be quoted
        assert '"*TWDT fires on*"' in cmd

    def test_value_without_spaces_is_not_quoted(self):
        runner = make_runner()
        with patch.dict(
            os.environ, {"ETST_CASE": "*TWDT*"}, clear=True
        ):
            cmd = runner._build_initial_command()
        assert '"' not in cmd

    def test_multiple_filters_combined(self):
        runner = make_runner()
        with patch.dict(
            os.environ,
            {
                "ETST_SUITE": "*Service/WDT*",
                "ETST_UNSKIP_CASE": "*TWDT*",
            },
            clear=True,
        ):
            cmd = runner._build_initial_command()
        assert "--ts *Service/WDT*" in cmd
        assert "--unskip-tc *TWDT*" in cmd

    def test_program_args_passthrough(self):
        """Program args from pio test -a '...' are passed through."""
        options = MockTestRunnerOptions()
        options.program_args = ["--ts", "*Proto*", "--no-skip"]
        runner = make_runner(options=options)
        with patch.dict(os.environ, {}, clear=True):
            cmd = runner._build_initial_command()
        assert "--ts" in cmd
        assert "*Proto*" in cmd
        assert "--no-skip" in cmd

    def test_program_args_combined_with_env_vars(self):
        """Program args and env vars are combined."""
        options = MockTestRunnerOptions()
        options.program_args = ["--ts", "*Suite*"]
        runner = make_runner(options=options)
        with patch.dict(
            os.environ, {"ETST_UNSKIP_CASE": "*target*"}, clear=True
        ):
            cmd = runner._build_initial_command()
        assert "--ts" in cmd
        assert "--unskip-tc" in cmd

    def test_program_args_with_spaces_are_quoted(self):
        """Program args with spaces must be quoted for firmware tokenizer."""
        options = MockTestRunnerOptions()
        options.program_args = ["--ts", "My Suite Name"]
        runner = make_runner(options=options)
        with patch.dict(os.environ, {}, clear=True):
            cmd = runner._build_initial_command()
        assert cmd == 'RUN: --ts "My Suite Name"'

    def test_program_args_exclusion_with_spaces_quoted(self):
        options = MockTestRunnerOptions()
        options.program_args = ["--tse", "Slow Tests"]
        runner = make_runner(options=options)
        with patch.dict(os.environ, {}, clear=True):
            cmd = runner._build_initial_command()
        assert cmd == 'RUN: --tse "Slow Tests"'

    def test_program_args_without_spaces_not_quoted(self):
        options = MockTestRunnerOptions()
        options.program_args = ["--ts", "*Proto*"]
        runner = make_runner(options=options)
        with patch.dict(os.environ, {}, clear=True):
            cmd = runner._build_initial_command()
        assert cmd == "RUN: --ts *Proto*"
        assert '"' not in cmd


class TestResumeAfter:
    """Tests for ETST_RESUME_AFTER env var."""

    def test_resume_after_basic(self):
        runner = make_runner()
        with patch.dict(
            os.environ,
            {"ETST_RESUME_AFTER": "my test name"},
            clear=True,
        ):
            cmd = runner._build_initial_command()
        assert cmd == "RESUME_AFTER: my test name"

    def test_resume_after_with_filters(self):
        """Filters are appended after the resume point."""
        runner = make_runner()
        with patch.dict(
            os.environ,
            {
                "ETST_RESUME_AFTER": "some test",
                "ETST_SUITE": "*WDT*",
            },
            clear=True,
        ):
            cmd = runner._build_initial_command()
        assert cmd.startswith("RESUME_AFTER: some test")
        assert "--ts *WDT*" in cmd

    def test_resume_after_with_program_args(self):
        options = MockTestRunnerOptions()
        options.program_args = ["--tc", "*foo*"]
        runner = make_runner(options=options)
        with patch.dict(
            os.environ,
            {"ETST_RESUME_AFTER": "previous test"},
            clear=True,
        ):
            cmd = runner._build_initial_command()
        assert cmd.startswith("RESUME_AFTER: previous test")
        assert "--tc" in cmd
        assert "*foo*" in cmd

    def test_resume_after_takes_precedence_over_run(self):
        """RESUME_AFTER produces a RESUME_AFTER command, not RUN."""
        runner = make_runner()
        with patch.dict(
            os.environ,
            {
                "ETST_RESUME_AFTER": "test A",
                "ETST_CASE": "*foo*",
            },
            clear=True,
        ):
            cmd = runner._build_initial_command()
        assert cmd.startswith("RESUME_AFTER:")
        assert not cmd.startswith("RUN:")

    def test_no_resume_after_returns_normal(self):
        """Without ETST_RESUME_AFTER, normal RUN command is built."""
        runner = make_runner()
        with patch.dict(
            os.environ, {"ETST_CASE": "*foo*"}, clear=True
        ):
            cmd = runner._build_initial_command()
        assert cmd.startswith("RUN: ")
