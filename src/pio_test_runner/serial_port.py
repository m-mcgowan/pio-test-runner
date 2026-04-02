"""Safe serial port management for ESP32 USB-CDC devices.

ESP32-S3 USB-CDC resets the device when DTR is asserted on serial open.
macOS asserts DTR by default when opening a serial port. This module
provides a safe open that avoids the reset.

Usage::

    from pio_test_runner.serial_port import open_serial

    ser = open_serial("/dev/cu.usbmodem1424101")
    # Device is NOT reset — DTR/RTS held low
    line = ser.readline()

To intentionally reset the device (e.g. after upload)::

    ser = open_serial("/dev/cu.usbmodem1424101", reset=True)
"""

import time

try:
    import serial as pyserial
except ImportError:
    pyserial = None


def open_serial(port, baudrate=115200, timeout=1, reset=False, retries=5):
    """Open a serial port without triggering a device reset.

    Uses ``serial_for_url(do_not_open=True)`` to pre-configure DTR/RTS
    before opening. This prevents ESP32-S3 USB-CDC from interpreting the
    DTR assertion as a reset request (USB_UART_CHIP_RESET).

    Args:
        port: Serial port path (e.g. /dev/cu.usbmodem1424101)
        baudrate: Baud rate (default 115200)
        timeout: Read timeout in seconds (default 1)
        reset: If True, assert DTR/RTS to trigger device reset after open
        retries: Number of retry attempts if port not ready (USB re-enum)

    Returns:
        An open serial.Serial instance

    Raises:
        serial.SerialException: If port cannot be opened after retries
    """
    if pyserial is None:
        raise RuntimeError("pyserial not installed: pip install pyserial")

    for attempt in range(retries):
        try:
            ser = pyserial.serial_for_url(port, do_not_open=True)
            ser.baudrate = baudrate
            ser.timeout = timeout

            if not reset:
                # Hold DTR/RTS low to prevent ESP32-S3 USB-CDC reset
                ser.dtr = False
                ser.rts = False

            ser.open()

            if reset:
                # DTR/RTS toggle to trigger reset
                ser.flushInput()
                ser.setDTR(False)
                ser.setRTS(False)
                time.sleep(0.1)
                ser.setDTR(True)
                ser.setRTS(True)
                time.sleep(0.1)

            ser.reset_input_buffer()
            return ser

        except (OSError, pyserial.SerialException):
            if attempt < retries - 1:
                time.sleep(1)
            else:
                raise
