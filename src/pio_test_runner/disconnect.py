"""Disconnect/reconnect protocol handler.

Manages the protocol between firmware and host for planned serial
disconnections (deep sleep, reset, peripheral reflash, etc.). The
handler tracks state but does not block — the caller decides how to
respond to disconnect windows.

Wire format (firmware → host)::

    ETST:DISCONNECT ms=<duration_ms> *XX
    ETST:RECONNECT *XX
"""

import logging
import time
from typing import Callable

from .protocol import parse_line, parse_payload

logger = logging.getLogger(__name__)


class DisconnectHandler:
    """Receives disconnect/reconnect protocol messages from firmware.

    Tracks state transitions but does not sleep or block. The test
    runner checks ``active`` to decide whether to suppress output or
    extend timeouts.

    Args:
        on_disconnect: Callback when disconnect is requested. Receives
            the expected duration in seconds.
        on_reconnect: Callback when reconnect is signalled.
        clock: Callable returning monotonic time. Injectable for testing.
    """

    def __init__(
        self,
        on_disconnect: Callable[[float], None] | None = None,
        on_reconnect: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_disconnect = on_disconnect
        self._on_reconnect = on_reconnect
        self._clock = clock

        self._active: bool = False
        self._pending_duration: float = 0.0
        self._disconnect_count: int = 0
        self._disconnect_time: float | None = None

    def feed(self, message: bytes | str) -> None:
        """Feed a message from the device.

        Protocol messages (``ETST:DISCONNECT``, ``ETST:RECONNECT``)
        are consumed. All other messages are ignored.

        Args:
            message: A line of device output.
        """
        line = (
            message.decode("utf-8", errors="replace")
            if isinstance(message, bytes)
            else message
        )

        parsed = parse_line(line.strip())
        if not parsed or parsed.crc_valid is False:
            return

        if parsed.tag == "DISCONNECT":
            payload = parse_payload(parsed.payload_str)
            ms_str = payload.get("ms", "0")
            duration_ms = int(ms_str) if isinstance(ms_str, str) else 0
            duration_s = duration_ms / 1000.0
            logger.info("Disconnect requested: %.1fs", duration_s)
            self._active = True
            self._pending_duration = duration_s
            self._disconnect_time = self._clock()
            if self._on_disconnect is not None:
                self._on_disconnect(duration_s)
            return

        if parsed.tag == "RECONNECT":
            if not self._active:
                logger.debug("RECONNECT received without prior DISCONNECT — ignoring")
                return
            logger.info("Reconnect signalled (cycle %d complete)", self._disconnect_count + 1)
            self._active = False
            self._disconnect_count += 1
            self._disconnect_time = None
            if self._on_reconnect is not None:
                self._on_reconnect()
            return

    @property
    def active(self) -> bool:
        """True while in a disconnect window — output should be suppressed."""
        return self._active

    @property
    def pending_duration(self) -> float:
        """Expected disconnect duration in seconds from the last request."""
        return self._pending_duration

    @property
    def disconnect_count(self) -> int:
        """Number of disconnect/reconnect cycles completed."""
        return self._disconnect_count

    def reset(self) -> None:
        """Reset all state."""
        self._active = False
        self._pending_duration = 0.0
        self._disconnect_count = 0
        self._disconnect_time = None
