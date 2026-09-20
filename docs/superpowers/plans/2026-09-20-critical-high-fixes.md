# Critical/High Findings Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all Critical and High findings from the review (C1–C4, H1–H3) in [docs/review-findings.md](../../review-findings.md), with a working build and a checked-in smoke-test harness that proves each fix.

**Architecture:** Each task fixes one finding (or the shared build prerequisite), in the smallest safe diff that closes the finding as scoped. Every behavioral fix (C1, C2, C3, H1) is proven by extending one shared Python smoke-test script (`tests/smoke_test.py`) that builds and drives the live `server` binary over raw TCP — the same style of harness used to originally discover/reproduce these bugs. H2 gets a targeted safety-net fix (no black-box network test; verified by direct build/inspection, see Task 7). All work happens on a new branch off `master`; nothing is pushed or merged without the user's separate say-so.

**Tech Stack:** ANSI C (gcc), GNU Make, Python 3 (smoke tests only, stdlib `socket`/`subprocess`, no dependencies).

**Spec:** [docs/review-findings.md](../../review-findings.md) — findings C1, C2, C3, C4, H1, H2, H3. Also consult [docs/build-and-run.md](../../build-and-run.md) (build fix was already validated once in a scratch copy during the review) and [docs/persistence.md](../../persistence.md) / [docs/command-processing.md](../../command-processing.md) for background on the UAF format and the wizard-privilege model touched by C2/H1.

## Global Constraints

- Do not touch anything not required by C1–C4/H1–H3. No unrelated refactors, no style-only changes.
- Scope decisions already made with the user (do not revisit without asking again):
  - **H2**: safety-net validation only (reject unsafe compiles, log unsafe loads). No bytecode-format redesign.
  - **H1**: fix the truncated password comparison only. Passwords stay plaintext in `UAF` for now; hashing is an explicit follow-up, not part of this patch.
  - **H3**: a single checked-in smoke-test script. No CI workflow in this patch.
- All new/changed C code must build cleanly with the same `CC` this plan establishes in Task 1 (`gcc -Wall -pedantic -std=gnu17`), with no new warnings introduced by these changes.
- Preserve existing K&R-style function definitions/signatures elsewhere in the codebase — do not modernize code this plan doesn't need to touch.
- Every task's fix must be demonstrated by the smoke-test script actually catching the *old* (broken) behavior before the fix and passing after — i.e. write the test scenario, confirm it fails against the pre-fix code, then fix, then confirm it passes. This is this codebase's substitute for unit-level TDD since none exists yet (see H3).

---

## Task 1: Fix the build (C4) and create the working branch

**Files:**
- Modify: `Makefile:32` (the `CC` line)
- Modify: `SaveLoad.c:44`, `LibSocket.c:31`, `Socket.c` (add `#include <errno.h>`), `UserFile.c:121`

**Interfaces:**
- Produces: a `server` binary that builds cleanly with `make server`, which every later task's smoke-test steps depend on.

- [ ] **Step 1: Create the working branch**

```bash
git checkout -b fix/critical-high-findings
```

- [ ] **Step 2: Replace the four bad `errno` declarations with `#include <errno.h>`**

In `SaveLoad.c`, `LibSocket.c`, and `UserFile.c`, remove the local `extern int errno;` declaration and add `#include <errno.h>` near the top of the file's include block (alongside the existing `#include` lines). `Socket.c` already includes `<errno.h>` but also declares `extern int errno;` right after — same removal there.

Exact edits:

`SaveLoad.c:44` — change
```c
extern int errno;
```
to (remove the line; add once near the top `#include` block, e.g. after `#include "System.h"`):
```c
#include <errno.h>
```

`LibSocket.c:31` — same pattern: remove `extern int errno;`, add `#include <errno.h>` near the top of the file (it already includes `<errno.h>` at the top in some builds — check first; if the include is already present, just delete the stray `extern int errno;` line and add nothing).

`Socket.c` — it already has `#include <errno.h>` near the top; delete the redundant `extern int errno;` declaration that follows it.

`UserFile.c:121` — this one is a local `extern int errno;` *inside* `OpenUAF()`, not file-scope. Delete that line and add `#include <errno.h>` to `UserFile.c`'s top include block (it does not currently include it at all).

- [ ] **Step 3: Relax the compiler standard to unblock the remaining prototype/arity conflicts**

