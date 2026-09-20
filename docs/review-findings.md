# AberMUD 5.30 — Review Findings

Back to [architecture.md](architecture.md). Cross-referenced from
[build-and-run.md](build-and-run.md), [persistence.md](persistence.md),
[communication-protocol.md](communication-protocol.md), [command-processing.md](command-processing.md).

Findings are ordered **Critical → High → Medium → Low**. Each includes: affected file/function,
the behavior, why it's a problem, a realistic failure scenario, a suggested remediation, and whether
it is **Confirmed** (reproduced or directly verified in this review) or **Plausible/needs runtime
verification** (established by static reading, not independently reproduced).

## Critical

### C1 — Remote unauthenticated stack buffer overflow in line assembly

- **File/function**: [IPCDirect.c:184-206](../IPCDirect.c), `ReadBlock()`, feeding
  `strcpy(pkt->pa_Data, UserInput[u])`; destination is `COMTEXT.pa_Data[512]`
  ([Comms.h](../Comms.h)).
- **Behavior**: Incoming bytes are appended one at a time into `UserInput[u][514]` until either a
  `\n` is seen or `InputPos[u]` reaches `513`. When the 513-count trips, the code null-terminates at
  index 512 (`UserInput[u][InputPos[u]-1]=0`), producing a **512-character string (513 bytes
  including the terminator)**, then `strcpy`s it directly into `pkt->pa_Data`, a **512-byte**
  buffer — one byte too small for the maximum string this code path can produce.
- **Why it's a problem**: This is a classic off-by-one buffer overflow, reachable by any TCP client
  **before authentication** (it fires on the very first line sent, including at the login-name
  prompt), on the pre-login, plain-telnet, and BSX ports alike.
- **Failure scenario / reproduction**: Confirmed in this review — built the tree with
  AddressSanitizer (`-fsanitize=address`), connected, and sent a single line of 600 `a` characters
  terminated by `\n`. Server aborted with:
  `AddressSanitizer: stack-buffer-overflow ... WRITE of size 513 ... in ReadBlock IPCDirect.c:206`,
  crashing the entire single-process server and disconnecting every connected player. On a
  non-ASan production build this is a stack smash with attacker-controlled content, not merely a
  clean crash — the severity should be treated as **at least remote DoS, and a memory-corruption
  primitive pending further exploitability analysis** (not attempted in this review).
- **Remediation**: Make the `UserInput`/`pkt->pa_Data` size relationship correct — either cap the
  line-completion threshold at `511` (`InputPos[u]==511`) so the resulting string plus terminator
  never exceeds 512 bytes, or use `strncpy`/an explicit bounded copy into `pa_Data` sized to its
  actual capacity (`sizeof(pkt->pa_Data)-1`) instead of `strcpy`. Given how central this code path
  is, replacing this and the handful of other raw `strcpy`/`sprintf` calls onto fixed network
  buffers (see M2 below) with bounded equivalents throughout `IPCDirect.c`/`ComServer.c` is
  recommended as a single pass.
- **Status**: **Confirmed** (reproduced with ASan; see [build-and-run.md](build-and-run.md) §4.1 for
  reproduction steps).
- **Fixed:** 2026-09-20, see branch worktree-fix-critical-high-findings, commit(s) 9c85e9e, 90b3a17.

### C2 — Privilege escalation: wizard status is a hardcoded, self-registerable display name

- **File/function**: `ArchWizard()` / `Arch()` macro, [SysSupport.c:183-200](../SysSupport.c),
  [System.h:585](../System.h); reached via ordinary registration in
  [ComDriver.c](../ComDriver.c) `Name_Got`/`CreatePersona`.
