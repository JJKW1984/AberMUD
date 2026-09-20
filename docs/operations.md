# AberMUD 5.30 — Operations

Back to [architecture.md](architecture.md). Related: [build-and-run.md](build-and-run.md),
[persistence.md](persistence.md), [review-findings.md](review-findings.md).

This is an operator-facing summary: what files the server needs, what it produces, how to keep it
running, and what to watch for. It restates operationally-relevant facts from the other documents
rather than re-deriving them — follow the links for the underlying evidence.

## 1. Required and generated files (all paths relative to the process's working directory)

| Path | Role | Required at start? | Constant / source |
|---|---|---|---|
| `UAF` | Player account records (fixed-length `UFF` structs) | **Yes** — must already exist and be `r+`-openable, or the process calls `Error()` and crashes ([UserFile.c](../UserFile.c) `OpenUAF`) | `USERFILE`, [System.h](../System.h) |
| `Creator.log` | Append-only operational log (`Log()`, [System.c](../System.c)) | No — created/appended automatically | `LOG_FILE`, [System.h](../System.h) |
| `<universe-file>` (e.g. `abermud.uni`) | The world/game content, given as `argv[1]` | No — server boots with an empty world if omitted | [Main.c](../Main.c) |
| `motd` / `motd.bsx` | Message-of-the-day shown at login; BSX-mode connections prefer `motd.bsx`, falling back to `motd` | No | [ComServer.c](../ComServer.c) `Handle_Login` |
| `register.log` | Append-only log of `name: email` for every new registration | No — created on first registration | [ComDriver.c](../ComDriver.c) `SetPlayerEmail` |
| `<name>.bak` | Previous universe file, kept by `SaveSystem` before each overwrite | Produced automatically | [SaveLoad.c](../SaveLoad.c) |
| `gonebang.uni` | Emergency dump written once by the crash-rescue path | Produced only after a fatal in-process `Error()` | [System.c](../System.c) `ErrFunc` |
| `server_log` | `Run_Aber`'s own append-only log (separate from `Creator.log`) | Created by `Run_Aber` on first launch | [Run_Aber.c](../Run_Aber.c) |
| `server.old` | Previous `server` binary, kept by the Makefile's link step (`mv server server.old`) | Produced by every `make server` | [Makefile](../Makefile) |

## 2. Ports

Three consecutive TCP ports starting at `SysPort` (default `TCP_PORT`=5000, override with
`-p <port>`, must be `>=5000`): main (plain telnet), `+1` ("alt", purpose beyond connection tagging
unconfirmed — see [communication-protocol.md](communication-protocol.md)), `+2` (BSX graphics
clients). All three must be free for the process to start (`Make_Socket`, [LibSocket.c](../LibSocket.c),
retries on `EADDRINUSE` with a 5-second sleep loop **indefinitely** — i.e. a stuck old process
holding the port will hang the new one's startup rather than failing fast; there is no timeout).

## 3. Starting and supervising the server

Two supported patterns, both confirmed by reading the relevant source:

1. **Directly**, e.g. from a process manager that already provides restart-on-exit (systemd
   `Restart=always`, a container orchestrator, etc.): `./server -p 5000 mygame.uni`. `Main.c`'s
   parent-pid-1 detection (closing/reopening stdio to `/dev/null`/`syslog`) is a minimal, historical
   accommodation for being launched by `init` directly — it does not provide structured logging
   integration with systemd (no `sd_notify`, no journal-native output), so operators using systemd
   should still capture `Creator.log` explicitly rather than relying on captured stdout.
2. **Via `Run_Aber`**: `./Run_Aber` daemonizes itself, then forks/execs `./server` in a supervised
   loop, restarting it whenever it exits for any reason, logging to `server_log`. **Confirmed gap**:
   `Run_Aber` execs `./server` with no arguments, so a universe filename is never passed through —
   supervising a server this way means every restart boots with an *empty* world unless something
   else (not present in this repository) arranges otherwise. See
   [review-findings.md](review-findings.md); until resolved, `Run_Aber` should not be used as-is for
   a deployment expecting a persistent universe to reload automatically. `Run_Aber`'s crash-loop
   guard gives up entirely (exits) if two consecutive restarts happen within 10 seconds of each
   other — after that point nothing restarts `server` again without an operator manually re-running
   `Run_Aber`.

Either pattern requires the working directory to already contain a valid `UAF` file (§1) before
first start.

## 4. Restart / deployment procedure

Because `SaveSystem` is **only ever invoked by explicit triggers** (in-game `SaveUniverse` command,
`Act_ForkDump`, or the one-shot crash-rescue path — see [persistence.md](persistence.md) §1.4), there
is **no automatic periodic save**. Any planned restart or deployment must ensure a wizard/admin has
run `SaveUniverse` (or an equivalent action-table-driven save) recently enough that acceptable data
loss applies, since:

- All pending timer/daemon (`WHEN`) events are lost on every restart regardless of save recency
  ([persistence.md](persistence.md) §1.5).
- All connected sessions are dropped on every restart (expected — TCP connections don't survive a
  process restart in this architecture).

A safe manual restart sequence, inferred from the code's own facilities (not itself scripted
anywhere in this repository):

