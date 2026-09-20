# Smoke tests

`smoke_test.py` builds nothing itself. Build the server first, then run:

    make server
    python3 tests/smoke_test.py --server-bin ./server

Each scenario boots a fresh, isolated server instance (temp working
directory, empty UAF, random-ish free port), drives it over raw TCP, and
asserts specific behavior. Exit code is 0 iff every scenario passed.

These are regression tests for the findings fixed in
docs/superpowers/plans/2026-09-20-critical-high-fixes.md — see
docs/review-findings.md for what each one is guarding against.