- **Behavior**: `ArchWizard(item)` grants full administrative/wizard privileges (every `Cmd_*`
  world-editing command, the `:` verb-check bypass, `EditTable`, `SetPFlag`, `Debugger`, `Exorcise`,
  etc. — essentially all ~90 built-in admin verbs) to any item whose **current display name**
  case-insensitively equals one of seven hardcoded literals: `"Anarchy"`, `"Debugiit"`,
  `BOSS1`=`"Arashi"`, `BOSS2`=`"Hobbit"`, `BOSS3`=`"Debugger"`, `BOSS4`=`"Bonzo"`,
  `BOSS5`=`":Boss"` (plus, via the separate `Arch()` macro, the build-time author-credit string
  `CPRT`=`"Alan Cox"`). The new-account registration flow's only name-collision check is against the
  **vocabulary word list** (`FindInList(WordList, name, WD_NOUN/...)`) — the wizard names are
  **not** pre-registered as vocabulary words anywhere in the `INIT` bootstrap
  ([ComDriver.c:606-762](../ComDriver.c)), so they are not rejected by that check. (`BOSS5` is
  `":Boss"`, which contains a non-letter character; the registration name filter in `Name_Got`
  rejects any name containing non-letter characters, so `BOSS5` specifically cannot be reached via
  ordinary registration — but the other six literal names contain only letters and remain directly
  reachable.)
- **Why it's a problem**: On a freshly-booted game (or any game where none of these six literal names
  has ever been registered), any anonymous connection can simply **register a brand-new character
  named `Arashi`** (or any of the other five) through the completely ordinary sign-up flow and
  immediately have full administrative control over the game world — no separate wizard
  authentication, invite, or flag exists. Even on an established game where the "real" `Arashi` is
  already registered, this remains a name-squatting/impersonation risk in combination with **C3**
  below (an abandoned login attempt can free up a name reservation out from under a currently-active
  player).
- **Failure scenario**: A player connects, registers as `Debugger` (a plausible, unremarkable name
  choice by coincidence or intent), and now has `ArchWizard()==true` for every command they type —
  able to delete rooms, edit any player's stats/flags, load/delete BSX assets, and force a
  `SaveUniverse` of a world they've just corrupted.
- **Remediation**: Wizard/admin status must not be derived from a player-controllable display name.
  Replace with a persistent, non-player-editable flag on the `UFF`/player record (e.g. a dedicated
  bit in `uff_Flag[]`, checked by `ArchWizard()` instead of/in addition to name comparison), and
  reserve the legacy names as pre-registered vocabulary words at boot so they can never be chosen by
  a new registration regardless.
- **Status**: **Confirmed by code reading** (the registration flow, the `ArchWizard` name list, and
  the absence of these names from the `INIT` vocabulary bootstrap were all directly verified by
  reading the source). **Not independently re-verified with a live exploit connection in this
  review session** — the general registration flow was already validated end-to-end with other
  names (see [build-and-run.md](build-and-run.md) §4.1), so completing the same flow with a reserved
  name would very likely succeed identically, but that specific run was not repeated live in this
  pass; recommend maintainers confirm in a controlled/offline environment before treating this as
  fully runtime-verified.
- **Fixed:** 2026-09-20, see branch worktree-fix-critical-high-findings, commit(s) 86f4d1e.

### C3 — Abandoned login/registration strips another player's active name reservation

- **File/function**: `RemoveUser()`, [SysSupport.c:288-315](../SysSupport.c), specifically the
  `FreeWord(UserList[u].us_Name, WD_NOUN)` call, which sits **outside** the
  `if(UserList[u].us_Item!=NULL)` guard and is instead only conditioned on
  `if(*UserList[u].us_Name)`.
- **Behavior**: `Name_Got()` ([ComDriver.c:224-302](../ComDriver.c)) copies the typed name into
  `UserList[u].us_Name` immediately, **before** any password/registration is completed and
  **before** `AddWord()` is ever called for that slot (`AddWord` only happens later, in
  `Check_Password`/`CreatePersona`, once login/registration fully succeeds). If the connection is
  then dropped (client disconnect, timeout, or the connection-slot-full path) while still mid-flow —
  at `AWAIT_PASSWORD`, `AWAIT_SETSEX`, `AWAIT_EMAIL`, or `AWAIT_PWSET` — `RemoveUser()` runs with
  `us_Item==NULL` (no player object was ever created) but `us_Name` still set to whatever the client
  typed. Because the `FreeWord` call only checks `*us_Name`, not `us_Item`, it unconditionally
  removes a `WD_NOUN` vocabulary entry for that exact name — **even though this session never
  registered that word itself**.
