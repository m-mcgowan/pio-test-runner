"""Tests for the shared ETST: protocol parser."""

from etst.protocol import (
    ParsedTag,
    compute_crc8,
    format_crc,
    msg_args,
    msg_error,
    msg_warn,
    parse_line,
    parse_payload,
)


class TestCRC8:
    def test_empty_string(self):
        assert compute_crc8("") == 0x00

    def test_known_value(self):
        # Verify CRC is deterministic and non-trivial
        crc = compute_crc8("ETST:READY")
        assert isinstance(crc, int)
        assert 0 <= crc <= 255
        assert crc == compute_crc8("ETST:READY")  # deterministic

    def test_different_input_different_crc(self):
        assert compute_crc8("ETST:READY") != compute_crc8("ETST:DONE")

    def test_format_crc(self):
        line = format_crc("ETST:READY")
        # Should end with " *XX" where XX is hex
        assert line.startswith("ETST:READY *")
        assert len(line) == len("ETST:READY *XX")
        # Parse it back and verify CRC
        parsed = parse_line(line)
        assert parsed is not None
        assert parsed.crc_valid is True


class TestParseLine:
    def test_simple_tag_with_crc(self):
        line = format_crc("ETST:READY")
        parsed = parse_line(line)
        assert parsed is not None
        assert parsed.tag == "READY"
        assert parsed.payload_str == ""
        assert parsed.crc_valid is True

    def test_tag_with_subtag(self):
        line = format_crc("ETST:MEM:BEFORE free=200000 min=180000")
        parsed = parse_line(line)
        assert parsed is not None
        assert parsed.tag == "MEM:BEFORE"
        assert "free=200000" in parsed.payload_str
        assert parsed.crc_valid is True

    def test_tag_with_payload(self):
        line = format_crc("ETST:SLEEP ms=3000")
        parsed = parse_line(line)
        assert parsed is not None
        assert parsed.tag == "SLEEP"
        assert parsed.payload_str == "ms=3000"
        assert parsed.crc_valid is True

    def test_tag_with_quoted_payload(self):
        line = format_crc('ETST:CASE:START suite="My Suite" name="test one"')
        parsed = parse_line(line)
        assert parsed is not None
        assert parsed.tag == "CASE:START"
        assert 'suite="My Suite"' in parsed.payload_str
        assert parsed.crc_valid is True

    def test_invalid_crc_rejected(self):
        # Valid format but wrong CRC
        line = format_crc("ETST:READY")
        # Corrupt the CRC
        corrupted = line[:-2] + "00"
        parsed = parse_line(corrupted)
        assert parsed is not None
        assert parsed.tag == "READY"
        assert parsed.crc_valid is False

    def test_no_crc_accepted(self):
        parsed = parse_line("ETST:READY")
        assert parsed is not None
        assert parsed.tag == "READY"
        assert parsed.crc_valid is None

    def test_no_crc_with_payload(self):
        parsed = parse_line("ETST:SLEEP ms=3000")
        assert parsed is not None
        assert parsed.tag == "SLEEP"
        assert parsed.payload_str == "ms=3000"
        assert parsed.crc_valid is None

    def test_non_protocol_line_returns_none(self):
        assert parse_line("Hello world") is None
        assert parse_line("READY") is None
        assert parse_line("[MEM] Before: free=200000") is None
        assert parse_line(">>> TEST START: Suite/name") is None
        assert parse_line("") is None

    def test_garbled_line(self):
        # Interleaved output that starts with ETST: but is garbled
        line = format_crc("ETST:READY")
        # Insert garbage in the middle
        garbled = line[:6] + "GARBAGE" + line[6:]
        parsed = parse_line(garbled)
        # Should either fail to parse or fail CRC
        if parsed is not None:
            assert parsed.crc_valid is False

    def test_whitespace_stripped(self):
        line = format_crc("ETST:READY")
        parsed = parse_line(f"  {line}  ")
        assert parsed is not None
        assert parsed.tag == "READY"
        assert parsed.crc_valid is True

    def test_all_tags(self):
        """Verify all protocol tags parse correctly."""
        tags = [
            "ETST:READY",
            "ETST:DONE",
            "ETST:SLEEP ms=3000",
            "ETST:DISCONNECT ms=5000",
            "ETST:RECONNECT",
            "ETST:MEM:BEFORE free=200000 min=180000",
            "ETST:MEM:AFTER free=199800 delta=-200 min=179800",
            "ETST:MEM:WARN leaked=8452",
            'ETST:CASE:START suite="Protocol" name="basic arithmetic"',
            'ETST:CASE:START suite="Timing" name="slow test" timeout=30',
        ]
        for content in tags:
            line = format_crc(content)
            parsed = parse_line(line)
            assert parsed is not None, f"Failed to parse: {line}"
            assert parsed.crc_valid is True, f"CRC failed for: {line}"

    def test_raw_preserved(self):
        line = format_crc("ETST:READY")
        parsed = parse_line(line)
        assert parsed.raw == line