1. Have a wizard run `SaveUniverse` (or trigger `Act_ForkDump` for a non-disruptive backup pass).
2. Stop the server (`PACKET_ABORT` in-game equivalent is `Cmd_Abort`/`Act_Abort`, which call
   `exit(0)` directly — there is no graceful "stop accepting new connections, drain, then exit"
   shutdown path; stopping the process disconnects everyone immediately).
3. Restart with the same universe filename argument passed explicitly (if not using `Run_Aber`, or
   until the `Run_Aber` argument-passing gap above is fixed).

## 5. Load-shedding behavior operators should know about

`MaxSlot()` ([UserVector.c](../UserVector.c)) caps simultaneous logged-in-or-logging-in sessions by
local server time of day (20 before 7am, 12 from 7am–9pm, 16 from 9–11pm, 20 at 11pm exactly, 8
otherwise) — independent of, and always lower than, the hard `MAXUSER`=128 array size. This is
explicitly documented in the source as customization scaffolding
(`/* This function should be customised according to the actual load parameters you wish to
enforce */`) — operators running this server should review and likely rewrite `MaxSlot()` for their
actual hardware/expected concurrency rather than relying on these historical defaults, which were
clearly tuned for a specific, now-obsolete host.

## 6. Monitoring signals

- `Creator.log` (`Log()` calls throughout) is the primary operational log — startup/shutdown
  milestones, load errors, `Error()` invocations, corrupt-login-attempt warnings, `UAF` write
  failures.
- A process that logs a `MODULE:`/`FILE:`/`LINE:`/`ERROR LOGGED WAS` block (the `ErrFunc` format) and
  then either recovers ("Attempting to rescue universe" → `gonebang.uni`) or prints "Cannot rescue
  game." and exits has hit an internal assertion failure (`Error()` macro) — this is the "soft
  crash" path and should always be investigated even though the process may continue running
  briefly before the final `exit(1)`.
- A process that disappears **without** an `ErrFunc`-style log entry, especially alongside an
  oversized-input pattern from a client, is more likely to have hit one of the memory-safety issues
  in [review-findings.md](review-findings.md) (raw `SIGSEGV`/`SIGBUS`/`abort()`), which are logged
  only as a one-line `Log("Segmentation Fault caused abort")`/similar before the process dies — check
  for a core dump (subject to the platform's core-pattern/ulimit configuration; not itself configured
  by anything in this repository).

## 7. Known operational gaps (see [review-findings.md](review-findings.md) for full detail)

- No automatic periodic save.
- No graceful shutdown/drain.
- `Run_Aber` does not pass the universe filename through to `./server`.
- No file locking between the live server and the offline `Reg`/`FindPW` tools against `UAF`.
- No rate limiting or connection throttling beyond the coarse `MaxSlot()`/`MAXUSER` caps — a single
  source can open many connections rapidly (see the Critical finding regarding unauthenticated input
  handling before any rate limiting applies).
- No log rotation for `Creator.log`/`server_log`/`register.log` — all three are append-only for the
  life of the deployment; operators should arrange external rotation (`logrotate` or equivalent).

## 8. Confirmed vs. unclear

**Confirmed**: file/port requirements, save triggers, `Run_Aber`'s argument-passing gap, `MaxSlot()`
behavior, log file locations.

**Unclear / needs verification**: actual expected concurrency/hardware profile this server is meant
to run on today (the `MaxSlot()` defaults and the 128-user cap both read as tuned for a much older,
more resource-constrained host than a typical modern deployment target) — this is an operator/product
decision, not something resolvable from the code alone.
