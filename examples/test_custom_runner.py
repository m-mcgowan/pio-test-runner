"""PlatformIO custom test runner — bootstraps pio-test-runner.

Copy this file to test/test_custom_runner.py in your PlatformIO project
and set test_framework = custom in platformio.ini.

This file finds the library's Python source via PIO's libdeps directory
(supports symlinks for local development) and delegates everything to
EmbeddedTestRunner.

Customize test behavior via weak C++ functions in separate .cpp files:
    bool ptr_board_init(Print& log)
    void ptr_after_cycle()
    void ptr_configure_context(doctest::Context&)
"""

import os
import sys
import glob

# Find pio-test-runner and embedded-bridge Python sources in libdeps
_test_dir = os.path.dirname(os.path.abspath(__file__))
_project_dir = os.path.normpath(os.path.join(_test_dir, ".."))
for pattern in [
    os.path.join(_project_dir, ".pio", "libdeps", "*", "pio-test-runner", "src"),
    os.path.join(_project_dir, ".pio", "libdeps", "*", "embedded-bridge", "python", "src"),
]:
    for p in glob.glob(pattern):
        if p not in sys.path:
            sys.path.insert(0, p)

from pio_test_runner.runner import EmbeddedTestRunner  # noqa: E402


class CustomTestRunner(EmbeddedTestRunner):
    pass