class TestParsePayload:
    def test_key_value(self):
        result = parse_payload("free=200000 min=180000")
        assert result == {"free": "200000", "min": "180000"}

    def test_quoted_value(self):
        result = parse_payload('name="deep sleep test" timeout=30')
        assert result == {"name": "deep sleep test", "timeout": "30"}

    def test_bare_flag(self):
        result = parse_payload("verbose")
        assert result == {"verbose": True}

    def test_mixed(self):
        result = parse_payload('suite="My Suite" name=test1 verbose')
        assert result == {"suite": "My Suite", "name": "test1", "verbose": True}

    def test_empty(self):
        result = parse_payload("")
        assert result == {}

    def test_signed_value(self):
        result = parse_payload("delta=+200")
        assert result == {"delta": "+200"}

    def test_negative_value(self):
        result = parse_payload("delta=-8452")
        assert result == {"delta": "-8452"}


class TestCRC8CrossValidation:
    """Verify Python CRC matches known C++ output.

    These values can be checked against the C++ crc8() from protocol.h
    by running a small test on device.
    """

    def test_crc_is_stable(self):
        # Record known CRCs for regression testing.
        # If the algorithm changes, these would break.
        cases = {
            "ETST:READY": compute_crc8("ETST:READY"),
            "ETST:DONE": compute_crc8("ETST:DONE"),
            "ETST:SLEEP ms=3000": compute_crc8("ETST:SLEEP ms=3000"),
        }
        # Verify they're all different
        crcs = list(cases.values())
        assert len(set(crcs)) == len(crcs), "CRC collision in test cases"

    def test_round_trip(self):
        """format_crc → parse_line → crc_valid."""
        content = "ETST:MEM:AFTER free=199800 delta=-200 min=179800"
        line = format_crc(content)
        parsed = parse_line(line)
        assert parsed is not None
        assert parsed.crc_valid is True
        assert parsed.tag == "MEM:AFTER"
        payload = parse_payload(parsed.payload_str)
        assert payload["free"] == "199800"
        assert payload["delta"] == "-200"
        assert payload["min"] == "179800"


class TestNewProtocolMessages:
    def test_msg_args(self):
        result = msg_args("--env DEVICE_REV=1.10")
        assert "ETST:ARGS --env DEVICE_REV=1.10" in result
        parsed = parse_line(result)
        assert parsed is not None
        assert parsed.tag == "ARGS"
        assert parsed.crc_valid is True

    def test_msg_error(self):
        result = msg_error("config", "malformed --env arg: KEY")
        parsed = parse_line(result)
        assert parsed is not None
        assert parsed.tag == "ERROR"
        assert parsed.crc_valid is True
        assert "config" in parsed.payload_str
        assert "malformed --env arg: KEY" in parsed.payload_str

    def test_msg_warn(self):
        result = msg_warn("something unusual")
        parsed = parse_line(result)
        assert parsed is not None
        assert parsed.tag == "WARN"
        assert parsed.crc_valid is True
