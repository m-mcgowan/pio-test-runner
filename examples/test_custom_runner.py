"""PlatformIO custom test runner shim.

Copy this file to ``test/test_custom_runner.py`` in your PlatformIO
project and set ``test_framework = custom`` in ``platformio.ini``.

PIO discovers runners by file path and class name — the class must be
called ``CustomTestRunner``. This shim auto-installs ``pio-test-runner``
(and its dependency ``embedded-bridge``) from GitHub on first use.

Set ``PIO_TEST_RUNNER_NO_AUTO_INSTALL=1`` to disable auto-installation.
"""

import os
import subprocess
import sys

_PACKAGES = [
    (
        "embedded_bridge",
        "embedded-bridge @ git+https://github.com/m-mcgowan/embedded-bridge.git#subdirectory=python",
    ),
    (
        "pio_test_runner",
        "pio-test-runner @ git+https://github.com/m-mcgowan/pio-test-runner.git",
    ),
]


def _auto_install():
    """pip-install missing packages into the running Python environment."""
    for module, pip_spec in _PACKAGES:
        try:
            __import__(module)
        except ImportError:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pip_spec],
            )


if not os.environ.get("PIO_TEST_RUNNER_NO_AUTO_INSTALL"):
    _auto_install()

from pio_test_runner.runner import EmbeddedTestRunner  # noqa: E402


class CustomTestRunner(EmbeddedTestRunner):
    """Delegates to EmbeddedTestRunner from pio-test-runner.

    Override methods here for project-specific customization, e.g.:

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Custom crash patterns
            self.crash_detector._patterns.append(
                CrashPattern("my_error", "MY_FATAL_ERROR")
            )
            # Longer silent timeout
            self.crash_detector._silent_timeout = 120.0
    """

    pass
