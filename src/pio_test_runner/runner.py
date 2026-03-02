"""PlatformIO test runner plugin for embedded devices.

Extends PlatformIO's TestRunnerBase with crash detection, disconnect
handling, and framework-agnostic test result parsing via embedded-bridge
receivers.

Two modes of operation:

1. **Line callback mode** (default) — PIO owns the serial connection.
   Override ``on_testing_line_output()`` only. Simple, but read-only
   (cannot send commands to the device).

2. **Orchestrated mode** — the runner owns the serial connection via
   ``stage_testing()`` override. Supports bidirectional protocol
   (READY/RUN/DONE), sleep orchestration, and reconnection after
   deep sleep.

Usage: create ``test/test_custom_runner.py`` in your PlatformIO project::

    from pio_test_runner.runner import EmbeddedTestRunner

    class CustomTestRunner(EmbeddedTestRunner):
        pass

Then set ``test_framework = custom`` in ``platformio.ini``.
"""

import logging
import time
import traceback

try:
    import click
except ImportError:
    click = None

try:
    import serial
except ImportError:
    serial = None

from embedded_bridge.receivers import CrashDetector, MemoryTracker, Router

from .disconnect import DisconnectHandler
from .ready_run_protocol import ProtocolState, ReadyRunProtocol
from .timing_tracker import TestTimingTracker

logger = logging.getLogger(__name__)

# Import PIO classes — available at runtime when used as a PIO plugin.
# Tests mock these.
try:
    from platformio.device.finder import SerialPortFinder
    from platformio.test.result import TestCase, TestStatus
    from platformio.test.runners.base import TestRunnerBase
    from platformio.test.runners.doctest import DoctestTestRunner
except ImportError:
    TestRunnerBase = object
    DoctestTestRunner = None
    TestCase = None
    TestStatus = None
    SerialPortFinder = None

# Import robust parser (works with or without PIO)
from .robust_doctest_parser import RobustDoctestParser


def _echo(msg, **kwargs):
    """Print via click if available, else plain print."""
    if click is not None:
        click.echo(msg, **kwargs)
    else:
        print(msg, end=kwargs.get("nl", "\n") if "nl" not in kwargs else ("" if not kwargs["nl"] else "\n"))


def _secho(msg, **kwargs):
    """Styled print via click if available, else plain print."""
    if click is not None:
        click.secho(msg, **kwargs)
    else:
        print(msg)


# Use DoctestTestRunner when available (provides doctest result parsing).
# Fall back to TestRunnerBase when PIO isn't installed (tests).
_BaseRunner = DoctestTestRunner if DoctestTestRunner is not None else TestRunnerBase


