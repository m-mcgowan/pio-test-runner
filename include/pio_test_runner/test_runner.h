#pragma once
#include <Arduino.h>
#if defined(ESP_IDF_VERSION)
#include <esp_heap_caps.h>
#include <esp_sleep.h>
#endif
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

/// Report test counts before execution begins.
inline void print_test_count(unsigned total, unsigned skip, unsigned run) {
    emit(Serial, "PTR:TESTS total=%u skip=%u run=%u", total, skip, run);
}

/// Signal that all tests have completed.
inline void signal_done() {
    emit(Serial, "PTR:DONE");
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

/// Signal that the device will be busy (no serial output) for up to @p ms.
/// The host extends its hang timeout accordingly.
inline void signal_busy(uint32_t ms) {
    emit(Serial, "PTR:BUSY ms=%lu", (unsigned long)ms);
}

/// Signal that the device is about to do a software restart.
/// The host handles reconnection the same way as sleep — waits for
/// port drop, reconnects, sends RUN: filter for Phase 2.
///
/// After calling this, the device should call esp_restart().
inline void signal_restart() {
    emit(Serial, "PTR:RESTART");
}

// =====================================================================
// Wake detection
// =====================================================================

/// Check if the device woke from deep sleep.
///
/// Returns true when esp_sleep_get_wakeup_cause() indicates a real
/// wakeup (timer, GPIO, ULP, etc.) — not ESP_SLEEP_WAKEUP_UNDEFINED
/// which indicates a normal boot or software reset.
///
/// Safe to call multiple times per boot — no consumption flag.
/// The runner ensures only the sleeping test runs on the wake boot
/// (via RUN: filter), and uses RESUME_AFTER on a separate cycle for
/// remaining tests where wakeup_cause is naturally UNDEFINED.
///
/// @code
/// TEST_CASE("survives deep sleep") {
///     if (pio_test_runner::is_test_wake()) {
///         // Phase 2: verify post-sleep state
///     } else {
///         // Phase 1: setup, then sleep
///         pio_test_runner::signal_sleep(3000);
///         esp_sleep_enable_timer_wakeup(3000000);
///         esp_deep_sleep_start();
///     }
/// }
/// @endcode
inline bool is_test_wake() {
#if defined(ESP_IDF_VERSION)
    return esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_UNDEFINED;
#else
    return false;
#endif
}

// =====================================================================
// Memory markers
// =====================================================================

/// Print heap stats before a test (parsed by MemoryTracker receiver).
inline void print_mem_before(size_t free_heap, size_t min_heap) {
#if defined(ESP_IDF_VERSION)
    size_t largest = heap_caps_get_largest_free_block(MALLOC_CAP_8BIT);
    emit(Serial, "PTR:MEM:BEFORE free=%zu min=%zu largest=%zu", free_heap, min_heap, largest);
#else
    emit(Serial, "PTR:MEM:BEFORE free=%zu min=%zu", free_heap, min_heap);
#endif
}

/// Print heap stats after a test (parsed by MemoryTracker receiver).
inline void print_mem_after(size_t free_heap, int64_t delta, size_t min_heap) {
#if defined(ESP_IDF_VERSION)
    size_t largest = heap_caps_get_largest_free_block(MALLOC_CAP_8BIT);
    emit(Serial, "PTR:MEM:AFTER free=%zu delta=%+lld min=%zu largest=%zu",
         free_heap, (long long)delta, min_heap, largest);
#else
    emit(Serial, "PTR:MEM:AFTER free=%zu delta=%+lld min=%zu",
         free_heap, (long long)delta, min_heap);
#endif
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