- **Why it's a problem**: If another, unrelated, currently-logged-in player happens to hold that
  exact name (their own `AddWord` having run at their own successful login), this abandoned session's
  disconnect **deletes that other player's name reservation** out of `WordList`. The victim's live
  session and `ITEM` are untouched, but the game no longer considers their name "taken" — reopening
  the door for a third party to register or log in under that same name while the original owner is
  still connected.
- **Failure scenario (reproduced in this review)**:
  1. Session A registers and logs in as `Bob` (full flow, succeeds) — `AddWord("Bob",...,WD_NOUN)`
     runs, `Bob` is now reserved.
  2. Session B connects, types `Bob` at the name prompt (reaching `AWAIT_PASSWORD`, since `Bob`
     already has a UAF-backed persona), then disconnects without sending a password.
  3. `RemoveUser()` for B's slot calls `FreeWord("Bob", WD_NOUN)`, removing the reservation A is
     still actively using.
  4. Session C connects and successfully completes **a brand-new registration** for `Bob` (the
     vocabulary collision check now passes, since the word was just freed), receiving its own
     distinct player `ITEM` also named `Bob`, while A's original session remains connected and
     unaware. The game now has two simultaneous, distinct items both resolving to the name `Bob`, and
     any subsequent command, message, or lookup that resolves "Bob" by name (`FindMaster` et al.) is
     ambiguous and will silently pick whichever one is first in `ItemList`.
  - Verified end-to-end against the built server in this review; see
    [build-and-run.md](build-and-run.md) §4.1.
- **Remediation**: Only call `FreeWord` if this session actually added the word in the first place
  (e.g. track "did AddWord run for this slot" alongside `us_Name`, or simply move the `AddWord` call
  earlier so it's symmetric with an equally-scoped `FreeWord`, or don't `FreeWord` at all unless
  `us_Item!=NULL` was true at some point in this session's lifetime). A defense-in-depth follow-up:
  `Name_Got` should also reject (or at least flag) an abandoned-then-reclaimed name collision against
  a still-connected `UserList[]` entry, not only against `WordList`.
- **Status**: **Confirmed** (reproduced live against the built server, see
  [build-and-run.md](build-and-run.md) §4.1).
- **Fixed:** 2026-09-20, see branch worktree-fix-critical-high-findings, commit(s) dafb731, 6cc487f.

### C4 — Codebase does not compile on a modern standards-conformant C toolchain

- **File/function**: `SaveLoad.c`, `LibSocket.c`, `Socket.c`, `UserFile.c` (all four declare
  `extern int errno;` instead of `#include <errno.h>`); `Main.c` (K&R-style signal handler
  declarations vs. ANSI prototypes pulled in via `Prototype.h`); `TableDriver.c`/`TabCommand.c`
  (`SendItem()` called with variable argument counts against a mismatched fixed-arity prototype).
- **Behavior**: Building `server` with a stock modern GCC (`gcc -Wall -pedantic`, no `-std=`
  override, as the [Makefile](../Makefile) specifies) fails to link
  (`errno: TLS definition in /usr/lib/libc.so.6 ... mismatches non-TLS reference`) and, before that,
  fails to compile several translation units outright over prototype/argument-count conflicts.
- **Why it's a problem**: The project is unbuildable out of the box on essentially every current
  Linux distribution's default toolchain, which blocks anyone trying to build, evaluate, patch, or
  deploy this codebase today without first discovering and applying fixes themselves.
- **Failure scenario**: Confirmed directly — a plain `make server` in this repository, on this
  review's environment (GCC 16.2.1), fails with the errors above before producing a binary.
- **Remediation**: (1) Replace all four `extern int errno;` declarations with `#include <errno.h>`.
  (2) Convert `Main.c`'s signal handlers and any remaining K&R-style function declarations that
  conflict with their ANSI prototypes to proper ANSI signatures, or consistently build with a
  compiler flag (`-std=gnu17` or similar) that tolerates the older style, as a stopgap. (3) Audit
  and fix arity mismatches between K&R-style function definitions and their ANSI prototypes
  (`SendItem` and any similarly-declared varargs-via-fixed-params functions). Full detail and the
  exact patch used to validate a working build in this review: [build-and-run.md](build-and-run.md)
  §3–4.
- **Status**: **Confirmed** (the unmodified repository was built exactly as shipped and reproduced
  every error listed; a minimal patch to a scratch copy — not committed to this repository — was
  then validated to produce a working `server` binary).
- **Fixed:** 2026-09-20, see branch worktree-fix-critical-high-findings, commit(s) f8fbca1.

## High

### H1 — Player passwords are stored and compared as short, unhashed cleartext

- **File/function**: `struct UserFileFormat.uff_Password[8]` ([System.h:578](../System.h)),
  written/read verbatim by `WriteRecord`/`ReadRecord` ([UserFile.c](../UserFile.c)); compared in
  `Check_Password()` via `strncmp(pwentry, LoginUFF.uff_Password, 7)`
  ([ComDriver.c:334](../ComDriver.c)).
- **Behavior**: Passwords are stored as raw bytes in the `UAF` file (no hashing, no salting), capped
  at 8 bytes total, and the login check only compares the **first 7 bytes** of that field
  (`strncmp(...,7)`), not the full 8 — so any two passwords sharing the same first 7 characters
  are treated as equal at login (the 8th byte is stored but never actually checked at the login
  gate; it is written in full by `PWNewVerify`/`Act_Save`, just never verified in full on the way
  back in).
- **Why it's a problem**: Anyone with filesystem read access to `UAF` (an operator, a
  misconfigured backup, a separate compromise) recovers every player's password directly, in the
  clear — this is also exactly what the standalone `FindPW` tool does by design
  ([FindPW.c](../FindPW.c)). The 7-of-8-byte comparison additionally weakens the effective password
  space slightly further than the already-short 8-byte field implies.