class EmbeddedTestRunner(_BaseRunner):
    """PlatformIO test runner with crash detection and disconnect handling.

    Uses embedded-bridge receivers to monitor device output for crashes,
    manage disconnect/reconnect windows, and parse test results from any
    supported framework (doctest, Unity, auto-detect).

    Subclass and override ``configure_*`` methods to customize behavior:

    - ``configure_orchestrated()`` — return True to enable stage_testing()
    - ``configure_sleep_padding()`` — extra seconds to wait after sleep

    PIO requires custom runners in ``test/test_custom_runner.py`` with
    class name ``CustomTestRunner``. Subclass this runner there::

        from pio_test_runner.runner import EmbeddedTestRunner

        class CustomTestRunner(EmbeddedTestRunner):
            pass
    """

    NAME = "embedded"

    def __init__(self, test_suite, project_config, options=None):
        super().__init__(test_suite, project_config, options)

        # Receivers — our value-add over PIO's built-in parsing
        self.crash_detector = CrashDetector()
        self.disconnect_handler = DisconnectHandler()
        self.protocol = ReadyRunProtocol()
        self.memory_tracker = MemoryTracker()
        self.timing_tracker = TestTimingTracker()

        # Router feeds all receivers (result parsing delegated to PIO)
        self.router = Router([
            (self.crash_detector, None),
            (self.disconnect_handler, None),
            (self.protocol, None),
            (self.memory_tracker, None),
            (self.timing_tracker, None),
        ])

        # Use robust parser if extending DoctestTestRunner
        if DoctestTestRunner is not None and hasattr(self, "_tc_parser"):
            self._tc_parser = RobustDoctestParser()

        # Track whether our runner explicitly finished the suite
        self._finished_by_runner = False

        # Serial connection (orchestrated mode only)
        self._ser = None
        self._line_buf = ""  # partial line buffer for serial reads

    # ------------------------------------------------------------------
    # Configuration hooks (override in subclass)
    # ------------------------------------------------------------------

    def configure_orchestrated(self) -> bool:
        """Return True to use orchestrated mode (stage_testing override).

        When True, the runner owns the serial connection and implements
        the READY/RUN/DONE bidirectional protocol. When False (default),
        PIO owns serial and the runner only processes line output.
        """
        return True

    def configure_sleep_padding(self) -> float:
        """Extra seconds to wait after device's reported sleep duration."""
        return 5.0

    def configure_hang_timeout(self) -> float:
        """Seconds without output before declaring a hang (orchestrated mode)."""
        return 30.0

    # ------------------------------------------------------------------
    # Line callback mode (PIO owns serial)
    # ------------------------------------------------------------------

    def on_testing_line_output(self, line):
        """Process a line of test output (line callback mode).

        PIO owns serial and handles echoing + result parsing.
        We feed our receivers for crash detection, disconnect handling,
        memory tracking, and timing.
        """
        if self._finished_by_runner:
            return

        self.router.feed(line)
        self._sync_test_name()
        self._check_crash()

    # ------------------------------------------------------------------
    # Orchestrated mode (runner owns serial)
    # ------------------------------------------------------------------

    def stage_testing(self):
        """Override PIO's stage_testing to manage the full serial lifecycle.

        Only active when ``configure_orchestrated()`` returns True.
        """
        if not self.configure_orchestrated():
            return super().stage_testing()

        if self.options.without_testing:
            return None

        _secho("Testing...", bold=True)
        _echo("")

        try:
            self._run_test_cycle(command="RUN_ALL", reset=True)

            if self.protocol.state == ProtocolState.SLEEPING:
                self._handle_sleep_resume()

        except Exception as exc:
            if serial is not None and isinstance(exc, serial.SerialException):
                if self.protocol.state in (ProtocolState.SLEEPING, ProtocolState.RUNNING):
                    # Expected — USB-CDC disconnects during deep sleep
                    self._handle_sleep_resume()
                else:
                    _secho(f"Serial error: {exc}", fg="red", err=True)
                    self._add_error_case("serial_error", str(exc), exc)
            else:
                _secho(f"Runner error: {exc}", fg="red", err=True)
                traceback.print_exc()
                self._add_error_case("runner_error", str(exc), exc)
        finally:
            self._close_serial()
            self._print_summary()
            if not self.test_suite.is_finished():
                self.test_suite.on_finish()

    def _run_test_cycle(self, command, reset=True):
        """Run one test cycle: open serial, wait for READY, send command, process."""
        self.protocol.reset()
        self._line_buf = ""
        last_activity = time.time()
        first_assertion_seen = False

        self._open_serial(reset=reset)

        # Main read loop
        while self.protocol.state in (ProtocolState.WAITING_FOR_READY, ProtocolState.RUNNING):
            try:
                data = self._ser.read(self._ser.in_waiting or 1)
            except Exception:
                if self.protocol.state == ProtocolState.RUNNING:
                    _echo("[runner] Serial port disconnected")
                    break
                raise

            if data:
                self._on_serial_data(data)
                last_activity = time.time()

                # Check if READY → send command
                if self.protocol.state == ProtocolState.READY:
                    self._send_command(command)
                    self.protocol.command_sent()
            else:
                # Check for hang
                if first_assertion_seen:
                    elapsed = time.time() - last_activity
                    if elapsed > self.configure_hang_timeout():
                        _secho(
                            f"\nHANG DETECTED: No output for {int(elapsed)}s — aborting",
                            fg="red", err=True,
                        )
                        test_name = self.protocol.current_test_full or "unknown"
                        self._add_error_case(
                            test_name,
                            f"Test hang: no output for {int(elapsed)}s",
                            RuntimeError(f"Test hang: no output for {int(elapsed)}s"),
                        )
                        break

            # Track assertion activity for hang detection
            # (crude but effective — avoids false hangs during boot)
            if not first_assertion_seen and self.protocol.state == ProtocolState.RUNNING:
                first_assertion_seen = True

            # Check if finished
            if self.protocol.state == ProtocolState.FINISHED:
                break
            if self.crash_detector.triggered:
                break
            if self.test_suite.is_finished():
                self._finished_by_runner = True
                break

        self._close_serial()

    def _on_serial_data(self, data):
        """Process raw bytes from serial into complete lines and feed receivers.

        Buffers partial lines across reads so protocol markers split across
        two serial reads (e.g. "REA" + "DY\\n") are handled correctly.
        """
        text = self._line_buf + data.decode("utf-8", errors="replace")
        # Split into complete lines, keeping any trailing partial in buffer
        parts = text.split("\n")
        self._line_buf = parts[-1]  # incomplete trailing fragment (or "")
        for line in parts[:-1]:
            line = line.rstrip("\r")
            if not line:
                continue
            # Our receivers process first (protocol, crash, memory, timing)
            self.router.feed(line)
            self._sync_test_name()
            self._check_crash()
            if self._finished_by_runner:
                return

            # Suppress output during disconnect windows and pre-READY boot
            if self.disconnect_handler.active:
                continue
            state = self.protocol.state
            if state == ProtocolState.WAITING_FOR_READY and not self.options.verbose:
                continue

            # Delegate result parsing + echo to PIO's base runner
            try:
                super().on_testing_line_output(line + "\n")
            except Exception:
                pass  # Parser errors are non-fatal

    def _handle_sleep_resume(self):
        """Wait for device to wake from deep sleep and resume testing."""
        sleep_s = self.protocol.sleep_duration_ms / 1000
        padding = self.configure_sleep_padding()
        total_wait = sleep_s + padding

        _echo(
            f"[runner] Device sleeping for {sleep_s:.0f}s, "
            f"waiting {total_wait:.0f}s for wake..."
        )
        time.sleep(total_wait)

        # Build filter for the sleeping test
        filter_cmd = f"RUN: *{self.protocol.sleeping_test_name}*"
        _echo(f"[runner] Resuming with: {filter_cmd}")

        self.protocol.reset_for_wake()
        try:
            self._run_test_cycle(command=filter_cmd, reset=False)
        except Exception as exc:
            if serial is not None and isinstance(exc, serial.SerialException):
                _echo("[runner] Port not ready, waiting 5s more...")
                time.sleep(5)
                self._run_test_cycle(command=filter_cmd, reset=False)
            else:
                raise

    # ------------------------------------------------------------------
    # Serial port management (orchestrated mode)
    # ------------------------------------------------------------------

    def _resolve_port(self):
        """Resolve the serial port using PlatformIO's SerialPortFinder."""
        if SerialPortFinder is None:
            raise RuntimeError("PlatformIO not available")
        project_options = self.project_config.items(
            env=self.test_suite.env_name, as_dict=True
        )
        port = SerialPortFinder(
            board_config=self.platform.board_config(project_options["board"]),
            upload_protocol=project_options.get("upload_protocol"),
            ensure_ready=True,
            verbose=self.options.verbose,
        ).find(initial_port=self.get_test_port())
        if not port:
            raise RuntimeError(
                "Could not find test port. Specify test_port in platformio.ini "
                "or use --test-port."
            )
        return port

    def _open_serial(self, reset=True):
        """Open serial connection to the device."""
        if serial is None:
            raise RuntimeError("pyserial not installed")
        port = self._resolve_port()
        self._ser = serial.serial_for_url(
            port,
            do_not_open=True,
            baudrate=self.get_test_speed(),
            timeout=1,
        )
        if reset and not self.options.no_reset:
            self._ser.rts = self.options.monitor_rts
            self._ser.dtr = self.options.monitor_dtr
        # else: leave DTR/RTS at pyserial defaults (True/True).
        # On macOS, the kernel asserts DTR when the fd opens.
        # Explicitly setting False would create a high→low transition
        # that triggers USB_UART_CHIP_RESET on ESP32-S3 USB-Serial/JTAG.
        self._ser.open()

        if reset and not self.options.no_reset:
            self._ser.flushInput()
            self._ser.setDTR(False)
            self._ser.setRTS(False)
            time.sleep(0.1)
            self._ser.setDTR(True)
            self._ser.setRTS(True)
            time.sleep(0.1)

    def _close_serial(self):
        """Close serial connection."""
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None

    def _send_command(self, command):
        """Send a command string to the device."""
        if self._ser and self._ser.is_open:
            self._ser.write(f"{command}\n".encode())
            self._ser.flush()
            _echo(f"[runner] Sent: {command}")

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _sync_test_name(self):
        """Keep memory tracker in sync with current test from protocol."""
        test_full = self.protocol.current_test_full
        if test_full:
            self.memory_tracker.set_current_test(test_full)

    def _check_crash(self):
        """Check for crash and report if detected."""
        if self.crash_detector.triggered and not self._finished_by_runner:
            crash = self.crash_detector.crash
            self.test_suite.add_case(TestCase(
                name=self.protocol.current_test_full or f"{self.test_suite.env_name}:crash",
                status=TestStatus.ERRORED,
                message=crash.reason,
                stdout="\n".join(crash.lines),
            ))
            self._finished_by_runner = True
            self.test_suite.on_finish()

    def _add_error_case(self, name, message, exc):
        """Add an error test case to the suite."""
        self.test_suite.add_case(TestCase(
            name=name,
            status=TestStatus.ERRORED,
            message=message,
            exception=exc,
        ))

    # ------------------------------------------------------------------
    # Summary reporting
    # ------------------------------------------------------------------

    def _print_summary(self):
        """Print memory and timing summary after tests complete."""
        self.timing_tracker.finalize()

        mem_report = self.memory_tracker.report()
        if mem_report:
            _echo("")
            _secho(mem_report, bold=True)

        timing_report = self.timing_tracker.report()
        if timing_report:
            _echo("")
            _echo(timing_report)

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def teardown(self):
        """Check for silent hang on teardown.

        Only fires if our runner did not explicitly finish the suite.
        This catches the case where PIO's serial reader timed out
        (no output for 600s) without our runner seeing a completion
        or crash — likely a device hang.
        """
        if self._finished_by_runner:
            return

        self.crash_detector.check_timeout()
        if self.crash_detector.triggered:
            crash = self.crash_detector.crash
            self.test_suite.add_case(TestCase(
                name=f"{self.test_suite.env_name}:hang",
                status=TestStatus.ERRORED,
                message=crash.reason,
            ))
