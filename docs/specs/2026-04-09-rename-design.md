# Rename: pio-test-runner → embedded-test-runner / etst

## Overview

Rename the project from its PlatformIO-specific name to a general embedded
test framework identity. The repo becomes `embedded-test-runner`, the code
API uses `etst` for brevity.

## Naming

| What | Old | New |
|------|-----|-----|
| GitHub repo | `pio-test-runner` | `embedded-test-runner` |
| Python package | `pio_test_runner` | `etst` |
| C++ include path | `<pio_test_runner/...>` | `<etst/...>` |
| C++ namespace (protocol) | `pio_test_runner` | `etst` |
| C++ namespace (doctest) | `ptr_doctest` | `etst::doctest` |
| library.json name | `pio-test-runner` | `embedded-test-runner` |
| Class: listener | `PtrTestListener` | `EtstDoctestListener` |
| Variable: wake flag | `_ptr_is_wake_cycle` | `_etst_is_wake_cycle` |
| Weak hooks | `ptr_board_init` etc. | Removed — config struct only |

## No Backward Compatibility

Clean break on `main`. All consumers are under our control and will stay on
the `releases/0.2.x` branch until they migrate. No symlinks, no namespace
aliases, no deprecated forwarding headers.

## Prerequisite: Release Branch

Before renaming, create a release branch and worktree so consumers aren't
broken:

```bash
git branch releases/0.2.x v0.2.0
git worktree add ../pio-test-runner-0.2.x releases/0.2.x
```

Update `firmware2/shared/platformio/local.ini` to symlink to the worktree:
```ini
pio-test-runner = symlink:///Users/mat/e/pio-test-runner-0.2.x
```

Update `platform_base.ini` to pin the release branch:
```ini
pio-test-runner = https://github.com/m-mcgowan/pio-test-runner.git#releases/0.2.x
```

## C++ Header Layout

```
include/etst/
  ├─ protocol.h              CRC, emit(), wire format (namespace etst)
  ├─ test_runner.h           lifecycle signals, phase detection (namespace etst)
  ├─ doctest/
  │   └─ runner.h            listener, filters, run_tests(), idle_loop()
  │                           (namespace etst::doctest)
  └─ (no pio_test_runner/ — clean break)
```

## C++ Namespace Structure

```cpp
namespace etst {
    // Framework-agnostic protocol and lifecycle
    void signal_ready();
    void signal_done();
    void signal_sleep(uint32_t ms);
    void signal_busy(uint32_t ms);
    void signal_restart();
    bool is_test_wake();        // renamed to is_continuation() in multi-phase spec
    void clear_test_wake();
    void print_test_count(...);
    void print_test_start(...);
    void print_mem_before(...);
    void print_mem_after(...);

    namespace doctest {
        // Doctest-specific adapter
        struct EtstDoctestListener : ::doctest::IReporter { ... };
        struct Config { ... };
        inline Config config;
        void run_tests();
        void idle_loop();
    }
}
```

## Config Structure

Three layers — framework-agnostic, framework-specific, platform defaults:

```cpp
namespace etst {
    struct Config {
        // User-provided board setup. Default: nullptr (no-op).
        bool (*board_init)(Print& log) = nullptr;

        // Called after each test cycle. Default: nullptr (no-op).
        void (*after_cycle)() = nullptr;

        // How long to wait for host before standalone mode.
        uint32_t ready_timeout_ms = 0;  // 0 = forever
    };
    inline Config config;

    namespace doctest {
        struct Config {
            // Doctest-specific context configuration.
            void (*configure)(::doctest::Context& ctx) = nullptr;
        };
        inline Config config;
    }
}
```

Platform defaults (restart, sleep, lightsleep) are named functions the user
can call or replace:

```cpp
namespace etst::platform::esp32 {
    void restart();      // calls esp_restart()
    void sleep();        // calls esp_deep_sleep_start()
    void lightsleep();   // calls esp_light_sleep_start()
    bool default_init(Print& log);  // no-op, returns true
}
```

The active platform implementation is selected at compile time. The user can
wrap defaults:

```cpp
bool my_board_init(Print& log) {
    etst::platform::esp32::default_init(log);  // library default
    mount_filesystem();
    return detect_board_revision(log);
}
etst::config.board_init = my_board_init;
```

## Python Package

```
etst/
  ├─ __init__.py
  ├─ protocol.py             wire format, message builders, PREFIX
  ├─ ready_run_protocol.py   state machine
  ├─ runner.py               EmbeddedTestRunner (PIO integration)
  ├─ disconnect.py           disconnect handler
  ├─ timing_tracker.py       per-test duration
  ├─ result_receiver.py      framework output parsing
  ├─ robust_doctest_parser.py
  └─ serial_port.py
```

Imports change: `from etst.runner import EmbeddedTestRunner`

The `test_custom_runner.py` shim updates its import path.

## pyproject.toml

```toml
[project]
name = "embedded-test-runner"
# ... 
packages = ["etst"]
```

## Consumer Migration

Consumers (firmware2/simple_publish, provisioner) stay on `releases/0.2.x`
until ready. Migration checklist when they switch to main:

1. Update `lib_deps` URL to `embedded-test-runner`
2. Change `#include <pio_test_runner/...>` → `#include <etst/...>`
3. Change `ptr_doctest::` → `etst::doctest::`
4. Change `pio_test_runner::` → `etst::`
5. Remove weak function definitions, use config struct
6. Copy updated `test_custom_runner.py`

## Scope

This spec covers the mechanical rename only:
- File/directory renames
- Find-replace across all source, tests, docs
- GitHub repo rename
- pyproject.toml / library.json updates
- Release branch creation

`default_main.cpp` is renamed to `etst/doctest/default_main.cpp` and updated
to use the new namespaces. It remains excluded from the library build
(header-only mode) — the entry point spec will address making it automatic.

It does NOT cover:
- Multi-phase generalization (`is_continuation()`) — separate spec
- Entry point / default_main.cpp — separate spec
- Embedded-bridge decoupling — separate spec
- Platform namespace implementation — separate spec (structure defined here
  as target, implementation details deferred)