Edit `Makefile:32`:
```makefile
CC	= gcc -Wall -pedantic
```
to:
```makefile
CC	= gcc -Wall -pedantic -std=gnu17
```

This resolves the `Main.c` signal-handler prototype conflicts and the `SendItem()` call-arity mismatches (K&R-style declarations across the codebase) without touching any of those call sites — validated during the original review (see [docs/build-and-run.md](../../build-and-run.md) §3–4). A full ANSI-prototype conversion of every K&R-style declaration is out of scope for this patch.

- [ ] **Step 4: Build and confirm**

```bash
make clean
make server FindPW Run_Aber Reg
echo "build exit: $?"
```

Expected: exit code `0`, and `server`/`FindPW`/`Run_Aber`/`Reg` all present and executable. (`make docs`/`make all` will still fail — that's [finding L2](../../review-findings.md), out of scope for this patch; do not attempt to fix it here.)

- [ ] **Step 5: Commit**

```bash
git add Makefile SaveLoad.c LibSocket.c Socket.c UserFile.c
git commit -m "build: fix errno declarations and relax C standard so the project builds on modern GCC (C4)"
```

---

## Task 2: Smoke-test harness skeleton (H3, baseline scenario)

**Files:**
- Create: `tests/smoke_test.py`
- Create: `tests/README.md`

**Interfaces:**
- Produces: `tests/smoke_test.py` with a `SmokeTest` scaffold — a `start_server()`/`stop_server()` pair, a `connect()` helper, a `run_all()` entry point, and a `register_scenario` list every later task appends one function to. This is what Tasks 3–6 each add a scenario to.

- [ ] **Step 1: Write the harness skeleton**

Create `tests/smoke_test.py`:

```python
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
```

- [ ] **Step 2: Write the test README**

Create `tests/README.md`:

```markdown
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
```

- [ ] **Step 3: Run it against the Task 1 build and confirm the baseline passes**

```bash
make server
python3 tests/smoke_test.py --server-bin ./server
```

Expected: `PASS: scenario_server_boots`, `1/1 scenarios passed`, exit code `0`.

- [ ] **Step 4: Commit**

```bash
git add tests/smoke_test.py tests/README.md
git commit -m "test: add smoke-test harness skeleton with a baseline boot scenario (H3)"
```

---

## Task 3: Fix C1 — stack buffer overflow in `ReadBlock`

**Files:**
- Modify: `IPCDirect.c:184-206` (`ReadBlock`)
- Modify: `tests/smoke_test.py` (add `scenario_oversized_line_does_not_crash`)

**Interfaces:**
- Consumes: `SmokeTest.connect()`/`.send()`/`.read()`/`.is_alive()` from Task 2.

- [ ] **Step 1: Add the failing scenario first**

Append to `tests/smoke_test.py`, above `def main():`:

```python
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
```

- [ ] **Step 2: Run it against the current (unfixed) code and confirm it fails**

```bash
python3 tests/smoke_test.py --server-bin ./server
```

Expected: `FAIL: scenario_oversized_line_does_not_crash: server crashed after receiving an oversized line` (or similar — the process should be observed to have died). This confirms the test actually detects the bug.

- [ ] **Step 3: Fix `ReadBlock`**

In `IPCDirect.c`, inside `ReadBlock()`, change:

```c
		if(c!='\r')
		{
			UserInput[u][InputPos[u]++]=c;
			if(InputPos[u]==513||c=='\n')
			{
```

to:

```c
		if(c!='\r')
		{
			UserInput[u][InputPos[u]++]=c;
			if(InputPos[u]==511||c=='\n')
			{
```

(`511` caps the assembled string at 510 characters, matching the 510-byte margin already used elsewhere in this file for output chunking — e.g. `BufOut`'s `while(strlen(y)>510)` — leaving headroom instead of exactly matching the destination size.)

Then, a few lines further down in the same block, change the unbounded copy:

```c
				strcpy(pkt->pa_Data,UserInput[u]);
```

to a bounded copy that can never overflow `pkt->pa_Data` regardless of how the threshold above is tuned later (defense in depth — this is the actual line ASan flagged):

```c
				strncpy(pkt->pa_Data,UserInput[u],sizeof(pkt->pa_Data)-1);
				pkt->pa_Data[sizeof(pkt->pa_Data)-1]=0;
```

- [ ] **Step 4: Rebuild and re-run the scenario, confirm it now passes**

```bash
make server
python3 tests/smoke_test.py --server-bin ./server
```

Expected: `PASS: scenario_oversized_line_does_not_crash`, `2/2 scenarios passed`.

- [ ] **Step 5 (recommended verification): re-run under AddressSanitizer**

```bash
gcc -std=gnu17 -g -w -fsanitize=address -fno-omit-frame-pointer -o server_asan $(sed -n '/^OFILES/,/^$/p' Makefile | grep -o '[A-Za-z]*\.o' | sed 's/\.o/.c/')
ASAN_OPTIONS=detect_leaks=0 python3 tests/smoke_test.py --server-bin ./server_asan
rm -f server_asan
```

Expected: no ASan report, `2/2 scenarios passed`. (This step is a one-off manual check, not part of the committed harness — the plain `server` binary from Task 1 is what CI/operators actually run.)

- [ ] **Step 6: Commit**

```bash
git add IPCDirect.c tests/smoke_test.py
git commit -m "fix: bound ReadBlock's line assembly and packet copy to stop a remote stack overflow (C1)"
```

---

## Task 4: Fix C3 — abandoned login frees another player's active name

**Files:**
- Modify: `User.h` (add `UF_NAMEWORD` flag constant, reusing the existing but currently-dead `us_Flags` field)
- Modify: `ComDriver.c` (set the flag at both `AddWord(...,WD_NOUN)` call sites)
- Modify: `SysSupport.c` (`RemoveUser`, `ExitUser` — guard the `FreeWord` calls on the flag)
- Modify: `tests/smoke_test.py` (add `scenario_abandoned_login_does_not_steal_active_name`)

**Interfaces:**
- Produces: `UF_NAMEWORD` (bit flag on `USER.us_Flags`) — consumed by `RemoveUser`/`ExitUser`.

- [ ] **Step 1: Add the failing scenario first**

Append to `tests/smoke_test.py`:

```python
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
```

- [ ] **Step 2: Run it against the current (unfixed) code and confirm it fails**

```bash
python3 tests/smoke_test.py --server-bin ./server
```

Expected: `FAIL: scenario_abandoned_login_does_not_steal_active_name: ...`.

- [ ] **Step 3: Add the flag constant**

In `User.h`, near the `AWAIT_*` constants, add:

```c
#define UF_NAMEWORD	1	/* us_Flags bit: this session's name is registered
				   in WordList (AddWord was called for it) — only
				   clear it via FreeWord if this bit is set, see
				   RemoveUser()/ExitUser() in SysSupport.c */
```

- [ ] **Step 4: Set the flag at both `AddWord(...,WD_NOUN)` call sites in `ComDriver.c`**

In `Check_Password()`, right after:
```c
	AddWord(UserList[u].us_Name,(short)(10000+u),WD_NOUN);	/* Add name word */
```
add:
```c
	UserList[u].us_Flags|=UF_NAMEWORD;
```

Same edit in `CreatePersona()`, right after its identical `AddWord(UserList[u].us_Name,(short)(10000+u),WD_NOUN);` line.

- [ ] **Step 5: Guard the `FreeWord` calls in `SysSupport.c`**

In `RemoveUser()`, change:
```c
	if(*UserList[u].us_Name)
		FreeWord(UserList[(unsigned short)u].us_Name,WD_NOUN);
```
to:
```c
	if(*UserList[u].us_Name && (UserList[u].us_Flags&UF_NAMEWORD))
		FreeWord(UserList[(unsigned short)u].us_Name,WD_NOUN);
```

In `ExitUser()`, change:
```c
	FreeWord(UserList[(unsigned short)u].us_Name,WD_NOUN);
```
to:
```c
	if(*UserList[u].us_Name && (UserList[u].us_Flags&UF_NAMEWORD))
		FreeWord(UserList[(unsigned short)u].us_Name,WD_NOUN);
```

(`us_Flags` is already reset to `0` for every freshly-bound slot in `Handle_Login()` — `ComServer.c:217` — so no extra reset is needed; a slot that never completes login never gets `UF_NAMEWORD` set, and a slot that does gets it cleared implicitly the next time that slot is reused and re-zeroed.)

- [ ] **Step 6: Rebuild and re-run, confirm the scenario now passes**

```bash
make server
python3 tests/smoke_test.py --server-bin ./server
```

Expected: `PASS: scenario_abandoned_login_does_not_steal_active_name`, `3/3 scenarios passed`.

- [ ] **Step 7: Commit**

```bash
git add User.h ComDriver.c SysSupport.c tests/smoke_test.py
git commit -m "fix: only free a player's name reservation if this session actually registered it (C3)"
```

---

## Task 5: Fix C2 — wizard status via self-registered hardcoded name

**Files:**
- Modify: `SysSupport.c` (add `ReservedWizardNames[]`, `NameIsReservedWizardName()`, `AnyReservedWizardNameRegistered()`)
- Modify: `Prototype.h` (declare the two new functions)
- Modify: `ComDriver.c` (`Name_Got()` — block new registration under a reserved name once any reserved name is already registered)
- Modify: `tests/smoke_test.py` (add two scenarios: block after bootstrap, allow bootstrap)

**Interfaces:**
- Produces: `int NameIsReservedWizardName(char *name)`, `int AnyReservedWizardNameRegistered(void)` — usable by any future admin tooling that needs to reason about the reserved-name set without duplicating the literal list.
- Consumes: `LoadPersona()` (`UserFile.c`, existing), `stricmp()` (`AnsiBits.c`, existing).

- [ ] **Step 1: Add the failing scenarios first**

Append to `tests/smoke_test.py`:

```python
RESERVED_WIZARD_NAMES = ["Arashi", "Hobbit", "Debugger", "Bonzo", "Anarchy", "Debugiit"]


@scenario
def scenario_reserved_name_blocked_once_bootstrapped(t):
    """C2: once a reserved wizard name is already registered, a second,
    unrelated anonymous connection must NOT be able to register a brand-new
    persona under that same name (was: ArchWizard() is purely name-based and
    the registration flow never checked the reserved-name list at all)."""
    first = t.connect()
    t.register(first, "Arashi")  # legitimate first-ever bootstrap must still work
    first.close()
    time.sleep(0.3)

    attacker = t.connect()
    transcript = t.register(attacker, "Arashi")
    assert b"reserved" in transcript.lower(), (
        f"a second registration under 'Arashi' was not rejected as reserved. transcript={transcript!r}"
    )
    attacker.close()


@scenario
def scenario_reserved_name_bootstrap_still_works_on_fresh_game(t):
    """C2 companion: a brand-new game (no reserved name registered yet) must
    still be able to register its first admin under a reserved name — this
    codebase has no other way to create the first wizard."""
    s = t.connect()
    transcript = t.register(s, "Hobbit")
    assert b"-}---" in transcript, (
        f"first-ever bootstrap registration under a reserved name unexpectedly failed. transcript={transcript!r}"
    )
    s.close()
```

- [ ] **Step 2: Run against the current (unfixed) code and confirm both fail appropriately**

```bash
python3 tests/smoke_test.py --server-bin ./server
```

Expected: `scenario_reserved_name_blocked_once_bootstrapped` FAILs (no rejection happens today); `scenario_reserved_name_bootstrap_still_works_on_fresh_game` already PASSes today (bootstrap already "works" today, just insecurely — that scenario exists to prevent Step 3's fix from over-blocking it).

- [ ] **Step 3: Add the reserved-name helpers to `SysSupport.c`**

Add near `ArchWizard()`:

```c
/*
 *	Reserved wizard identity names. Keep this list in sync with the
 *	literal comparisons in ArchWizard() above — this is the set of names
 *	that grant full admin privileges purely by being used as a display
 *	name (see docs/review-findings.md finding C2). New registrations
 *	under any of these are blocked once any one of them is already
 *	registered (AnyReservedWizardNameRegistered()), so a brand-new game
 *	can still bootstrap its first wizard exactly once, but an established
 *	game can't have this identity stolen or duplicated via registration.
 */
static char *ReservedWizardNames[]={
	"Anarchy","Debugiit",BOSS1,BOSS2,BOSS3,BOSS4,BOSS5,NULL
};

int NameIsReservedWizardName(char *name)
{
	int ct=0;
	while(ReservedWizardNames[ct])
	{
		if(stricmp(name,ReservedWizardNames[ct])==0)
			return(1);
		ct++;
	}
	return(0);
}

int AnyReservedWizardNameRegistered(void)
{
	UFF dummy;
	int ct=0;
	while(ReservedWizardNames[ct])
	{
		if(LoadPersona(ReservedWizardNames[ct],&dummy)!=-1)
			return(1);
		ct++;
	}
	return(0);
}
```

- [ ] **Step 4: Declare the new functions in `Prototype.h`**

Near the existing `extern int ArchWizard(ITEM *);` line, add:

```c
extern int NameIsReservedWizardName(char *);
extern int AnyReservedWizardNameRegistered(void);
```

- [ ] **Step 5: Block reserved-name registration (once bootstrapped) in `ComDriver.c`**

In `Name_Got()`, right after:
```c
	if(LoadPersona(name,&LoginUFF)==-1)	/* New player ? */
	{
```
add:
```c
		if(NameIsReservedWizardName(name)&&AnyReservedWizardNameRegistered())
		{
			SendUser(u,"Sorry that name is reserved.\n");
			SendTPacket(UserList[u].us_Port,PACKET_SETPROMPT,
					"By what name shall I call you: ");
			UserList[u].us_State=AWAIT_NAME;
			*UserList[u].us_Name=0;
			PermitInput(u);
			return(0);
		}
```
(before the existing `#ifdef AGOS` line inside that same `if` block).

- [ ] **Step 6: Rebuild and re-run, confirm both scenarios now pass**

```bash
make server
python3 tests/smoke_test.py --server-bin ./server
```

Expected: `PASS: scenario_reserved_name_blocked_once_bootstrapped`, `PASS: scenario_reserved_name_bootstrap_still_works_on_fresh_game`, `5/5 scenarios passed`.

- [ ] **Step 7: Commit**

```bash
git add SysSupport.c Prototype.h ComDriver.c tests/smoke_test.py
git commit -m "fix: block re-registration under a reserved wizard name once one is bootstrapped (C2)"
```

**Note for the user:** this closes the *registration* path. It does not add a way to grant wizard status to a second person after the first bootstrap — that already exists today via an existing wizard's `SetName` command (`Editing.c`, already `ArchWizard()`-gated) renaming a trusted player to one of the reserved names. If that's not an acceptable long-term admin model, replacing name-based wizard status with a persistent account flag is a larger follow-up (would need a `UAF` format bump) — not in this patch's scope per your H2/H1 scoping answers, but flagging it since C2 and that follow-up are related.

---

## Task 6: Fix H1 — truncated password comparison

**Files:**
- Modify: `ComDriver.c:334` (`Check_Password`)
- Modify: `tests/smoke_test.py` (add `scenario_full_password_is_checked`)

- [ ] **Step 1: Add the failing scenario first**

Append to `tests/smoke_test.py`:

```python
@scenario
def scenario_full_password_is_checked(t):
    """H1: login must compare the full password, not just the first 7 of 8
    bytes (was strncmp(...,7) in Check_Password, ComDriver.c)."""
    s = t.connect()
    t.register(s, "Pwcheck", password="abcdefgh")  # 8-char password
    s.close()
    time.sleep(0.3)

    # A DIFFERENT 8-char password sharing the first 7 bytes must be rejected.
    s2 = t.connect()
    t.read(s2, 0.5)
    t.send(s2, "Pwcheck")
    t.read(s2, 0.5)
    t.send(s2, "abcdefgX")  # same first 7 bytes, different 8th
    transcript = t.read(s2, 0.8)
    assert b"-}---" not in transcript, (
        "login succeeded with a password differing only in the 8th byte — "
        "the comparison is still truncated"
    )
    s2.close()
```

- [ ] **Step 2: Run against the current (unfixed) code and confirm it fails**

```bash
python3 tests/smoke_test.py --server-bin ./server
```

Expected: `FAIL: scenario_full_password_is_checked: login succeeded with a password differing only in the 8th byte...`.

- [ ] **Step 3: Fix the comparison**

In `ComDriver.c`, inside `Check_Password()`, change:
```c
	if(strncmp(pwentry,LoginUFF.uff_Password,7))
```
to:
```c
	if(strncmp(pwentry,LoginUFF.uff_Password,8))
```

- [ ] **Step 4: Rebuild and re-run, confirm it now passes**

```bash
make server
python3 tests/smoke_test.py --server-bin ./server
```

Expected: `PASS: scenario_full_password_is_checked`, `6/6 scenarios passed`.

- [ ] **Step 5: Commit**

```bash
git add ComDriver.c tests/smoke_test.py
git commit -m "fix: compare the full 8-byte password field at login instead of truncating to 7 (H1)"
```

---

## Task 7: H2 safety net — reject/flag unsafe 64-bit pointer packing in action-table bytecode

**Files:**
- Modify: `CompileTable.c` (the two literal-item/text pointer-pack call sites)
- Modify: `SaveLoad.c` (`LoadAction`'s three `SetTwo(...)` re-pack call sites)

**Interfaces:**
- No new exported symbols. This task adds validation inline at existing pack sites; nothing outside these two files needs to change.
- Not covered by the network-level smoke-test harness (see rationale below) — verified by a manual compile/inspection step in Step 4.

Per the scope decision: this task does **not** change the bytecode operand format or the interpreter's word-stride logic (that would be finding H2's "full redesign" option, explicitly out of scope). It adds validation exactly at the two places a pointer gets packed into the unsafe 2×16-bit representation, so a value that can't survive that packing is caught immediately instead of silently corrupted.

- [ ] **Step 1: Add a shared round-trip check macro**

At the top of `CompileTable.c` (near its other static helpers) and again at the top of `SaveLoad.c` (both files already independently implement the pack/unpack logic — see [docs/persistence.md](../../persistence.md) §1.3 — so the check is duplicated in both rather than introducing a new cross-file dependency for a two-line check):

```c
/* A pointer only survives the compiled bytecode's 2x16-bit packed
   representation if it fits in 32 bits. See docs/review-findings.md
   finding H2 — this is a safety net, not a fix for the underlying
   16-bit-word bytecode format. */
#define FITS_PACKED_WIDTH(p)	(sizeof(void *)<=4 || ((unsigned long)(void *)(p))>>32==0)
```

(`sizeof(void*)<=4` short-circuits the `||` on a 32-bit build, so the right-hand shift is never evaluated there — shifting a 32-bit `unsigned long` by 32 bits would otherwise be undefined behavior, not a guaranteed no-op. `||`'s short-circuit guarantees the shift is skipped at runtime whenever the left side is true, which is the standard, safe way to write this, not just a stylistic preference.)

- [ ] **Step 2: Reject at compile time in `CompileTable.c`**

At the first pack site (~line 492, the item-literal encoder), change:
```c
l1:	if(WriteDb((unsigned short)(((unsigned int)(x))/65536L))==-1)
	{
		SendItem(i,"Line Too Complex.\n");
		return(-1);
	}
	if(WriteDb((unsigned short)(((unsigned int)(x))%65536L))==-1)
	{
		SendItem(i,"Line Too Complex.\n");
		return(-1);
	}
	return(0);
```
to:
```c
l1:	if(!FITS_PACKED_WIDTH(x))
	{
		SendItem(i,"This item reference cannot be stored safely on this build (address too wide) — table not compiled.\n");
		return(-1);
	}
	if(WriteDb((unsigned short)(((unsigned int)(x))/65536L))==-1)
	{
		SendItem(i,"Line Too Complex.\n");
		return(-1);
	}
	if(WriteDb((unsigned short)(((unsigned int)(x))%65536L))==-1)
	{
		SendItem(i,"Line Too Complex.\n");
		return(-1);
	}
	return(0);
```

At the second pack site (~line 539, `EncodeText`'s literal-text-pointer encoder), apply the same change: add the `if(!FITS_PACKED_WIDTH(a)) { SendItem(i,"..."); return(-1); }` guard immediately before its first `WriteDb(...)` call, with a message adjusted to say "text reference" instead of "item reference".

This makes an unsafe literal reference a **compile-time rejection with a clear message to the wizard editing the table**, instead of a silent truncation — the safest possible outcome for the author-facing path.

- [ ] **Step 3: Log (don't abort) at load time in `SaveLoad.c`**

`LoadAction()` reconstructs an already-safely-resolved `ITEM*`/`TPTR` (via `LoadItem()`/`LoadString()`/`LoadComment()`, which are ordinal-based and always valid — see [docs/persistence.md](../../persistence.md) §1.3) and then re-packs it into the live bytecode's 2×16-bit slot. Aborting the whole boot here would be disproportionate (one old, possibly-already-affected table line taking down the entire server on every future restart) — so this path logs instead, so the operator has visibility instead of silence.

Change, at each of the three `SetTwo(c,(char *)t);` / `SetTwo(c,(char *)i);` call sites in `LoadAction()`:

```c
			 SetTwo(c,(char *)t);
			 c+=2;
			 break;
```

to:

```c
			 if(!FITS_PACKED_WIDTH(t))
				 Log("WARNING: loaded table references a pointer too wide to pack safely on this build (see docs/review-findings.md H2) — this line may misbehave");
			 SetTwo(c,(char *)t);
			 c+=2;
			 break;
```

(and correspondingly for the `i` variant, with `t` replaced by `i` in the guard).

- [ ] **Step 4: Manual verification (no automated scenario for this one)**

This finding isn't reachable through the plain-text network protocol without first having wizard access and a populated universe with a literal item-reference table line — building that fixture is disproportionate to what this safety-net fix needs to prove. Instead, verify by inspection + build:

```bash
make server
echo "build exit: $?"
grep -n "FITS_PACKED_WIDTH" CompileTable.c SaveLoad.c
```

Expected: clean build, and both files show the guard applied at all five call sites (2 in `CompileTable.c`, 3 in `SaveLoad.c`). Confirm by re-reading the diff that no existing call site was missed (cross-check against the exact line list gathered during planning: `CompileTable.c` lines ~492 and ~539; `SaveLoad.c` `SetTwo` calls at the three sites inside `LoadAction()`).

- [ ] **Step 5: Commit**

```bash
git add CompileTable.c SaveLoad.c
git commit -m "fix: add a 64-bit safety net around action-table item/text pointer packing (H2)"
```

---

## Task 8: Full regression pass and wrap-up

**Files:** none (verification only)

- [ ] **Step 1: Clean build from scratch**

```bash
make clean
make server FindPW Run_Aber Reg
echo "exit: $?"
```

Expected: `0`, no new warnings beyond what Task 1 already accepted as pre-existing (see [docs/review-findings.md](../../review-findings.md) L5 for the pre-existing warning list — this patch must not add to it).

- [ ] **Step 2: Full smoke-test run**

```bash
python3 tests/smoke_test.py --server-bin ./server
```

Expected: `8/8 scenarios passed`, exit code `0`.

- [ ] **Step 3: Update `docs/review-findings.md` status for each fixed finding**

For C1, C2, C3, C4, H1, H2, add a one-line "**Fixed:** <date>, see `fix/critical-high-findings`" note under each finding's `Status:` line (H3 gets the same note pointing at the new `tests/` directory). Do not remove the original finding text — it stays as the historical record of what was wrong and why.

- [ ] **Step 4: Final commit**

```bash
git add docs/review-findings.md
git commit -m "docs: mark C1-C4, H1-H3 as fixed in review-findings.md"
```

- [ ] **Step 5: Stop — do not push or open a PR**

Per this session's standing instruction, commits happen locally on `fix/critical-high-findings`; pushing/PR creation waits for an explicit ask from the user.

---

## Self-Review

**Spec coverage:** C1 → Task 3. C2 → Task 5. C3 → Task 4. C4 → Task 1. H1 → Task 6. H2 → Task 7. H3 → Task 2 (+ one scenario per task). All six Critical/High-severity findings plus H3 (tests) are covered; no Critical/High finding from [review-findings.md](../../review-findings.md) is left unaddressed. Medium/Low findings (M1–M5, L1–L5) are intentionally out of scope, per the user's request to address "all critical and high bugs."

**Placeholder scan:** every step has literal code, not descriptions of code; every commit step has an exact message; no "TODO"/"handle appropriately" language.

**Type/name consistency:** `UF_NAMEWORD` (Task 4) is defined once in `User.h` and used identically in `ComDriver.c` and `SysSupport.c`. `NameIsReservedWizardName`/`AnyReservedWizardNameRegistered` (Task 5) are declared in `Prototype.h` exactly matching their `SysSupport.c` definitions and are called with matching signatures from `ComDriver.c`. `FITS_PACKED_WIDTH` (Task 7) is defined identically (copy-pasted intentionally, per Step 1's own note) in both files it's used in. `SmokeTest`/`@scenario` (Task 2) interfaces are used consistently by every later task's scenario additions (`t.connect()`, `t.read()`, `t.send()`, `t.register()`, `t.is_alive()`).
