# AberMUD 5.30 — Build and Run

Back to [architecture.md](architecture.md). Related: [operations.md](operations.md).

This document was validated by actually building this repository's code in an isolated scratch
copy and running the resulting server during this review — see §4 for exactly what was done and
what was fixed to get there. **No files in this repository were modified by that process**; the
Makefile and sources are exactly as committed (`git status` is clean apart from the new `docs/`
directory this review adds).

## 1. What the Makefile builds

```
make all  →  server  FindPW  Run_Aber  Reg  docs
```

| Target | Links | Produces |
|---|---|---|
| `server` | all of `OFILES` (the full engine, minus `Reg.c`/`FindPW.c`/`Run_Aber.c`) | the MUD server binary |
| `FindPW` | `UserFile.o FindPW.o AnsiBits.o` | offline password-lookup tool |
| `Reg` | `UserFile.o AnsiBits.o Reg.o` | offline "mark persona registered" tool |
| `Run_Aber` | `Run_Aber.o` | process supervisor (see [runtime-flows.md](runtime-flows.md) §8) |
| `client` | `Socket.o Client.o` (links `-lcurses`) | **cannot build — `Client.c` is not present in this repository** |
| `docs` | runs `nroff -man` over `DOC/*` | **cannot build — no `DOC/` directory in this repository** |

