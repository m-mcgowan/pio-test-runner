"""Acceptance test fixtures for on-device filter validation."""


def pytest_addoption(parser):
    parser.addoption("--port", required=True, help="Serial port for the device")
    parser.addoption("--baud", default=115200, type=int, help="Baud rate")