- **Failure scenario**: A backup of `UAF`, or output from `FindPW`, leaking anywhere (misconfigured
  permissions, a copied backup, a compromised operator account) discloses every player's real
  password in plaintext, which — given common password reuse — is a credential-stuffing risk well
  beyond this one game.
- **Remediation**: Store a salted password hash (even a decades-old scheme like crypt() with a
  per-record salt would be a substantial improvement over the current plaintext scheme) instead of
  the raw password bytes, and compare the full stored value, not a truncated prefix. This is a
  file-format-breaking change and should be versioned via the existing `Load_Format` mechanism (see
  [persistence.md](persistence.md) §1.2).
- **Status**: **Confirmed** by reading `UserFile.c`, `ComDriver.c`, and `FindPW.c` directly.
- **Fixed:** 2026-09-20, see branch worktree-fix-critical-high-findings, commit(s) 35fa63a.

### H2 — Action-table item-pointer operands silently truncate on 64-bit builds

- **File/function**: `SetTwo`/`GetTwo`, [SaveLoad.c:99-116](../SaveLoad.c) (mirrored in
  `CompileTable.c` for the same purpose during compilation).
- **Behavior**: A literal item reference embedded in compiled action-table bytecode is packed into
  **two 16-bit words** (32 bits total) by treating a `char *`/`ITEM *` as an `unsigned long` and
  splitting it via `%65536`/`/65536`. This only round-trips correctly when
  `sizeof(ITEM*) <= 4`. Measured in this review's build environment: `sizeof(void*) == 8` (standard
  LP64 Linux/x86-64), so any pointer value with nonzero bits above bit 31 is silently truncated —
  confirmed by direct measurement (a `malloc`'d heap pointer's low 32 bits did not round-trip
  through this exact truncate/reconstruct pattern in a standalone test compiled in this review).
- **Why it's a problem**: Any action-table line that embeds a literal item pointer as an operand
  (rather than resolving an item dynamically by parsed noun/adjective at runtime) can silently
  resolve to the **wrong item**, a dangling/invalid pointer, or a crash, on any 64-bit build —
  without any error or warning, since the truncation is silent arithmetic, not a detected fault.
