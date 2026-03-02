"""Shared protocol parser for PTR: wire format.

All protocol lines use the format::

    PTR:<TAG>[:<SUBTAG>] [payload ...] *XX

Where ``*XX`` is a CRC-8/MAXIM checksum (2 hex chars) of everything
before the `` *XX`` suffix.

Payload is tag-specific. Common conventions (shared helpers):
  - key=value pairs: ``free=200000 min=180000``
  - bare flags: ``verbose``
  - quoted values: ``name="deep sleep test"``

Usage::

    from pio_test_runner.protocol import parse_line

    parsed = parse_line('PTR:READY *7F')
    if parsed and parsed.crc_valid:
        print(parsed.tag)  # "READY"
"""

import re
from dataclasses import dataclass

# Protocol line prefix
PREFIX = "PTR:"

# CRC-8/MAXIM polynomial
_CRC8_POLY = 0x31
_CRC8_INIT = 0x00

# Match: PTR:<tag> [payload] *XX  (CRC is optional for parsing flexibility)
_LINE_RE = re.compile(
    r"^PTR:(\S+?)(?:\s+(.*?))?\s+\*([0-9A-Fa-f]{2})$"
)

# Fallback: PTR:<tag> [payload]  (no CRC — for testing or legacy)
_LINE_NO_CRC_RE = re.compile(
    r"^PTR:(\S+?)(?:\s+(.*))?$"
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
        content: The line content without CRC (e.g. "PTR:READY").

    Returns:
        The line with CRC appended (e.g. "PTR:READY *7F").
    """
    return f"{content} *{compute_crc8(content):02X}"


@dataclass
class ParsedTag:
    """Result of parsing a PTR: protocol line.

    Attributes:
        tag: Full tag string (e.g. "READY", "MEM:BEFORE", "TEST:START").
        payload_str: Raw payload string after the tag (may be empty).
        crc_valid: True if CRC matched, False if mismatched, None if no CRC.
        raw: The original line.
    """

    tag: str
    payload_str: str
    crc_valid: bool | None
    raw: str


def parse_line(line: str) -> ParsedTag | None:
    """Parse a PTR: protocol line.

    Returns None if the line doesn't start with ``PTR:``.
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
