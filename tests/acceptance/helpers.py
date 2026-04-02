"""Shared helpers for acceptance tests."""

import re
import time

import serial as pyserial

from pio_test_runner.protocol import format_crc
from pio_test_runner.serial_port import open_serial


def open_device(port, baud=115200, retries=10):
    """Open serial connection without triggering a device reset."""
    return open_serial(port, baudrate=baud, retries=retries)


def wait_for_ready(ser, timeout=15):
    """Wait for PTR:READY from device.

    If the device doesn't respond, sends RESTART to wake it from
    the idle loop. If the port itself is dead (device sleeping),
    the caller must handle the SerialException and power-cycle.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            line = ser.readline().decode("utf-8", errors="replace").strip()
        except Exception:
            time.sleep(1)
            continue
        if "PTR:READY" in line:
            return True
    # Device may be in idle loop but not sending READY yet — send RESTART
    try:
        cmd = format_crc("RESTART")
        ser.write((cmd + "\n").encode())
    except Exception:
        return False
    time.sleep(3)
    try:
        ser.reset_input_buffer()
    except Exception:
        return False
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            line = ser.readline().decode("utf-8", errors="replace").strip()
        except Exception:
            time.sleep(1)
            continue
        if "PTR:READY" in line:
            return True
    return False


def send_command(ser, command, timeout=120):
    """Send a command and collect output until PTR:DONE.

    Returns a dict with:
      tests_run:   list of test names that executed
      total:       total test count from doctest summary
      passed:      passed count
      failed:      failed count
      skipped:     skipped count
      mem_markers: list of (test_name, free_before, free_after, delta) tuples
      test_starts: list of dicts with suite, name, timeout fields
      raw_lines:   all output lines
    """
    assert wait_for_ready(ser), "Device did not send PTR:READY"

    crc_command = format_crc(command)
    print(f"[accept] Sending: {crc_command}")
    ser.write((crc_command + "\n").encode())

    tests_run = []
    mem_markers = []
    test_starts = []
    raw_lines = []
    total = passed = failed = skipped = 0
    current_test = None

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            line = ser.readline().decode("utf-8", errors="replace").strip()
        except (OSError, pyserial.SerialException):
            # USB-CDC port dropped momentarily (e.g. after RESTART)
            # Wait and retry — port usually comes back within seconds
            time.sleep(2)
            try:
                ser.close()
            except Exception:
                pass
            for _ in range(5):
                try:
                    ser.open()
                    break
                except Exception:
                    time.sleep(1)
            continue
        if not line:
            continue
        raw_lines.append(line)

        # Parse test start markers
        m = re.search(
            r'PTR:TEST:START\s+suite="([^"]*)"\s+name="([^"]*)"(?:\s+timeout=(\d+))?',
            line,
        )
        if m:
            name = m.group(2)
            tests_run.append(name)
            current_test = name
            start_info = {"suite": m.group(1), "name": name}
            if m.group(3):
                start_info["timeout"] = int(m.group(3))
            test_starts.append(start_info)

        # Parse memory markers
        m = re.search(r"PTR:MEM:BEFORE\s+free=(\d+)", line)
        if m:
            free_before = int(m.group(1))

        m = re.search(r"PTR:MEM:AFTER\s+free=(\d+)\s+delta=([+-]?\d+)", line)
        if m:
            free_after = int(m.group(1))
            delta = int(m.group(2))
            mem_markers.append(
                {
                    "test": current_test,
                    "free_before": free_before,
                    "free_after": free_after,
                    "delta": delta,
                }
            )

        # Parse doctest summary
        m = re.search(
            r"test cases:\s*(\d+)\s*\|\s*(\d+)\s*passed\s*\|\s*(\d+)\s*failed\s*\|\s*(\d+)\s*skipped",
            line,
        )
        if m:
            total = int(m.group(1))
            passed = int(m.group(2))
            failed = int(m.group(3))
            skipped = int(m.group(4))

        if "PTR:DONE" in line:
            break

    return {
        "tests_run": tests_run,
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "mem_markers": mem_markers,
        "test_starts": test_starts,
        "raw_lines": raw_lines,
    }


def send_sleep(ser):
    """Reset device for next test cycle.

    Sends RESTART (not SLEEP) so the device reboots and enters READY
    state for the next test. SLEEP would enter deep sleep, losing the
    USB-CDC port.

    ESP32-S3 USB-CDC may drop briefly during restart. The next test's
    open_device() retries to handle this.
    """
    try:
        cmd = format_crc("RESTART")
        ser.write((cmd + "\n").encode())
    except Exception:
        pass  # port may already be gone
    # Wait for device to reboot and USB-CDC to re-enumerate
    time.sleep(5)


def has_line_matching(lines, pattern):
    """Check if any line matches the regex pattern."""
    return any(re.search(pattern, line) for line in lines)
