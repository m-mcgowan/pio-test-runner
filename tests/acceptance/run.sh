#!/usr/bin/env bash
# Run acceptance tests for pio-test-runner filter validation.
#
# Prerequisites:
#   - Device connected (pass name as argument, e.g. "1.9")
#   - Integration firmware flashed:
#       cd tests/integration && pio test -e esp32s3 --without-testing
#
# Usage:
#   tests/acceptance/run.sh "1.9"             # run all acceptance tests
#   tests/acceptance/run.sh "1.9" -k "unskip" # run subset

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <device-name> [pytest-args...]"
    echo "  e.g.: $0 '1.9'"
    echo "  e.g.: $0 '1.9' -k 'unskip' -v"
    exit 1
fi

DEVICE="$1"
shift

# Resolve port
PORT=$(usb-device port "$DEVICE") || {
    echo "Error: Could not find device '$DEVICE'"
    exit 1
}
echo "Device: $DEVICE → $PORT"

# Set up venv if needed
VENV_DIR="$REPO_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating venv at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

# Install dependencies
"$VENV_DIR/bin/pip" install -q -e "$REPO_DIR[dev]" pyserial

# Ensure integration firmware is flashed and device stays awake after.
# PTR_POST_TEST=restart prevents deep sleep so acceptance tests can connect.
echo "Uploading integration firmware..."
PTR_POST_TEST=restart pio test -e esp32s3 \
    --upload-port "$PORT" --test-port "$PORT" \
    -d "$SCRIPT_DIR/../integration" \
    2>&1 | tail -3
echo ""

# Run acceptance tests
"$VENV_DIR/bin/python" -m pytest "$SCRIPT_DIR" \
    --port "$PORT" \
    --ignore="$SCRIPT_DIR/test_sleep.py" \
    -v "$@"
