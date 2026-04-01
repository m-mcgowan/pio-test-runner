"""Shared helpers for acceptance tests."""

import re
import time

import serial as pyserial

from pio_test_runner.protocol import format_crc


def open_device(port, baud=115200):
    """Open serial connection and drain stale data."""
    ser = pyserial.Serial(port, baud, timeout=1)
    ser.reset_input_buffer()
    return ser


def wait_for_ready(ser, timeout=15):
    """Wait for PTR:READY from device. Sends RESTART if needed."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = ser.readline().decode("utf-8", errors="replace").strip()
        if "PTR:READY" in line:
            return True
    # Try restart
    cmd = format_crc("RESTART")
    ser.write((cmd + "\n").encode())
    time.sleep(3)
    ser.reset_input_buffer()
    deadline = time.time() + 15
    while time.time() < deadline:
        line = ser.readline().decode("utf-8", errors="replace").strip()
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
    ser.write((crc_command + "\n").encode())

    tests_run = []
    mem_markers = []
    test_starts = []
    raw_lines = []
    total = passed = failed = skipped = 0
    current_test = None

    deadline = time.time() + timeout
    while time.time() < deadline:
        line = ser.readline().decode("utf-8", errors="replace").strip()
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
    """Send SLEEP command to put device in idle state for next test."""
    cmd = format_crc("SLEEP")
    ser.write((cmd + "\n").encode())
    time.sleep(0.5)


def has_line_matching(lines, pattern):
    """Check if any line matches the regex pattern."""
    return any(re.search(pattern, line) for line in lines)
