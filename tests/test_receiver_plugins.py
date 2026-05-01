"""Tests for receiver plugin discovery via setuptools entry points."""

from conftest import (
    FakeEntryPoint,
    MockProjectConfig,
    MockTestRunnerOptions,
    MockTestSuite,
    fake_entry_points,
)

from etst.runner import EmbeddedTestRunner


class _RecordingReceiver:
    """Minimal plugin receiver that just records what it sees."""

    def __init__(self, runner):
        self.runner = runner
        self.messages = []
        self.partition_started = 0
        self.partition_completed = 0

    def feed(self, message):
        self.messages.append(message)


def make_runner_with_plugins(plugins):
    """Build a runner with fake entry-point plugins active."""
    eps = [FakeEntryPoint(name, cls) for name, cls in plugins.items()]
    with fake_entry_points({"embedded_test_runner.receivers": eps}):
        return EmbeddedTestRunner(
            MockTestSuite(), MockProjectConfig(), MockTestRunnerOptions()
        )


def test_plugin_is_discovered_and_instantiated():
    runner = make_runner_with_plugins({"recording": _RecordingReceiver})

    # Plugin instances are tracked separately so lifecycle hooks can find them.
    assert len(runner._plugin_receivers) == 1
    plugin = runner._plugin_receivers[0]
    assert isinstance(plugin, _RecordingReceiver)
    assert plugin.runner is runner


def test_plugin_receives_messages_through_router():
    runner = make_runner_with_plugins({"recording": _RecordingReceiver})
    plugin = runner._plugin_receivers[0]

    runner.router.feed("hello")
    runner.router.feed("world")

    assert plugin.messages == ["hello", "world"]


class _CovOnlyReceiver:
    """Plugin that only wants COV: lines (instance-method predicate)."""

    def __init__(self, runner):
        self.messages = []

    def predicate(self, message):
        return isinstance(message, str) and message.startswith("COV:")

    def feed(self, message):
        self.messages.append(message)


class _AttrPredicateReceiver:
    """Plugin that uses a predicate attribute (callable, not a method)."""

    def __init__(self, runner):
        self.messages = []
        self.predicate = lambda m: isinstance(m, str) and "important" in m

    def feed(self, message):
        self.messages.append(message)


def test_predicate_method_filters_messages():
    runner = make_runner_with_plugins({"covonly": _CovOnlyReceiver})
    plugin = runner._plugin_receivers[0]

    runner.router.feed("COV:DATA 0x1234")
    runner.router.feed("plain log line")
    runner.router.feed("COV:END")

    assert plugin.messages == ["COV:DATA 0x1234", "COV:END"]


def test_predicate_attribute_filters_messages():
    runner = make_runner_with_plugins({"attr": _AttrPredicateReceiver})
    plugin = runner._plugin_receivers[0]

    runner.router.feed("important event")
    runner.router.feed("noise")
    runner.router.feed("more important things")

    assert plugin.messages == ["important event", "more important things"]
