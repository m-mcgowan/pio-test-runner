"""Acceptance test fixtures for on-device filter validation."""

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
