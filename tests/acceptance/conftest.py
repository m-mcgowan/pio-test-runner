"""Acceptance test fixtures for on-device filter validation."""

import time

import pytest


def pytest_addoption(parser):
    parser.addoption("--port", required=True, help="Serial port for the device")
    parser.addoption("--baud", default=115200, type=int, help="Baud rate")


@pytest.fixture(scope="session")
def port(request):
    return request.config.getoption("--port")


@pytest.fixture(scope="session")
def baud(request):
    return request.config.getoption("--baud")


@pytest.fixture(scope="session", autouse=True)
def ensure_device_awake(port):
    """Wait for device port to be available.

    After ETST_ON_DONE=restart, the USB-CDC port drops briefly during
    reboot. Wait for it to become openable. Don't read serial data —
    leave ETST:READY for send_command() to consume.
    """
    from helpers import open_device

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            ser = open_device(port, retries=1)
            ser.close()
            return
        except Exception:
            time.sleep(1)

    print(f"[accept] WARNING: Port {port} not available after 15s")
