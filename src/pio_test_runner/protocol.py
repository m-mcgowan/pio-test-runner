"""Shared protocol parser for ETST: wire format.

All protocol lines use the format::

    ETST:<TAG>[:<SUBTAG>] [payload ...] *XX

Where ``*XX`` is a CRC-8/MAXIM checksum (2 hex chars) of everything
before the `` *XX`` suffix.

Payload is tag-specific. Common conventions (shared helpers):
  - key=value pairs: ``free=200000 min=180000``
  - bare flags: ``verbose``
  - quoted values: ``name="deep sleep test"``

Usage::

    from pio_test_runner.protocol import parse_line

    parsed = parse_line('ETST:READY *7F')
    if parsed and parsed.crc_valid:
        print(parsed.tag)  # "READY"
"""

import re
from dataclasses import dataclass

# Protocol line prefix
PREFIX = "ETST:"

# CRC-8/MAXIM polynomial
_CRC8_POLY = 0x31
_CRC8_INIT = 0x00

# Match: ETST:<tag> [payload] *XX  (CRC is optional for parsing flexibility)
_LINE_RE = re.compile(
    r"^ETST:(\S+?)(?:\s+(.*?))?\s+\*([0-9A-Fa-f]{2})$"
)

# Fallback: ETST:<tag> [payload]  (no CRC — for testing or legacy)
_LINE_NO_CRC_RE = re.compile(
    r"^ETST:(\S+?)(?:\s+(.*))?$"
)

# Key=value or key="quoted value" or bare flag
_TOKEN_RE = re.compile(
    r'(\w+)="([^"]*)"'   # key="quoted value"
    r"|(\w+)=(\S+)"      # key=value
    r"|(\w+)"             # bare flag
)


def compute_crc8(data: str) -> int:
    """Compute CRC-8/MAXIM over a UTF-8 string.

    Uses polynomial 0x31, init 0x00 — same as Dallas/Maxim 1-Wire.
    Matches the C++ ``crc8()`` in protocol.h.
    """
    crc = _CRC8_INIT
    for byte in data.encode("utf-8"):
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ _CRC8_POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def format_crc(content: str) -> str:
    """Format a protocol line with CRC suffix.

    Args:
        content: The line content without CRC (e.g. "ETST:READY").

    Returns:
        The line with CRC appended (e.g. "ETST:READY *7F").
    """
    return f"{content} *{compute_crc8(content):02X}"


@dataclass
class ParsedTag:
    """Result of parsing a ETST: protocol line.

    Attributes:
        tag: Full tag string (e.g. "READY", "MEM:BEFORE", "CASE:START").
        payload_str: Raw payload string after the tag (may be empty).
        crc_valid: True if CRC matched, False if mismatched, None if no CRC.
        raw: The original line.
    """

    tag: str
    payload_str: str
    crc_valid: bool | None
    raw: str


def parse_line(line: str) -> ParsedTag | None:
    """Parse a ETST: protocol line.

    Returns None if the line doesn't start with ``ETST:``.
    Validates CRC if present. Lines without CRC are accepted
    (``crc_valid=None``) for backward compatibility and testing.

    Args:
        line: A line of device output (trailing newline stripped).

    Returns:
        ParsedTag or None.
    """
    stripped = line.strip()
    if not stripped.startswith(PREFIX):
        return None

    # Try with CRC first
    m = _LINE_RE.match(stripped)
    if m:
        tag = m.group(1)
        payload_str = m.group(2) or ""
        crc_hex = m.group(3)
        # Content for CRC is everything before " *XX"
        content = stripped[: stripped.rfind(f" *{crc_hex}")]
        expected = compute_crc8(content)
        actual = int(crc_hex, 16)
        return ParsedTag(
            tag=tag,
            payload_str=payload_str,
            crc_valid=(expected == actual),
            raw=stripped,
        )

    # Try without CRC
    m = _LINE_NO_CRC_RE.match(stripped)
    if m:
        tag = m.group(1)
        payload_str = m.group(2) or ""
        return ParsedTag(
            tag=tag,
            payload_str=payload_str,
            crc_valid=None,
            raw=stripped,
        )

    return None


# =====================================================================
# Protocol message builders
# =====================================================================
# Use these instead of string literals so the prefix is defined once.


def msg_ready() -> str:
    """Build ETST:READY message (with CRC)."""
    return format_crc(f"{PREFIX}READY")


def msg_done() -> str:
    """Build ETST:DONE message (with CRC)."""
    return format_crc(f"{PREFIX}DONE")


def msg_counts(total: int, skip: int = 0, run: int | None = None) -> str:
    """Build ETST:COUNTS message (with CRC)."""
    if run is None:
        run = total - skip
    return format_crc(f"{PREFIX}COUNTS total={total} skip={skip} run={run}")


def msg_case_start(suite: str, name: str, timeout: int | None = None) -> str:
    """Build ETST:CASE:START message (with CRC)."""
    payload = f'{PREFIX}CASE:START suite="{suite}" name="{name}"'
    if timeout is not None:
        payload += f" timeout={timeout}"
    return format_crc(payload)


def msg_sleep(ms: int) -> str:
    """Build ETST:SLEEP message (with CRC)."""
    return format_crc(f"{PREFIX}SLEEP ms={ms}")


def msg_busy(ms: int) -> str:
    """Build ETST:BUSY message (with CRC)."""
    return format_crc(f"{PREFIX}BUSY ms={ms}")


def msg_restart() -> str:
    """Build ETST:RESTART message (with CRC)."""
    return format_crc(f"{PREFIX}RESTART")


def msg_disconnect(ms: int) -> str:
    """Build ETST:DISCONNECT message (with CRC)."""
    return format_crc(f"{PREFIX}DISCONNECT ms={ms}")


def msg_reconnect() -> str:
    """Build ETST:RECONNECT message (with CRC)."""
    return format_crc(f"{PREFIX}RECONNECT")


def msg_mem_before(free: int, min_free: int, largest: int | None = None) -> str:
    """Build ETST:MEM:BEFORE message (with CRC)."""
    payload = f"{PREFIX}MEM:BEFORE free={free} min={min_free}"
    if largest is not None:
        payload += f" largest={largest}"
    return format_crc(payload)


def msg_mem_after(free: int, delta: int, min_free: int, largest: int | None = None) -> str:
    """Build ETST:MEM:AFTER message (with CRC)."""
    payload = f"{PREFIX}MEM:AFTER free={free} delta={delta:+d} min={min_free}"
    if largest is not None:
        payload += f" largest={largest}"
    return format_crc(payload)


def msg_mem_warn(leaked: int) -> str:
    """Build ETST:MEM:WARN message (with CRC)."""
    return format_crc(f"{PREFIX}MEM:WARN leaked={leaked}")


# =====================================================================
# Payload parsing
# =====================================================================


def parse_payload(payload_str: str) -> dict[str, str | bool]:
    """Parse a payload string into key-value pairs.

    Supports:
      - ``key=value`` — string value
      - ``key="quoted value"`` — string with spaces
      - ``flag`` — bare flag, maps to True

    Args:
        payload_str: The payload portion of a protocol line.

    Returns:
        Dict of parsed key-value pairs.
    """
    result: dict[str, str | bool] = {}
    for m in _TOKEN_RE.finditer(payload_str):
        if m.group(1) is not None:
            # key="quoted value"
            result[m.group(1)] = m.group(2)
        elif m.group(3) is not None:
            # key=value
            result[m.group(3)] = m.group(4)
        elif m.group(5) is not None:
            # bare flag
            result[m.group(5)] = True
    return result
