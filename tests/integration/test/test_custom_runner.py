"""PlatformIO custom test runner — uses pio-test-runner for orchestration.

Auto-adds the pio-test-runner and embedded-bridge source directories to
sys.path so the imports work without pip install.
"""

import os
import sys

# Add source dirs to path so imports resolve without pip install
_here = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.normpath(os.path.join(_here, "..", "..", ".."))
_bridge = os.path.normpath(os.path.join(_repo, "..", "embedded-bridge", "python", "src"))

for p in [os.path.join(_repo, "src"), _bridge]:
    if p not in sys.path:
        sys.path.insert(0, p)

from pio_test_runner import EmbeddedTestRunner


class CustomTestRunner(EmbeddedTestRunner):
    pass
