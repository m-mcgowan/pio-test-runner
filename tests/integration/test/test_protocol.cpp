/**
 * @file test_protocol.cpp
 * @brief Basic tests that exercise the READY/RUN/DONE protocol handshake.
 *
 * These tests are intentionally simple — the value is that they pass
 * through the full protocol lifecycle (READY → RUN_ALL → output → DONE)
 * and the Python runner processes them correctly.
 */

#include <doctest.h>
#include <Arduino.h>
#include <string>

TEST_SUITE("Protocol") {

TEST_CASE("basic arithmetic") {
    CHECK(2 + 2 == 4);
    CHECK(10 - 3 == 7);
    CHECK(6 * 7 == 42);
}

TEST_CASE("string operations") {
    std::string hello = "Hello";
    std::string world = "World";
    CHECK((hello + " " + world) == "Hello World");
    CHECK(hello.size() == 5);
}

TEST_CASE("Arduino millis is running") {
    unsigned long before = millis();
    delay(10);
    unsigned long after = millis();
    CHECK(after > before);
}

}

// Intentionally outside any TEST_SUITE — verifies filter behavior for
// tests with no suite name (m_test_suite is nullptr/empty in doctest).
TEST_CASE("no suite standalone") {
    CHECK(1 + 1 == 2);
}
