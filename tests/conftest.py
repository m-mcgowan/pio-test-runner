"""Test fixtures and PIO mocks for pio-test-runner tests."""

import enum
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch


class MockTestStatus(enum.Enum):
    PASSED = enum.auto()
    FAILED = enum.auto()
    SKIPPED = enum.auto()
    WARNED = enum.auto()
    ERRORED = enum.auto()


class MockTestCase:
    def __init__(self, name, status, message=None, stdout=None, source=None,
                 duration=0, exception=None):
        self.name = name
        self.status = status
        self.message = message
        self.stdout = stdout
        self.source = source
        self.duration = duration
        self.exception = exception


class MockTestSuite:
    def __init__(self, env_name="esp32s3", test_name="*"):
        self.env_name = env_name
        self.test_name = test_name
        self.cases = []
        self._finished = False

    def add_case(self, case):
        self.cases.append(case)

    def on_finish(self):
        self._finished = True

    def on_start(self):
        pass

    def is_finished(self):
        return self._finished


class MockProjectConfig:
    def __init__(self):
        self.path = "/fake/platformio.ini"

    def get(self, section, key, default=None):
        return default


class MockPlatform:
    def is_embedded(self):
        return True


class MockTestRunnerOptions:
    def __init__(self, verbose=0):
        self.verbose = verbose
        self.without_building = True
        self.without_uploading = True
        self.without_testing = True
        self.without_debugging = True
        self.upload_port = None
        self.test_port = None
        self.no_reset = False
        self.monitor_rts = None
        self.monitor_dtr = None
        self.program_args = None


def install_pio_mocks():
    """Install mock PIO modules so runner.py can import them."""
    # Create mock module hierarchy
    pio_test_result = MagicMock()
    pio_test_result.TestCase = MockTestCase
    pio_test_result.TestStatus = MockTestStatus

    # Mock TestRunnerBase that doesn't call PlatformFactory
    class MockTestRunnerBase:
        NAME = None
        EXTRA_LIB_DEPS = None
        TESTCASE_PARSE_RE = None

        def __init__(self, test_suite, project_config, options=None):
            self.test_suite = test_suite
            self.project_config = project_config
            self.options = options or MockTestRunnerOptions()
            self.platform = MockPlatform()
            self._testing_output_buffer = ""

        def setup(self):
            pass

        def teardown(self):
            pass

    pio_test_runners_base = MagicMock()
    pio_test_runners_base.TestRunnerBase = MockTestRunnerBase

    # Mock DoctestTestRunner and its parser
    pio_test_runners_doctest = MagicMock()
    pio_test_runners_doctest.DoctestTestRunner = None
    pio_test_runners_doctest.DoctestTestCaseParser = MagicMock()

    # Mock SerialPortFinder
    pio_device_finder = MagicMock()
    pio_device_finder.SerialPortFinder = MagicMock()

    sys.modules["platformio"] = MagicMock()
    sys.modules["platformio.test"] = MagicMock()
    sys.modules["platformio.test.result"] = pio_test_result
    sys.modules["platformio.test.runners"] = MagicMock()
    sys.modules["platformio.test.runners.base"] = pio_test_runners_base
    sys.modules["platformio.test.runners.doctest"] = pio_test_runners_doctest
    sys.modules["platformio.test.runners.readers"] = MagicMock()
    sys.modules["platformio.test.runners.readers.serial"] = MagicMock()
    sys.modules["platformio.test.runners.readers.native"] = MagicMock()
    sys.modules["platformio.exception"] = MagicMock()
    sys.modules["platformio.platform"] = MagicMock()
    sys.modules["platformio.platform.factory"] = MagicMock()
    sys.modules["platformio.device"] = MagicMock()
    sys.modules["platformio.device.finder"] = pio_device_finder


# Install mocks before any test imports runner.py
install_pio_mocks()


class FakeEntryPoint:
    """Stand-in for importlib.metadata.EntryPoint with a controllable load()."""

    def __init__(self, name, target):
        self.name = name
        self._target = target

    def load(self):
        return self._target


@contextmanager
def fake_entry_points(group_to_eps):
    """Patch importlib.metadata.entry_points to return controlled fakes.

    Args:
        group_to_eps: dict mapping group name -> list of FakeEntryPoint.
    """
    def _entry_points(*, group=None):
        return list(group_to_eps.get(group, []))

    with patch("importlib.metadata.entry_points", side_effect=_entry_points):
        yield