- **Failure scenario**: A wizard writes or the compiler emits a table line hardcoding a specific
  item reference (`{$item}`-style literal item operand); on a 64-bit server, the compiled bytecode's
  stored "pointer" is really just the low 32 bits of the real address, and whatever the interpreter
  does with `GetTwo()`'s reconstructed value at execution time operates on a bogus pointer —
  `CheckItem()`'s `ICheck()` (`System.c`, only active when `CHECK_ITEM` is defined, which it is by
  default in `System.h`) would likely catch this as an "Invalid ITEM *" and crash via `Error()`
  rather than silently corrupting memory, which is a partial mitigation, but represents server
  instability triggerable by ordinary content authoring.
- **Remediation**: Store a full-width (64-bit-safe) representation — either the item's ordinal
  position in `ItemList` (the same portable scheme already used for the structural item graph itself,
  `SaveItem`/`LoadItem`) rather than a raw address, or an explicit `int64_t`/`intptr_t`-sized pack/
  unpack pair instead of two 16-bit halves. Both the `CompileTable.c` compile-time packer and the
  `SaveLoad.c` save/load packer need the same fix, kept in lock-step.
- **Status**: **Confirmed by code reading and pointer-size measurement**. **The runtime
  consequence (an actual wrong-item resolution or crash from this code path) was not independently
  reproduced** in this review — doing so would require game content with a literal item-pointer
  table operand, which this review did not have available (no populated universe file was present
  in the repository); see [persistence.md](persistence.md) §4 for the open question about how common
  this pattern is in typical game content.
- **Fixed:** 2026-09-20, see branch worktree-fix-critical-high-findings, commit(s) ed76364.

### H3 — Missing tests and CI entirely

- **File/function**: repository-wide — confirmed by `find`/`git ls-files`: no test directory, no
  test harness, no CI configuration (no `.github/`, no equivalent) anywhere in this repository.
- **Why it's a problem**: A ~22,500-line C codebase with pervasive manual memory management, a
  custom bytecode interpreter, and a hand-rolled network protocol has zero automated regression
  coverage. Every finding in this document (including the two reproduced crashes, C1 and C3) would
  have been caught immediately by even a minimal smoke-test harness (connect, send a long line,
  assert no crash; register two sessions under the same name, assert the second is rejected).
  Without tests, every future change — including fixes for the findings in this document — risks
  silent regressions.
- **Failure scenario**: Any future contributor's fix to, say, the parser or the save format has no
  automated way to confirm it didn't break login, movement, or persistence, and must be manually
  re-verified by hand every time — exactly the ad hoc process this review had to follow to produce
  its own findings.
