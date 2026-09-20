#!/bin/bash
# AddressSanitizer verification for memory-safety regression fixes (e.g. C1).
#
# Why this exists: some off-by-one / small stack overflows (like the one
# fixed in ReadBlock, IPCDirect.c) do not reliably crash a plain,
# non-instrumented build -- the overflow can silently land in stack
# padding rather than corrupting anything the process reads afterward,
# depending on compiler and stack layout. AddressSanitizer is the
# authoritative way to verify these fixes stay fixed, because it flags the
# out-of-bounds write itself rather than waiting for it to cause visible
# damage.
#
# This script builds an ASan-instrumented server binary from the exact
# same source list the Makefile uses (OFILES), runs the smoke-test harness
# (tests/smoke_test.py) against it, and cleans up the binary afterward. It
# exits non-zero if the smoke test fails, propagating the failure.
#
# This is a manual/opt-in check, NOT part of CI -- run it by hand after
# touching any code that assembles or copies fixed-size buffers, or when
# reviewing a change that claims to fix a memory-safety finding.
#
# Usage:
#   tests/asan_check.sh [--port PORT]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT="${ASAN_CHECK_PORT:-5950}"

if [[ "${1:-}" == "--port" && -n "${2:-}" ]]; then
    PORT="$2"
fi

cd "$REPO_ROOT"

TMPDIR="$(mktemp -d)"
BIN="$TMPDIR/server_asan"

cleanup() {
    rm -rf "$TMPDIR"
}
trap cleanup EXIT

# Extract the same source file list the Makefile builds the server from
# (OFILES, translated from .o to .c) so this script can never silently
# drift from what actually ships.
SOURCES=$(sed -n '/^OFILES/,/^$/p' Makefile | grep -o '[A-Za-z]*\.o' | sed 's/\.o/.c/')

if [[ -z "$SOURCES" ]]; then
    echo "ERROR: could not extract OFILES source list from Makefile" >&2
    exit 2
fi

echo "Building ASan-instrumented server into $BIN ..."
# shellcheck disable=SC2086
gcc -std=gnu17 -g -w -fsanitize=address -fno-omit-frame-pointer -o "$BIN" $SOURCES

echo "Running smoke tests against ASan build (port $PORT) ..."
status=0
ASAN_OPTIONS=detect_leaks=0 python3 tests/smoke_test.py --server-bin "$BIN" --port "$PORT" || status=$?

exit "$status"
