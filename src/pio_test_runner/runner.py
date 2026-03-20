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
import os
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
from embedded_bridge.receivers import SleepWakeMonitor

from .disconnect import DisconnectHandler
from .protocol import format_crc
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

        # Sleep/wake monitoring via port disappearance (USB-CDC)
        self.sleep_monitor = SleepWakeMonitor()

        # Serial connection (orchestrated mode only)
        self._ser = None
        self._port_path = None  # persists across serial open/close for sleep monitoring
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

    def _build_initial_command(self):
        """Build the initial RUN command from filters.

        Filters can come from two sources (both are combined):

        1. Program args (``pio test -a "--ts *pattern*"``):
           Passed to native tests as argv; for embedded tests we forward
           them to the device via the RUN: protocol. Supports the same
           flags as doctest: --tc, --ts, --tce, --tse.

        2. Environment variables:
           PTR_TEST_CASE=*pattern*       → --tc (test-case filter)
           PTR_TEST_SUITE=*pattern*      → --ts (test-suite filter)
           PTR_TEST_CASE_EXCLUDE=*pat*   → --tce (test-case-exclude)
           PTR_TEST_SUITE_EXCLUDE=*pat*  → --tse (test-suite-exclude)

        Returns "RUN_ALL" if no filters specified, otherwise
        "RUN: --tc ... --ts ..." etc.
        """
        filters = []

        # Source 1: program args from pio test -a "..."
        program_args = getattr(self.options, "program_args", None)
        if program_args:
            # program_args is a list of strings, e.g. ["--ts", "*BHI385*"]
            # Pass them through directly — the firmware parser handles them.
            filters.extend(program_args)

        # Source 2: environment variables
        env_map = {
            "PTR_TEST_CASE": "--tc",
            "PTR_TEST_SUITE": "--ts",
            "PTR_TEST_CASE_EXCLUDE": "--tce",
            "PTR_TEST_SUITE_EXCLUDE": "--tse",
        }
        for env_var, flag in env_map.items():
            value = os.environ.get(env_var, "").strip()
            if value:
                filters.append(f"{flag} {value}")

        if not filters:
            return "RUN_ALL"

        command = "RUN: " + " ".join(filters)
        _echo(f"[runner] Filters: {command}")
        return command

    def stage_testing(self):
        """Override PIO's stage_testing to manage the full serial lifecycle.

        Only active when ``configure_orchestrated()`` returns True.

        Handles deep sleep mid-test: when a test enters deep sleep, the
        runner resumes it after wake, then sends RESUME_AFTER to run
        remaining tests. The device handles the listing/exclude logic
        internally. The loop continues until FINISHED (no more sleeps).
        """
        if not self.configure_orchestrated():
            return super().stage_testing()

        if self.options.without_testing:
            return None

        _secho("Testing...", bold=True)
        _echo("")

        try:
            self.protocol.reset_all()
            initial_command = self._build_initial_command()
            self._run_test_cycle(command=initial_command, reset=True)

            # Loop until all tests complete. When a test enters deep sleep,
            # the device reboots and context.run() is interrupted. We:
            #   1. Resume the sleeping test (Phase 2)
            #   2. Send RESUME_AFTER:<sleep_test> — device skips all tests
            #      up to and including it, running only remaining tests
            #   3. Repeat until FINISHED (no more sleeps)
            MAX_SLEEP_RETRIES = 3  # prevent infinite loops from firmware bugs
            sleep_retry_counts: dict[str, int] = {}
            while self.protocol.state == ProtocolState.SLEEPING:
                sleep_test = self.protocol.sleeping_test_name

                # Guard against infinite sleep/resume loops (e.g. wake stub
                # stack corrupting RTC_NOINIT markers so Phase 2 never runs)
                retry_count = sleep_retry_counts.get(sleep_test, 0)
                if retry_count >= MAX_SLEEP_RETRIES:
                    _secho(
                        f"[runner] ERROR: Test '{sleep_test}' entered sleep "
                        f"{retry_count} times — skipping (possible RTC memory "
                        f"corruption)", fg="red", err=True)
                    err = RuntimeError(
                        f"Infinite sleep loop detected after {retry_count} "
                        f"retries — test always enters Phase 1 on resume")
                    self._add_error_case(sleep_test, str(err), err)
                    # Close serial and restart to recover
                    self._close_serial()
                    break
                sleep_retry_counts[sleep_test] = retry_count + 1

                self._handle_sleep_resume()

                # After resume, run remaining tests starting after the
                # sleep test. The device does a listing pass to discover
                # test order and builds its own exclude list.
                if self.protocol.state != ProtocolState.SLEEPING and sleep_test:
                    _echo(f"[runner] Running remaining tests after: {sleep_test}")
                    # Send RESTART to reboot the device — required so sleep
                    # tests see a clean reset reason (not deep sleep wake).
                    self._restart_device()
                    self._run_test_cycle(
                        command=f"RESUME_AFTER: {sleep_test}", reset=False)

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
        self._finished_by_runner = False
        # Allow PIO's test suite to accept new results — it may have been
        # marked finished by a previous cycle's doctest summary line.
        self.test_suite._finished = False
        last_activity = time.time()
        first_assertion_seen = False

        self._open_serial(reset=reset)

        # Main read loop
        ready_deadline = time.time() + 30  # 30s timeout for WAITING_FOR_READY
        while self.protocol.state in (ProtocolState.WAITING_FOR_READY, ProtocolState.READY, ProtocolState.RUNNING):
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
                # Check for hang during WAITING_FOR_READY
                if self.protocol.state == ProtocolState.WAITING_FOR_READY:
                    if time.time() > ready_deadline:
                        _secho(
                            "\nTIMEOUT: No PTR:READY received in 30s — aborting",
                            fg="red", err=True,
                        )
                        self._add_error_case(
                            "ready_timeout",
                            "Device did not send PTR:READY within 30s",
                            RuntimeError("PTR:READY timeout"),
                        )
                        break

                # Check for hang during RUNNING
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
            # PIO's doctest parser may declare finished before PTR:DONE.
            # Keep reading so we receive PTR:DONE and can send SLEEP.
            if (self.protocol.state == ProtocolState.RUNNING
                    and self.test_suite.is_finished()):
                # Give firmware a few seconds to emit PTR:DONE
                _echo("[runner] PIO reports tests finished, waiting for PTR:DONE...")
                done_deadline = time.time() + 5
                while time.time() < done_deadline:
                    try:
                        data = self._ser.read(self._ser.in_waiting or 1)
                    except Exception:
                        break
                    if data:
                        self._on_serial_data(data)
                    if self.protocol.state == ProtocolState.FINISHED:
                        break
                break

        # Send SLEEP to put the device into deep sleep (prevents battery
        # drain and unintended test re-runs from USB reset).
        if self.protocol.state == ProtocolState.FINISHED and self._ser and self._ser.is_open:
            try:
                self._ser.write(b"SLEEP\n")
                self._ser.flush()
            except Exception:
                pass  # best-effort — device may have disconnected

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
        """Wait for device to wake from deep sleep and resume testing.

        Uses SleepWakeMonitor to confirm sleep via USB-CDC port
        disappearance, then polls for port reappearance to confirm wake.
        The sleep timer starts when the port actually drops, not when the
        firmware announces sleep.
        """
        sleep_s = self.protocol.sleep_duration_ms / 1000
        padding = self.configure_sleep_padding()
        port = self._port_path

        _echo(f"[runner] Device sleeping for {sleep_s:.0f}s...")

        # Close serial so the OS releases the port
        self._close_serial()

        if port:
            # Configure monitor with the port path
            self.sleep_monitor = SleepWakeMonitor(port_path=port)

            # Wait for port to disappear (confirms sleep entry)
            drop_deadline = time.monotonic() + 10
            while time.monotonic() < drop_deadline:
                self.sleep_monitor.check_port()
                if self.sleep_monitor.state == "sleeping":
                    _echo("[runner] Port dropped — sleep confirmed")
                    break
                time.sleep(0.1)
            else:
                _secho("[runner] WARNING: port did not disappear — "
                       "device may not have entered sleep", fg="yellow", err=True)

            # Wait for port to reappear (confirms wake)
            wake_deadline = time.monotonic() + sleep_s + padding
            while time.monotonic() < wake_deadline:
                self.sleep_monitor.check_port()
                if self.sleep_monitor.state == "waking":
                    _echo("[runner] Port reappeared — device waking")
                    break
                time.sleep(0.2)
            else:
                _secho(f"[runner] WARNING: port did not reappear within "
                       f"{sleep_s + padding:.0f}s", fg="yellow", err=True)
        else:
            # No port path available — fall back to blind wait
            time.sleep(sleep_s + padding)

        # Resume test via READY/RUN handshake
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
        self._port_path = port
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

        # On macOS, opening the serial fd asserts DTR which injects a
        # garbage byte into the device's USB-CDC RX buffer. Send a bare
        # newline to terminate any garbage — the device's readStringUntil()
        # returns the garbage as a short line which fails CRC and is discarded.
        self._ser.write(b"\n")

        if reset and not self.options.no_reset:
            self._ser.flushInput()
            self._ser.setDTR(False)
            self._ser.setRTS(False)
            time.sleep(0.1)
            self._ser.setDTR(True)
            self._ser.setRTS(True)
            time.sleep(0.1)

    def _restart_device(self):
        """Send RESTART command and wait for device to reboot.

        The device calls esp_restart() which triggers a software reset.
        On ESP32-S3 USB-CDC, the port disappears and reappears very quickly
        (much faster than deep sleep wake). We wait for the device's
        acknowledgment before closing serial, then let the reset complete.
        """
        if not self._ser or not self._ser.is_open:
            self._open_serial(reset=False)

        _echo("[runner] Sending RESTART")
        self._send_command("RESTART")

        # Wait for device to acknowledge RESTART before closing serial.
        # On macOS USB-CDC, closing the fd immediately after flush() can
        # abort the in-flight USB transfer before the device processes it.
        ack_deadline = time.time() + 5
        buf = ""
        while time.time() < ack_deadline:
            try:
                data = self._ser.read(self._ser.in_waiting or 1)
            except Exception:
                break  # Port disappeared — device is resetting
            if data:
                buf += data.decode("utf-8", errors="replace")
                if "Restarting" in buf:
                    _echo("[runner] Device acknowledged RESTART")
                    break
        else:
            _secho("[runner] WARNING: No RESTART ack within 5s", fg="yellow", err=True)

        self._close_serial()

        # Brief wait for esp_restart() to complete. Software reset on
        # ESP32-S3 USB-CDC is fast (~1s) — port monitoring often misses
        # the brief disconnect. The next _run_test_cycle will wait for
        # PTR:READY which confirms the device has rebooted.
        time.sleep(2)

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
            self._ser.write(f"{format_crc(command)}\n".encode())
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
                exception=RuntimeError(crash.reason),
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
                exception=RuntimeError(crash.reason),
            ))