- **Remediation**: At minimum, a scripted smoke-test harness (comparable to the Python test scripts
  used to validate this review's findings) that drives the built `server` binary over TCP through
  login, a few commands, save/load, and disconnect, run in CI on every change. Unit-level coverage
  for the pure-logic pieces (`Parser.c` tokenization, `CompileTable.c`/table bytecode round-trips,
  `UserFile.c` endianness swap functions) would be comparatively cheap to add and high-value.
- **Status**: **Confirmed** (absence directly verified by repository search).
- **Fixed:** 2026-09-20, see branch worktree-fix-critical-high-findings, commit(s) db833f7 onward.

## Medium

### M1 — `PWNew`/`PWNewVerify` can read one uninitialized/out-of-bounds heap byte

- **File/function**: `PWNew()`/`PWNewVerify()`, [ActionCode.c:1303-1319](../ActionCode.c).
- **Behavior**: `PWNew` allocates exactly 9 bytes (`malloc(9)`, not zeroed) and
  `strncpy(UserList[u].us_UserPtr, v, 8)` into it. `strncpy` only null-terminates the destination if
  the source is shorter than the copy count — if the player's typed new password is **8 or more
  characters**, byte index 8 (the 9th, final byte of the allocation) is left as whatever `malloc`
  happened to return, not necessarily `0`. `PWNewVerify` later runs `strcmp(v,
  UserList[u].us_UserPtr)` against that same buffer, which will read past the intended 8-byte
  password if that final byte is non-zero, until it happens to find a zero byte somewhere in
  adjacent heap memory.
- **Why it's a problem**: A one-byte (at minimum) heap over-read, content-dependent on unrelated
  heap allocator state — at best causes a spurious password-mismatch (comparing against garbage), at
  worst is a (small, hard-to-trigger-usefully) out-of-bounds read.
- **Failure scenario**: A player changing their password to any 8+ character string via the in-game
  `Password` command flow (`AWAIT_PWNEW`) has a chance the confirmation step (`PWNewVerify`)
  spuriously reports "Incorrect" even though they typed the same string twice, depending on
  allocator behavior.
- **Remediation**: `malloc(9)` → `calloc(1,9)` (or explicitly zero the 9th byte after allocation), or
  simply size the destination correctly and cap/validate the source length before copying.
- **Status**: **Confirmed by code reading**; not independently reproduced live in this review (would
  require a specific heap-layout-dependent trigger not attempted here).

### M2 — Unbounded `%s` scan into a fixed field during login parsing

- **File/function**: `Handle_Login()`, [ComServer.c:169](../ComServer.c) and
  [ComServer.c:203](../ComServer.c) (`sscanf(msg,"%ld$%s",&v,ubf)`), feeding
  [ComServer.c:221](../ComServer.c) (`sprintf(UserList[ct].us_UserName,"Internet:%s",ubf)`).
- **Behavior**: `ubf` is a 96-byte local buffer (`char ubf[MAXUSERID+64]`), but the `sscanf("%s")`
  read into it is unbounded (no width specifier), and the subsequent `sprintf` writes `"Internet:"`
  (9 bytes) plus that field into `UserList[ct].us_UserName`, which is only `MAXUSERID+1` = **33
  bytes**. GCC's own `-Wformat-overflow` flags this exact call
  ("`'%s' directive writing up to 95 bytes into a region of size 24`" — the compiler's more precise
  bound calculation).
- **Why it's a problem**: If the userid field of a `PACKET_LOGINREQUEST` message were ever longer
  than ~23 characters, this overflows `us_UserName`. In the *current* codebase, this message is only
  ever synthesized server-side by `MakeConnection()` ([IPCDirect.c:781](../IPCDirect.c)) from a
  short, bounded dotted-quad IP string, so it is **not exploitable through the default network path
  as shipped** — but the parsing function itself makes no attempt to bound the field regardless of
  where the message came from, so this is latent, not neutralized by design.
- **Failure scenario**: Any future change that lets `PACKET_LOGINREQUEST` content be influenced by
  something longer than an IPv4 address (an IPv6 address string is already longer than
  `NetName()` currently produces headroom for at 33 bytes total field size — worth checking
  explicitly if IPv6 support is ever added, since `NetName()` itself is IPv4-only, see L1) would
  reintroduce a directly reachable overflow here.
- **Remediation**: Bound the `sscanf` field width explicitly (`%95s` matching `ubf`'s actual size),
  and use `snprintf` with an explicit size cap for the `sprintf` into `us_UserName` regardless.
- **Status**: **Confirmed by code + compiler warning**; not independently exploited live in this
  review (current call path is not attacker-reachable with a long enough value, as noted above).

### M3 — No file locking between the live server and the offline `Reg`/`FindPW` tools

- **File/function**: `UserFile.c` (`OpenUAF`/`CloseUAF`, all read/write paths); `Reg.c`, `FindPW.c`.
- **Behavior**: No `flock`/`fcntl` locking anywhere around `UAF` access. The live `server` process
  is internally safe (single-threaded, one call site active at a time), but `Reg`/`FindPW` are
  separate processes that open and read/write the same file independently.
- **Why it's a problem**: Running `Reg`/`FindPW` while `server` is live and actively writing (e.g.
  during a player's `Password` change or death-save) is a classic lost-update/torn-write race —
  each process's `fseek`+`fread`/`fwrite` sequence assumes exclusive access it does not actually
  have.
- **Failure scenario**: An operator runs `./Reg somebody` to mark a registration approved at the
  same moment a different player's `SavePersona` write lands on disk; depending on exact timing this
  can corrupt either record or silently lose one of the two updates.
- **Remediation**: Add `flock(fileno(f), LOCK_EX)` (or equivalent) around the open/read/write/close
  sequence in `UserFile.c`'s shared helpers, used consistently by `server`, `Reg`, and `FindPW`
  alike.
- **Status**: **Confirmed by code reading** (absence of any locking call is unambiguous); not
  reproduced as an actual corrupted file in this review (would require deliberately racing two
  processes against the same file, not attempted).

### M4 — `Run_Aber` supervisor never passes the universe filename through

- **File/function**: [Run_Aber.c:39-43](../Run_Aber.c) (`execvp("./server",&argv[0])`).
- See [runtime-flows.md](runtime-flows.md) §8 and [operations.md](operations.md) §3 for full detail.
- **Why it's a problem**: Every server restart under `Run_Aber` supervision silently boots an empty
  world unless something else not present in this repository re-supplies the argument.
- **Failure scenario**: An operator deploys `Run_Aber` expecting it to be a drop-in crash-resilient
  supervisor for their existing game; the first crash-and-restart cycle "loses" the entire game world
  from the players' perspective (server comes back up with nothing in it), even though the actual
  universe file on disk is untouched and fine — this is a supervision bug, not a data-loss bug, but
  it manifests identically to one from an operator's perspective.
- **Remediation**: Have `Run_Aber` accept and forward its own `argv[1:]` (or a configured universe
  path) to the `execvp("./server", …)` call.
- **Status**: **Confirmed by code reading.**

### M5 — No automatic periodic save / no graceful shutdown

- Covered in full in [persistence.md](persistence.md) §1.4 and [operations.md](operations.md) §4.
- **Why it's a problem**: All world state changes since the last explicit `SaveUniverse` (or
  `Act_ForkDump`) are lost on any crash or unplanned restart; there is no drain/quiesce shutdown path
  (`Cmd_Abort`/`Act_Abort` both call `exit(0)` immediately), so any restart — planned or not —
  disconnects every player without warning.
- **Remediation**: Add an operator- or timer-triggered periodic `SaveSystem()` call (e.g. as a
  standing action-table `WHEN`, or a small addition to the main loop), and a graceful-shutdown signal
  handler that broadcasts a warning, saves, and then exits.
- **Status**: **Confirmed by code reading** (absence of any periodic-save or graceful-shutdown call
  site verified by exhaustive grep of `SaveSystem`/`exit(` call sites).

## Low

### L1 — Stale, institution-specific dead code: hardcoded IP block-list

- **File/function**: `MakeConnection()`, [IPCDirect.c:784-797](../IPCDirect.c) (comment: "SWANSEA
  STUFF - PLEASE ENFORCE ELSEWHERE TOO...").
- **Behavior**: Two literal, hardcoded IPv4 addresses are rejected unconditionally, with a comment
  suggesting this was a specific historical abuse-mitigation for the original Aberystwyth-hosted
  instance, decades ago.
- **Why it's a problem**: Misleading/dead institutional cruft — not a general access-control
  mechanism, provides no real security value today, and its own comment flags that it was always
  meant to be supplemented elsewhere (which evidently never happened, since no other enforcement of
  it exists in this codebase).
- **Remediation**: Remove it, or replace with a proper, configurable IP allow/deny-list mechanism if
  access control at this layer is still wanted.
- **Status**: **Confirmed by code reading.**

### L2 — Build references to files that do not exist in this repository

- **File/function**: [Makefile](../Makefile) `CFILES`/`docs`/`client` targets referencing
  `Client.c`, `runaber.c`, `IPCClean.c`, `NoPrototype.h`, and `DOC/*`.
- Full detail: [build-and-run.md](build-and-run.md) §2.
- **Why it's a problem**: `make all` (the top-level convenience target) fails outright because
  `docs` is a listed prerequisite and `DOC/` doesn't exist; the stale `CFILES`/`NoPrototype.h`
  references are silently harmless today (unused by the targets that actually get built) but will
  confuse the next person trying to understand what's supposed to exist.
- **Remediation**: Either restore the missing files (`client`, `DOC/`) if they're still wanted, or
  remove/comment out the corresponding Makefile targets and stale filename references so the
  Makefile accurately reflects what's actually in the repository.
- **Status**: **Confirmed by code reading + build attempt.**

### L3 — `AWAIT_*` session-state constants duplicated verbatim in two headers

- **File/function**: [Comms.h](../Comms.h) and [User.h](../User.h) both define the full
  `AWAIT_LOGIN`..`AWAIT_EMAIL` constant set, identically.
- **Why it's a problem**: Any future change to one copy (adding a state, renumbering) without the
  identical change to the other silently desynchronizes session-state handling across the codebase,
  with no compiler warning (both headers are included in different combinations by different
  translation units).
- **Remediation**: Define the `AWAIT_*` constants in exactly one header (`User.h` is the more logical
  home, since they describe `USER.us_State`) and have the other include it, or remove the duplicate
  entirely.
- **Status**: **Confirmed by direct comparison of both headers.**

### L4 — `PACKET_BSXOBJECT` defined but never used as a distinct protocol case

- **File/function**: `PACKET_BSXOBJECT` (200... actually 201, [Comms.h](../Comms.h)) vs. its absence
  as a `switch` case anywhere in `ComServer.c`/`IPCDirect.c`/`BSX.c` (BSX object placement instead
  reuses `PACKET_BSXSCENE` framing with embedded `@VIO`/`@RMO` tokens).
- **Why it's a problem**: Minor, but a reader implementing a new BSX-aware client or extending the
  protocol could reasonably expect this constant to be meaningful/handled somewhere and waste time
  looking for its dispatch.
- **Remediation**: Remove if genuinely unused, or document explicitly as reserved-for-future-use if
  intentional.
- **Status**: **Confirmed by exhaustive grep for `PACKET_BSXOBJECT` across the tree** (only the
  definition site matches).

### L5 — Assorted compiler-flagged minor issues (unused variables, pointer/int size casts)

- **File/function**: `CompileTable.c` (five `-Wpointer-to-int-cast` warnings around
  [CompileTable.c:492-839](../CompileTable.c), part of the same pointer-packing pattern as H2, but
  in the *compiler* rather than the save/load path — same remediation applies), `Snoop.c:103`,
  `LookFor.c:47`, `BSX.c:368` (unused-but-set variables), `NewCmd.c:154` (`%d` format given a
  `size_t` argument).
- **Why it's a problem**: Individually cosmetic (none were observed to cause incorrect behavior in
  this review), but collectively indicate the codebase has not been kept clean against a modern
  compiler's warning set, making genuinely new problems harder to spot in the noise.
- **Remediation**: Address as routine cleanup once the Critical/High findings above are resolved;
  low urgency on their own.
- **Status**: **Confirmed by compiler warnings** during this review's build (see
  [build-and-run.md](build-and-run.md) §3).

