#pragma once
#include <Arduino.h>
#include "pio_test_runner/protocol.h"

/// @brief PlatformIO test runner protocol — firmware-side API.
///
/// All protocol lines use the PTR: prefix with CRC-8 checksum.
/// See protocol.h for wire format details.
///
/// @code
/// #include <pio_test_runner/test_runner.h>
/// @endcode

namespace pio_test_runner {

// =====================================================================
// Disconnect protocol
// =====================================================================

/// Tell the host we're going away for @p duration_ms milliseconds.
/// Call this BEFORE Serial.end() / deep sleep / reset.
inline void request_disconnect(uint32_t duration_ms) {
    emit(Serial, "PTR:DISCONNECT ms=%lu", (unsigned long)duration_ms);
}

/// Tell the host we're back. Call this AFTER Serial.begin().
inline void signal_reconnect() {
    emit(Serial, "PTR:RECONNECT");
}

// =====================================================================
// Ready/Run/Done protocol
// =====================================================================

/// Signal that the device is ready to receive test commands.
/// The host will respond with RUN_ALL or RUN:<filter>.
inline void signal_ready() {
    emit(Serial, "PTR:READY");
}

/// Signal that all tests have completed.
inline void signal_done() {
    emit(Serial, "PTR:DONE");
}

/// Wait for a test command from the host.
///
/// Blocks until a non-empty line is received or timeout expires.
/// Returns empty String on timeout (no runner present — backward compat).
///
/// @param timeout_ms  Maximum time to wait (0 = wait forever).
/// @return Command string: "RUN_ALL", "RUN: <filter>", or "" on timeout.
inline String wait_for_command(uint32_t timeout_ms = 5000) {
    uint32_t start = millis();
    String line;
    while (timeout_ms == 0 || millis() - start < timeout_ms) {
        if (Serial.available()) {
            line = Serial.readStringUntil('\n');
            line.trim();
            if (line.length() > 0) return line;
        }
        delay(10);
    }
    return "";  // timeout — no runner present
}

// =====================================================================
// Sleep signalling
// =====================================================================

/// Signal that the device is entering deep sleep for @p duration_ms.
/// The host will wait this long plus padding before reconnecting.
///
/// After calling this, the device should enter deep sleep. The host
/// uses the sleeping test name to build a filter for the wake cycle.
inline void signal_sleep(uint32_t duration_ms) {
    emit(Serial, "PTR:SLEEP ms=%lu", (unsigned long)duration_ms);
}

// =====================================================================
// Memory markers
// =====================================================================

/// Print heap stats before a test (parsed by MemoryTracker receiver).
inline void print_mem_before(size_t free_heap, size_t min_heap) {
    emit(Serial, "PTR:MEM:BEFORE free=%zu min=%zu", free_heap, min_heap);
}

/// Print heap stats after a test (parsed by MemoryTracker receiver).
inline void print_mem_after(size_t free_heap, int64_t delta, size_t min_heap) {
    emit(Serial, "PTR:MEM:AFTER free=%zu delta=%+lld min=%zu",
         free_heap, (long long)delta, min_heap);
}

/// Print a memory leak warning.
inline void print_mem_warning(int64_t leaked_bytes) {
    emit(Serial, "PTR:MEM:WARN leaked=%lld", (long long)leaked_bytes);
}

// =====================================================================
// Test start markers
// =====================================================================

/// Print a test start marker (parsed by TestTimingTracker receiver).
inline void print_test_start(const char* suite, const char* name) {
    emit(Serial, "PTR:TEST:START suite=\"%s\" name=\"%s\"", suite, name);
}

/// Print a test start marker with timeout annotation.
inline void print_test_start(const char* suite, const char* name, float timeout_s) {
    emit(Serial, "PTR:TEST:START suite=\"%s\" name=\"%s\" timeout=%.0f",
         suite, name, timeout_s);
}

}  // namespace pio_test_runner
