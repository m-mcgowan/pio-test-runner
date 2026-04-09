"""PlatformIO pre-script: configure embedded-test-runner Python imports.

Add to your platformio.ini extra_scripts:

    extra_scripts = pre:path/to/embedded-test-runner/scripts/pio_test_init.py

This script adds embedded-test-runner and embedded-bridge Python sources to
sys.path so test_custom_runner.py can import them without pip install.
"""

import os
import sys

Import("env")  # noqa: F821 — PIO SCons builtin

# Resolve library root: this script is at <lib_root>/scripts/pio_test_init.py
# In SCons, get the script path from the SConscript node stack.
import SCons.Script
_sconscripts = SCons.Script.call_stack
if _sconscripts:
    _script_path = _sconscripts[-1].sconscript
    _lib_root = os.path.dirname(os.path.dirname(str(_script_path)))
else:
    # Fallback: assume lib root is two levels up from CWD
    _lib_root = os.path.normpath(os.path.join(os.getcwd(), "..", ".."))

_lib_python_src = os.path.join(_lib_root, "src")

# Add embedded-test-runner Python source to sys.path
if os.path.isdir(_lib_python_src) and _lib_python_src not in sys.path:
    sys.path.insert(0, _lib_python_src)

# Find embedded-bridge Python source
_candidates = [
    # Sibling repo (development layout: ~/e/embedded-bridge)
    os.path.join(_lib_root, "..", "embedded-bridge", "python", "src"),
]

# Scan libdeps for embedded-bridge
_libdeps = env.subst("$PROJECT_LIBDEPS_DIR/$PIOENV")
if os.path.isdir(_libdeps):
    for name in os.listdir(_libdeps):
        if "embedded-bridge" in name.lower():
            _candidates.append(os.path.join(_libdeps, name, "python", "src"))
            _candidates.append(os.path.join(_libdeps, name, "src"))

for candidate in _candidates:
    candidate = os.path.normpath(candidate)
    if os.path.isdir(os.path.join(candidate, "embedded_bridge")):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
        break