`CC = gcc -Wall -pedantic -std=gnu17` — this now pins the C dialect explicitly (previously there was
no `-std=` flag, so it used the compiler's default C dialect). This was the C4 fix; see §4 below and
[review-findings.md](review-findings.md) C4.

## 2. Missing files (confirmed by `ls`/`git ls-files`)

The Makefile's `CFILES`/prerequisites reference these paths, none of which exist in this checkout:

- `Client.c` — the curses-based `client` target's source (README-5.30 describes a "massively
  cleaned up" client expecting a modern XPG curses library; not present here).
- `runaber.c` and `IPCClean.c` — listed in `CFILES` for `server`, but the actual restart-supervisor
  logic lives in `Run_Aber.c` (built and linked separately, correctly) and no `IPCClean.c` exists
  anywhere in the tree. **Because `OFILES` (the list actually linked into `server`) does not include
  `runaber.o`/`IPCClean.o`, this omission does not stop `server` from building** — it only means
  those two filenames in `CFILES` are stale/dead references with no effect on the default targets.
- `NoProto.h` exists (confirmed present) but is **not** the header actually compiled in this
  configuration — `System.h` includes `Prototype.h` when `ANSI_C` is defined (it always is, per
  `System.h:75`) and falls back to a file literally named `NoPrototype.h` (note: different from
  `NoProto.h`) only when it isn't — and `NoPrototype.h` also does not exist in this repository. In
  practice this branch is never taken because `ANSI_C` is always defined, so it has no effect on a
  default build, but the fallback path is unusable as written if anyone ever undefines `ANSI_C`.
- `DOC/` (a directory of `nroff -man` source files for `Manual.txt`) does not exist, so
  `make docs` (and therefore a bare `make all`, which lists `docs` as a prerequisite) fails.

**Practical impact**: `make all` as shipped does not fully succeed (`client` isn't wired into `all`
in this Makefile, so that specific gap doesn't block `all`, but `docs` is a prerequisite of `all`
and does block it). `make server` (and `FindPW`/`Run_Aber`/`Reg` individually) are unaffected by any
of the missing files and build to completion. See [review-findings.md](review-findings.md) for this
logged as a build-hygiene finding.

## 3. Compiler compatibility problems found

Building `server` with a stock modern GCC (this review used GCC 16.2.1 on Linux, C dialect defaulted
by `-Wall -pedantic` with no `-std=`) fails outright with several classes of error, **all confirmed
in this review by attempting the unmodified build**:

1. **`errno` declared as `extern int` in application code**
   (`SaveLoad.c`, `LibSocket.c`, `Socket.c`, `UserFile.c`) instead of including `<errno.h>`. Modern
   glibc declares `errno` via a thread-local macro, and a conflicting plain `extern int errno;`
   declaration causes a hard link-time error (`TLS definition ... mismatches non-TLS reference`),
   not just a warning. This is a genuine portability defect in the source, not a toolchain quirk —
   see [review-findings.md](review-findings.md).
2. **Function pointer / signal handler type mismatches** — `Main.c`'s `SegV`/`Bus`/`Div0` handlers
   and `extern int WrapUp()` are declared with old K&R-implicit-int-style signatures that don't
   match the ANSI prototypes pulled in via `Prototype.h`, and `signal()` is called with handlers
   whose declared type doesn't match `void(*)(int)` under `-pedantic` on a modern compiler that no
   longer tolerates implicit-int. This is exactly the pre-ANSI/ANSI promotion-rule variance the
   comment block at [System.h:30-37](../System.h) warns about — it was apparently tolerated by
   compilers available when 5.30 was released and is not tolerated by current GCC.
3. **`SendItem()`/similar varargs-via-K&R-style-declaration functions rejected as over-called** —
   several files (`TableDriver.c`, `TabCommand.c`) call `SendItem(i, fmt, a, b, ...)` with a varying
   number of trailing arguments, matching the K&R-style definition in `SysSupport.c`
   (`void SendItem(x,a,b,c,d,e,f) ITEM *x; char *a,*b,*c,*d,*e,*f;`), but a strict ANSI prototype for
   it elsewhere in the header chain declares it with a fixed, smaller arity, and modern GCC enforces
   that prototype strictly rather than falling back to the old-style definition's laxer checking.

None of these are exotic — they are the direct, predictable fallout of a codebase written against
1990s-era C compilers (explicitly Lattice C / early ANSI-transitional compilers, per comments
throughout) being compiled with a 2020s-era strict-ANSI/ISO C compiler. They are **build-blocking**,
not just warnings, on the toolchain used for this review.

## 4. What this review actually did to get a working build (scratch copy only, originally — since applied for real)

> **Update:** the fix described in this section has since been applied for real and committed to
> this repository, as Task 1 of the `worktree-fix-critical-high-findings` branch (commit `f8fbca1`,
> "build: fix errno declarations and relax C standard so the project builds on modern GCC (C4)") —
> see [review-findings.md](review-findings.md) C4. The Makefile's `CC` line and the four `errno`
> declarations now match what this section originally validated only in a scratch copy. The
> narrative below is kept as historical context for *how* that fix was first discovered and
> validated, before it was committed.

To validate the runtime flows and findings in this documentation set, this review made a **temporary,
uncommitted copy** of the repository in a scratch directory and applied the minimum patch needed to
get `server`/`FindPW`/`Reg`/`Run_Aber` to link, without touching game logic:

1. Replaced the four `extern int errno;` declarations with `#include <errno.h>` (or added the
   include where it was missing) in `SaveLoad.c`, `LibSocket.c`, `Socket.c`, `UserFile.c`.
2. Compiled with `-std=gnu17` instead of the compiler's bare default, which relaxed the old-style
   function declaration/prototype conflicts enough to link successfully (GNU C's more permissive
   handling of pre-ISO declaration styles under `gnu17`, versus stricter behavior under other
   defaults on this toolchain).

With those two changes, `make server FindPW Run_Aber Reg CC="gcc -std=gnu17 -Wall -pedantic"`
completed successfully in the scratch copy, producing working binaries. `make docs` still fails
(no `DOC/`, as documented above) and was not attempted further.

**This review recommended these same two changes (or equivalent modernization) be made to the actual
repository by the maintainers**, tracked as a Critical build finding — see
[review-findings.md](review-findings.md). At the time this review was written, they were deliberately
**not** applied to this checked-in copy of the repository, per this review's scope ("do not make
unrelated code changes") — they have since been applied for real, as noted at the top of this
section.

### 4.1 Runtime validation performed

Using the patched scratch build:

- Started `./server -p 5100` with an empty/fresh `UAF` file and no universe argument; confirmed it
  logs `Startup Commenced` / `Startup Completed` / `IPC Server Starting` to `Creator.log` and binds
  three listening sockets (verified with `ss -ltnp`, ports `5100`/`5101`/`5102`).
  - **Empty-universe login gap**: with no universe loaded, a brand-new registration walks the full
    name/sex/email/password flow and creates a player `ITEM`, but `FindMaster(10000,1,1)`
    (the conventional "autostart room", adjective/noun code 1/1) never resolves because no room with
    that vocabulary exists — the player is created but left unplaced (`O_FREE`). This is expected,
    self-consistent behavior for an empty database (not a bug), and was useful for isolating the
    comms-layer/login-flow tests in this review from needing real game content.
- Drove full connect → name → sex → email → password → login flows over raw TCP with a Python test
  harness, confirming the state-machine transitions described in
  [runtime-flows.md](runtime-flows.md) §2 match observed server behavior.
- Confirmed the 13th simultaneous idle connection is refused with "Sorry..... the game is currently
  full." — matching the `MaxSlot()`-gated cap described in
  [runtime-flows.md](runtime-flows.md) §2 for the hour-of-day the test ran in.
- Rebuilt with `-fsanitize=address` and reproduced a stack-buffer-overflow crash from a single
  oversized (>513-byte) input line, confirming the finding recorded in
  [review-findings.md](review-findings.md) as Critical.
- Reproduced the name-reuse-after-abandoned-registration defect (also in
  [review-findings.md](review-findings.md)) by completing one registration for a name, then starting
  and abandoning (disconnecting mid-flow) a second registration attempt for the *same* name, and
  observing that a third connection was subsequently able to complete registration under that name
  while the first session was still logged in.

## 5. Running the server manually

```
./server [-p <port>] [<universe-file>]
```

- `-p <port>` overrides the default listening port (`TCP_PORT`=5000, [System.h](../System.h));
  the port argument **must be `>= 5000`** or the process refuses to start (`Main.c:87-91`,
  an explicit RFC-alignment check, not configurable). Three consecutive ports are bound starting at
  this value (main / alt / BSX — see [communication-protocol.md](communication-protocol.md)).
- `<universe-file>`, if given, is loaded via `LoadSystem()` at boot; if omitted the server starts
  with an empty world (see [runtime-flows.md](runtime-flows.md) §1).
- The process must be run from a working directory containing (or able to create) `Creator.log`
  (hardcoded `LOG_FILE`, [System.h](../System.h)), `UAF` (hardcoded `USERFILE`), and optionally
  `motd`/`motd.bsx` and `register.log` (created on first registration by
  `SetPlayerEmail`, [ComDriver.c:476](../ComDriver.c)). **`UAF` must already exist and be openable
  `r+`** — `OpenUAF()` (`UserFile.c`) has no create-if-missing fallback and calls `Error()` (crash
  path) if `fopen(..., "r+")` fails; an empty (zero-record) file is sufficient (`touch UAF` before
  first run).
- When invoked with parent PID 1 (i.e. launched directly by an init system rather than a shell),
  `Main.c` closes stdin/stdout/stderr and reopens `/dev/null`/`syslog` in their place — this is a
  historical daemonization shortcut, not a general-purpose init-system integration (see
  [operations.md](operations.md) for the more complete `Run_Aber`-based alternative).

## 6. Confirmed vs. unclear

**Confirmed by this review's own build/run** (see §4): the exact compiler errors, the two patches
that resolve them, and the resulting binary's basic startup/login/connection-cap/crash behavior.

**Unclear / needs verification by maintainers**: whether the `-std=gnu17` relaxation is an
acceptable permanent fix or whether the underlying K&R-style declarations should instead be properly
converted to ANSI prototypes throughout (the more correct, but far larger, fix); whether other C
compilers/platforms (the code's comments reference Lattice C and the Amiga toolchain, neither
available to this review) still matter as build targets for this project going forward.
