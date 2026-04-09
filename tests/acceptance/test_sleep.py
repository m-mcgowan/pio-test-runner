#!/usr/bin/env python3
"""Acceptance tests: deep sleep orchestration on real hardware.

Validates the complete sleep/wake cycle:
1. Device runs tests until one calls signal_sleep()
2. Host detects ETST:SLEEP, waits for device to wake
3. Host sends RUN: filter for the sleeping test (Phase 2)
4. Host sends RESUME_AFTER: to run remaining tests
5. All tests complete

The integration firmware has a test_z_deep_sleep.cpp that exercises
two sleep cycles with configurable durations.

NOTE: These tests require a device that can wake from deep sleep.
USB-CDC disappears during sleep — the test must handle port
reconnection. The device needs an RTC timer or external wake source.
"""

import re
import time

import pytest

from helpers import open_device, has_line_matching
from pio_test_runner.protocol import format_crc


@pytest.fixture
def device(port, baud):
    ser = open_device(port, baud)
    yield ser
    ser.close()


def collect_until(ser, sentinel, timeout=60):
    """Collect lines until a line containing sentinel is seen."""
    lines = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = ser.readline().decode("utf-8", errors="replace").strip()
        if not line:
            continue
        lines.append(line)
        if sentinel in line:
            return lines
    return lines


def wait_for_ready_after_sleep(port, baud, sleep_ms, timeout=30):
    """Wait for device to wake from sleep and send ETST:READY.

    After deep sleep, the USB-CDC port disappears and reappears.
    We re-open the serial port and wait for READY.
    """
    import serial as pyserial

    # Wait for sleep + wake + boot
    wait_s = (sleep_ms / 1000) + 5  # sleep duration + boot overhead
    time.sleep(wait_s)

    # Try to reconnect
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ser = pyserial.Serial(port, baud, timeout=1)
            # Wait for READY
            ready_deadline = time.time() + 10
            while time.time() < ready_deadline:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if "ETST:READY" in line:
                    return ser
            ser.close()
        except (OSError, pyserial.SerialException):
            time.sleep(1)

    pytest.fail("Device did not send ETST:READY after sleep wake")


class TestDeepSleep:
    """Full sleep/wake orchestration cycle."""

    @pytest.mark.skipif(
        True,  # TODO: enable when deep sleep test fixtures are ready
        reason="Deep sleep tests need port reconnection infrastructure",
    )
    def test_sleep_wake_resume(self, device, port, baud):
        """Complete sleep → wake → resume → remaining cycle."""
        # Phase 1: Run all tests until one sleeps
        assert has_line_matching(
            collect_until(device, "ETST:READY"), r"ETST:READY"
        )

        cmd = format_crc("RUN_ALL")
        device.write((cmd + "\n").encode())

        # Collect until ETST:SLEEP
        lines = collect_until(device, "ETST:SLEEP", timeout=120)
        sleep_line = [l for l in lines if "ETST:SLEEP" in l]
        assert sleep_line, "Expected ETST:SLEEP but test completed without sleep"

        # Extract sleep duration
        m = re.search(r"ETST:SLEEP ms=(\d+)", sleep_line[0])
        assert m, f"Could not parse sleep duration from: {sleep_line[0]}"
        sleep_ms = int(m.group(1))

        # Extract sleeping test name from ETST:TEST:START before SLEEP
        test_starts = [l for l in lines if "ETST:TEST:START" in l]
        assert test_starts, "No test started before sleep"
        m = re.search(r'name="([^"]*)"', test_starts[-1])
        sleeping_test = m.group(1)

        # Close port — device is about to sleep, USB-CDC will disappear
        device.close()

        # Phase 2: Wait for wake, reconnect, run sleeping test Phase 2
        ser = wait_for_ready_after_sleep(port, baud, sleep_ms)

        filter_cmd = format_crc(f'RUN: --tc "{sleeping_test}"')
        ser.write((filter_cmd + "\n").encode())

        # Collect Phase 2 results
        phase2_lines = collect_until(ser, "ETST:DONE", timeout=60)
        assert has_line_matching(phase2_lines, r"ETST:DONE")

        # Phase 3: Run remaining tests after the sleeping test
        remaining_lines = collect_until(ser, "ETST:READY", timeout=10)

        resume_cmd = format_crc(f"RESUME_AFTER: {sleeping_test}")
        ser.write((resume_cmd + "\n").encode())

        final_lines = collect_until(ser, "ETST:DONE", timeout=120)
        assert has_line_matching(final_lines, r"ETST:DONE")

        # Cleanup
        sleep_cmd = format_crc("SLEEP")
        ser.write((sleep_cmd + "\n").encode())
        ser.close()
