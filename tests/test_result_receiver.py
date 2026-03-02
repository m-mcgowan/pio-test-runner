"""Tests for TestResultReceiver."""

from embedded_bridge.receivers.base import Receiver

from pio_test_runner.result_receiver import TestResult, TestResultReceiver


class TestDoctestParsing:
    def test_all_passing_summary(self):
        receiver = TestResultReceiver(framework="doctest")
        receiver.feed("[doctest] test cases:  3 |  3 passed | 0 failed |")
        assert receiver.is_complete
        results = receiver.drain_results()
        assert len(results) == 1
        assert results[0].passed is True

    def test_failure_with_source(self):
        receiver = TestResultReceiver(framework="doctest")
        receiver.feed("TEST CASE: my test case")
        receiver.feed("test/main.cpp:42: FAILED:")
        receiver.feed("  CHECK( x == 1 )")
        receiver.feed("with expansion:")
        receiver.feed("  0 == 1")
        receiver.feed("[doctest] test cases:  1 |  0 passed | 1 failed |")

        results = receiver.drain_results()
        assert len(results) == 1
        assert results[0].name == "my test case"
        assert results[0].passed is False
        assert results[0].source == "test/main.cpp:42"
        assert "0 == 1" in results[0].message

    def test_multiple_failures(self):
        receiver = TestResultReceiver(framework="doctest")
        receiver.feed("TEST CASE: first test")
        receiver.feed("test/a.cpp:10: FAILED:")
        receiver.feed("  CHECK( false )")

        receiver.feed("TEST CASE: second test")
        receiver.feed("test/b.cpp:20: FAILED:")
        receiver.feed("  CHECK( 0 )")

        receiver.feed("[doctest] test cases:  2 |  0 passed | 2 failed |")

        results = receiver.drain_results()
        assert len(results) == 2
        assert results[0].name == "first test"
        assert results[1].name == "second test"

    def test_test_case_name_tracked(self):
        receiver = TestResultReceiver(framework="doctest")
        receiver.feed("TEST CASE: widget initialization")
        receiver.feed("test/widget.cpp:5: FAILED:")
        receiver.feed("  assertion failed")
        receiver.feed("[doctest] test cases:  1 |  0 passed | 1 failed |")

        results = receiver.drain_results()
        assert results[0].name == "widget initialization"

    def test_no_individual_results_synthesizes_from_summary(self):
        receiver = TestResultReceiver(framework="doctest")
        receiver.feed("[doctest] test cases:  5 |  5 passed | 0 failed |")

        results = receiver.drain_results()
        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].name == "all tests"

    def test_ignores_output_after_summary(self):
        receiver = TestResultReceiver(framework="doctest")
        receiver.feed("[doctest] test cases:  1 |  1 passed | 0 failed |")
        assert receiver.is_complete
        receiver.feed("stray output after completion")
        # Should not crash or add results


class TestUnityParsing:
    def test_pass_result(self):
        receiver = TestResultReceiver(framework="unity")
        receiver.feed("test/test_main.c:15:test_addition:PASS")

        results = receiver.drain_results()
        assert len(results) == 1
        assert results[0].name == "test_addition"
        assert results[0].passed is True
        assert results[0].source == "test/test_main.c:15"

    def test_fail_result_with_message(self):
        receiver = TestResultReceiver(framework="unity")
        receiver.feed("test/test_main.c:20:test_subtraction:FAIL: Expected 5 Was 3")

        results = receiver.drain_results()
        assert len(results) == 1
        assert results[0].name == "test_subtraction"
        assert results[0].passed is False
        assert results[0].message == "Expected 5 Was 3"

    def test_ignore_result(self):
        receiver = TestResultReceiver(framework="unity")
        receiver.feed("test/test_main.c:25:test_multiply:IGNORE")

        results = receiver.drain_results()
        assert len(results) == 1
        assert results[0].name == "test_multiply"
        assert results[0].passed is False  # IGNORE is not a pass

    def test_summary_marks_complete(self):
        receiver = TestResultReceiver(framework="unity")
        receiver.feed("test/test_main.c:10:test_one:PASS")
        receiver.feed("test/test_main.c:20:test_two:PASS")
        receiver.feed("-----------------------")
        receiver.feed("2 Tests 0 Failures 0 Ignored")

        assert receiver.is_complete
        results = receiver.drain_results()
        assert len(results) == 2

    def test_multiple_results(self):
        receiver = TestResultReceiver(framework="unity")
        receiver.feed("test/a.c:1:test_a:PASS")
        receiver.feed("test/b.c:2:test_b:FAIL: wrong value")
        receiver.feed("test/c.c:3:test_c:PASS")

        results = receiver.drain_results()
        assert len(results) == 3
        assert results[0].passed is True
        assert results[1].passed is False
        assert results[2].passed is True


