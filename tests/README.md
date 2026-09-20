# Smoke tests

`smoke_test.py` builds nothing itself. Build the server first, then run:

    make server
    python3 tests/smoke_test.py --server-bin ./server

Each scenario boots a fresh, isolated server instance (temp working
directory, empty UAF, port configurable via `--port`), drives it over raw TCP, and
asserts specific behavior. Exit code is 0 iff every scenario passed.

These are regression tests for the findings fixed in
docs/superpowers/plans/2026-09-20-critical-high-fixes.md — see
docs/review-findings.md for what each one is guarding against.

## AddressSanitizer verification (`asan_check.sh`)

`smoke_test.py` alone is not always enough to catch a regression in a
memory-safety fix. Some off-by-one / small stack overflows (for example
the one fixed in `ReadBlock`, `IPCDirect.c`, finding C1) do not reliably
crash a plain, non-instrumented build — the out-of-bounds write can
silently land in stack padding rather than corrupting anything the
process reads afterward, depending on compiler and stack layout. So a
plain-build smoke test can pass even if such a bug were reintroduced.

`tests/asan_check.sh` is the authoritative way to verify these fixes stay
fixed: it builds an AddressSanitizer-instrumented server from the same
source list the Makefile uses, runs the smoke-test harness against it,
and reports any out-of-bounds access directly (rather than waiting for it
to cause visible damage). Run it by hand:

    tests/asan_check.sh

It builds into a temp directory and cleans up after itself, and exits
non-zero if the smoke test fails. This is a manual/opt-in check, not part
of CI — run it after touching any fixed-size buffer handling, or when
reviewing a change that claims to fix a memory-safety finding.
