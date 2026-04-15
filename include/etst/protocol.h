#pragma once

/// @file protocol.h
/// @brief Shared protocol primitives for embedded-test-runner wire format.
///
/// All protocol lines use the format:
///     ETST:<TAG>[:<SUBTAG>] [payload ...] *XX
///
/// Where *XX is a CRC-8/MAXIM checksum (2 hex chars) of everything
/// before the " *XX" suffix. The CRC lets the host reject garbled
/// lines from multithreaded serial output.
///
/// Payload is tag-specific. Common conventions (shared helpers available):
///   - key=value pairs: free=200000 min=180000
///   - bare flags: verbose
///   - quoted values: name="deep sleep test"
///
/// Use emit() for atomic line output — formats into a stack buffer
/// and writes in a single call to prevent interleaving.

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>

#ifdef ARDUINO
#include <Arduino.h>
#else
// Minimal Print shim for native builds / tests
#include <cstring>
struct Print {
    virtual size_t write(const uint8_t* buf, size_t len) = 0;
    size_t write(const char* buf, size_t len) {
        return write(reinterpret_cast<const uint8_t*>(buf), len);
    }
};
#endif

namespace etst {

/// Maximum protocol line length (before \n).
/// Lines longer than this are truncated — keep payloads concise.
static constexpr size_t MAX_LINE_LEN = 256;

/// Protocol line prefix. All protocol lines start with this.
static constexpr const char* PREFIX = "ETST:";

// -----------------------------------------------------------------
// CRC-8/MAXIM (polynomial 0x31, init 0x00)
// -----------------------------------------------------------------

/// Compute CRC-8/MAXIM over a byte buffer.
inline uint8_t crc8(const uint8_t* data, size_t len) {
    uint8_t crc = 0x00;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            if (crc & 0x80) {
                crc = (crc << 1) ^ 0x31;
            } else {
                crc <<= 1;
            }
        }
    }
    return crc;
}

/// Compute CRC-8/MAXIM over a null-terminated string.
inline uint8_t crc8(const char* str) {
    return crc8(reinterpret_cast<const uint8_t*>(str), strlen(str));
}

// -----------------------------------------------------------------
// Atomic line emission
// -----------------------------------------------------------------

/// Emit a complete protocol line atomically.
///
/// Formats into a stack buffer, computes CRC-8, appends " *XX\n",
/// and writes in a single out.write() call. This prevents
/// interleaving with output from other threads/tasks.
///
/// @param out  Output stream (e.g. Serial)
/// @param fmt  printf format string (should NOT include newline)
/// @param ...  Format arguments
///
/// Example:
///   emit(Serial, "ETST:SLEEP ms=%lu", (unsigned long)3000);
///   // writes: "ETST:SLEEP ms=3000 *A7\n"
inline void emit(Print& out, const char* fmt, ...) {
    char buf[MAX_LINE_LEN + 8];  // room for " *XX\n\0"

    va_list args;
    va_start(args, fmt);
    int n = vsnprintf(buf, MAX_LINE_LEN, fmt, args);
    va_end(args);

    if (n < 0) return;
    if (static_cast<size_t>(n) >= MAX_LINE_LEN) {
        n = MAX_LINE_LEN - 1;  // truncated
    }

    // Compute CRC over the formatted content
    uint8_t checksum = crc8(reinterpret_cast<const uint8_t*>(buf), n);

    // Append " *XX\n"
    n += snprintf(buf + n, 8, " *%02X\n", checksum);

    // Single atomic write
    out.write(reinterpret_cast<const uint8_t*>(buf), n);
}

// -----------------------------------------------------------------
// CRC validation (receive side)
// -----------------------------------------------------------------

/// Result of validating a received line with CRC.
struct ValidatedLine {
    const char* content;   ///< Pointer into buf (null-terminated command)
    uint8_t content_len;   ///< Length of content (excluding CRC suffix)
    bool valid;            ///< true if CRC matched
};

/// Validate CRC on a received line and strip the " *XX" suffix.
///
/// Expects format: ``COMMAND *XX`` where XX is CRC-8/MAXIM hex.
/// Modifies buf in place (null-terminates the command portion).
///
/// @param buf   Mutable buffer containing the received line (trimmed, no newline)
/// @param len   Length of buf
/// @return ValidatedLine with valid=true if CRC matched, valid=false otherwise.
///         On failure, content points to buf (original) for diagnostics.
inline ValidatedLine validate_crc(char* buf, size_t len) {
    ValidatedLine result = { buf, static_cast<uint8_t>(len), false };

    // Need at least " *XX" (4 chars) plus 1 char of content
    if (len < 5) return result;

    // Find " *" suffix — must be at len-4
    if (buf[len - 4] != ' ' || buf[len - 3] != '*') return result;

    // Parse 2-digit hex CRC
    char* end = nullptr;
    unsigned long received = strtoul(&buf[len - 2], &end, 16);
    if (end != &buf[len]) return result;

    // Null-terminate the content (before " *XX")
    size_t content_len = len - 4;
    buf[content_len] = '\0';

    // Compute and compare
    uint8_t expected = crc8(reinterpret_cast<const uint8_t*>(buf), content_len);
    result.content_len = static_cast<uint8_t>(content_len);
    result.valid = (static_cast<uint8_t>(received) == expected);
    return result;
}

/// Log a CRC validation failure via the protocol emit channel.
///
/// @param out  Output stream (e.g. Serial)
/// @param raw  Original received bytes
/// @param len  Length of raw
inline void log_crc_failure(Print& out, const char* raw, size_t len) {
    char hex[96];
    int n = snprintf(hex, sizeof(hex), "[ETST] CRC fail (%zu bytes): ", len);
    for (size_t i = 0; i < len && n < static_cast<int>(sizeof(hex)) - 4; i++) {
        n += snprintf(hex + n, sizeof(hex) - n, "%02X ",
                      static_cast<uint8_t>(raw[i]));
    }
    hex[n] = '\0';
    out.write(reinterpret_cast<const uint8_t*>(hex), n);
    out.write(reinterpret_cast<const uint8_t*>("\n"), 1);
}

// -----------------------------------------------------------------
// Error and warning signaling
// -----------------------------------------------------------------

/// Emit a non-recoverable error. The host treats this as a hard
/// test-run failure and does not retry.
///
/// @param code     Error category: "config", "hardware", "memory", "internal"
/// @param message  Human-readable description
inline void signal_error(Print& out, const char* code, const char* message) {
    emit(out, "ETST:ERROR %s \"%s\"", code, message);
}

/// Emit a warning. Informational — firmware continues.
inline void signal_warn(Print& out, const char* message) {
    emit(out, "ETST:WARN %s", message);
}

}  // namespace etst
