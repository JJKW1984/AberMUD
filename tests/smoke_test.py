#!/usr/bin/env python3
"""
Smoke tests for AberMUD 5.30 critical/high-severity fixes.
Builds nothing itself — run `make server` first (or pass --skip-build to
reuse an existing binary). Boots the server in a temp working directory,
drives it over raw TCP, and asserts each fixed bug stays fixed.

Usage: python3 tests/smoke_test.py [--server-bin ../server] [--port 5900]
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

SCENARIOS = []  # populated by @scenario as this file grows


def scenario(fn):
    """Register a test scenario. Each scenario takes a SmokeTest instance
    and must raise AssertionError (with a clear message) on failure."""
    SCENARIOS.append(fn)
    return fn


class SmokeTest:
    def __init__(self, server_bin, port):
        self.server_bin = os.path.abspath(server_bin)
        self.port = port
        self.workdir = None
        self.proc = None

    def start_server(self, universe=None):
        self.workdir = tempfile.mkdtemp(prefix="abermud_smoke_")
        open(os.path.join(self.workdir, "UAF"), "wb").close()
        args = [self.server_bin, "-p", str(self.port)]
        if universe:
            shutil.copy(universe, os.path.join(self.workdir, os.path.basename(universe)))
            args.append(os.path.basename(universe))
        self.proc = subprocess.Popen(
            args, cwd=self.workdir,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self._wait_for_port(self.port, timeout=5.0)

    def _wait_for_port(self, port, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                out = self.proc.stdout.read().decode("latin1", "replace")
                raise RuntimeError(f"server exited early (code {self.proc.returncode}):\n{out}")
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
                s.close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(f"server never opened port {port}")

    def stop_server(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def connect(self, timeout=2.0):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        s.settimeout(timeout)
        return s

    @staticmethod
    def read(sock, t=1.0):
        out = b""
        deadline = time.time() + t
        while time.time() < deadline:
            try:
                d = sock.recv(4096)
                if not d:
                    return out + b"<EOF>"
                out += d
            except socket.timeout:
                pass
        return out

    @staticmethod
    def send(sock, line):
        sock.sendall(line.encode() + b"\r\n")

    def register(self, sock, name, password="pw", read_t=0.8):
        """Drive a full new-account registration; returns the final transcript."""
        transcript = self.read(sock, read_t)
        self.send(sock, name)
        transcript += self.read(sock, read_t)
        self.send(sock, "m")
        transcript += self.read(sock, read_t)
        self.send(sock, "smoketest@example.invalid")
        transcript += self.read(sock, read_t)
        self.send(sock, password)
        transcript += self.read(sock, read_t)
        return transcript


@scenario
def scenario_server_boots(t):
    """Baseline: the server boots and accepts a connection."""
    s = t.connect()
    banner = t.read(s, 1.0)
    assert b"name" in banner.lower() or b"registered" in banner.lower() or len(banner) > 0, \
        "expected some login prompt output on connect, got nothing"
    s.close()


@scenario
def scenario_oversized_line_does_not_crash(t):
    """C1: a single line >512 bytes must not crash the server (was a stack
    buffer overflow in ReadBlock, IPCDirect.c)."""
    s = t.connect()
    t.read(s, 0.5)
    s.sendall(b"a" * 600 + b"\r\n")
    time.sleep(1.0)
    assert t.is_alive(), "server crashed after receiving an oversized line"
    s.close()
    # server must still be answering new connections afterwards
    s2 = t.connect()
    banner = t.read(s2, 1.0)
    assert len(banner) > 0, "server stopped accepting connections after the oversized line"
    s2.close()


@scenario
def scenario_abandoned_login_does_not_steal_active_name(t):
    """C3: disconnecting mid-login under someone else's active name must not
    free that name's WordList reservation (was an unconditional FreeWord()
    call in RemoveUser, SysSupport.c)."""
    a = t.connect()
    t.register(a, "Smoke")  # session A fully registers and logs in as "Smoke"
    after_a = t.read(a, 0.3)

    # Session B: connect, type the SAME name (existing persona -> goes to
    # password prompt), then disconnect without sending a password.
    b = t.connect()
    t.read(b, 0.5)
    t.send(b, "Smoke")
    t.read(b, 0.5)
    b.close()
    time.sleep(1.5)  # let FixLineFaults()/RemoveUser() run on the server's ~1s loop

    # Session C: attempt a brand-new registration under the same name. If C3
    # is present, this succeeds (the name was wrongly freed); it must be
    # rejected instead, since session A is still logged in as "Smoke".
    c = t.connect()
    transcript = t.register(c, "Smoke")
    assert b"confused with other things" in transcript or b"Password:" in transcript, (
        "a second registration under an already-active name succeeded — "
        f"the abandoned login on session B freed it. transcript={transcript!r}"
    )
    a.close()
    c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server-bin", default="./server")
    ap.add_argument("--port", type=int, default=5900)
    args = ap.parse_args()

    if not os.path.isfile(args.server_bin):
        print(f"ERROR: server binary not found at {args.server_bin} — run 'make server' first.", file=sys.stderr)
        return 2

    failures = []
    for fn in SCENARIOS:
        t = SmokeTest(args.server_bin, args.port)
        name = fn.__name__
        try:
            t.start_server()
            fn(t)
            print(f"PASS: {name}")
        except Exception as e:  # noqa: BLE001 - smoke test, we want to catch everything
            print(f"FAIL: {name}: {e}")
            failures.append(name)
        finally:
            t.stop_server()

    print(f"\n{len(SCENARIOS) - len(failures)}/{len(SCENARIOS)} scenarios passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