## Summary table

| # | Title | Severity | Status |
|---|---|---|---|
| C1 | Remote unauthenticated stack buffer overflow in `ReadBlock` | Critical | Confirmed (ASan-reproduced) |
| C2 | Wizard privilege via self-registerable hardcoded name | Critical | Confirmed by code reading |
| C3 | Abandoned login strips another player's name reservation | Critical | Confirmed (reproduced live) |
| C4 | Codebase does not build on a modern C toolchain | Critical | Confirmed (build attempted) |
| H1 | Cleartext, truncated-comparison passwords | High | Confirmed by code reading |
| H2 | 64-bit pointer truncation in table item operands | High | Confirmed by code + measurement |
| H3 | No tests, no CI | High | Confirmed |
| M1 | `PWNew` uninitialized/OOB heap byte | Medium | Confirmed by code reading |
| M2 | Unbounded `%s` into fixed `us_UserName` field | Medium | Confirmed by code + compiler warning |
| M3 | No locking between server and `Reg`/`FindPW` on `UAF` | Medium | Confirmed by code reading |
| M4 | `Run_Aber` drops the universe filename argument | Medium | Confirmed by code reading |
| M5 | No periodic save / no graceful shutdown | Medium | Confirmed by code reading |
| L1 | Dead "Swansea" IP block-list | Low | Confirmed |
| L2 | Makefile references nonexistent files | Low | Confirmed |
| L3 | Duplicated `AWAIT_*` constants | Low | Confirmed |
| L4 | Unused `PACKET_BSXOBJECT` constant | Low | Confirmed |
| L5 | Minor compiler warnings (dead vars, format, pointer casts) | Low | Confirmed |