class TestAutoDetection:
    def test_detects_doctest(self):
        receiver = TestResultReceiver(framework="auto")
        receiver.feed("[doctest] test cases:  1 |  1 passed | 0 failed |")

        assert receiver.is_complete
        results = receiver.drain_results()
        assert len(results) == 1

    def test_detects_unity(self):
        receiver = TestResultReceiver(framework="auto")
        receiver.feed("test/main.c:10:test_hello:PASS")

        results = receiver.drain_results()
        assert len(results) == 1
        assert results[0].name == "test_hello"

    def test_non_test_output_before_detection(self):
        receiver = TestResultReceiver(framework="auto")
        receiver.feed("Booting...")
        receiver.feed("WiFi connected")
        receiver.feed("test/main.c:10:test_hello:PASS")

        results = receiver.drain_results()
        assert len(results) == 1

    def test_detects_doctest_from_test_case_line(self):
        receiver = TestResultReceiver(framework="auto")
        receiver.feed("TEST CASE: my test")
        receiver.feed("test/x.cpp:1: FAILED:")
        receiver.feed("  oops")
        receiver.feed("[doctest] test cases:  1 |  0 passed | 1 failed |")

        results = receiver.drain_results()
        assert len(results) == 1
        assert results[0].name == "my test"

    def test_detects_doctest_from_failure(self):
        receiver = TestResultReceiver(framework="auto")
        receiver.feed("test/x.cpp:1: FAILED:")
        receiver.feed("  bad value")
        receiver.feed("[doctest] test cases:  1 |  0 passed | 1 failed |")

        results = receiver.drain_results()
        assert len(results) == 1
        assert results[0].passed is False


class TestDrainResults:
    def test_returns_and_clears(self):
        receiver = TestResultReceiver(framework="unity")
        receiver.feed("test/a.c:1:test_a:PASS")

        results1 = receiver.drain_results()
        assert len(results1) == 1

        results2 = receiver.drain_results()
        assert len(results2) == 0

    def test_empty_when_nothing(self):
        receiver = TestResultReceiver()
        assert receiver.drain_results() == []


class TestIsComplete:
    def test_not_initially(self):
        receiver = TestResultReceiver()
        assert receiver.is_complete is False

    def test_after_doctest_summary(self):
        receiver = TestResultReceiver(framework="doctest")
        receiver.feed("[doctest] test cases:  1 |  1 passed | 0 failed |")
        assert receiver.is_complete is True

    def test_after_unity_summary(self):
        receiver = TestResultReceiver(framework="unity")
        receiver.feed("1 Tests 0 Failures 0 Ignored")
        assert receiver.is_complete is True


class TestInvalidFramework:
    def test_raises_on_invalid(self):
        import pytest

        with pytest.raises(ValueError, match="Unknown framework"):
            TestResultReceiver(framework="googletest")


class TestBytesInput:
    def test_bytes_decoded(self):
        receiver = TestResultReceiver(framework="unity")
        receiver.feed(b"test/a.c:1:test_a:PASS")

        results = receiver.drain_results()
        assert len(results) == 1
        assert results[0].passed is True


class TestReceiverProtocol:
    def test_satisfies_receiver_protocol(self):
        receiver = TestResultReceiver()
        assert isinstance(receiver, Receiver)
