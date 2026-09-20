# AberMUD 5.30 — Persistence

Back to [architecture.md](architecture.md). Related: [data-model.md](data-model.md),
[review-findings.md](review-findings.md).

There are **two independent, unsynchronized** persistence stores. Neither knows about the other's
transaction boundaries — see §4 for the correctness consequence.

## 1. The universe file (`SaveLoad.c`)

### 1.1 What's in it

`SaveSystem(filename)` / `LoadSystem(filename)` ([SaveLoad.c:1090-1155](../SaveLoad.c)) serialize,
in this order:

1. **Header** (`WriteHeader`/`ReadHeader`): item count, **format version** (written as `10` in this
   release, historically incremented whenever the on-disk shape changes — see the changelog comment
   at [SaveLoad.c:330-333](../SaveLoad.c)), a save timestamp, and one reserved/unused long.
2. **Every item** in `ItemList` (`SaveObject`/`LoadObject`), including all attached substructures
   (`SaveSub`/`LoadSub`, keyed the same way as in-memory `it_Properties`, see
   [data-model.md](data-model.md) §2).
3. **All action tables** (`SaveAllTables`/`LoadAllTables`) — full compiled bytecode, not source.
4. **The vocabulary** (`SaveVocab`/`LoadVocab`) — the entire `WordList`.
5. **512 numeric flags** (`SaveFlag`/`LoadFlag` × 512) and **16 classes**
   (`SaveClass`/`LoadClass` × 16) — global game-state scratch arrays used by action-table code
   (`FlagData[512]`, `TableDriver.c`) and the class-naming table (`Class.c`).
6. **Bit-flag names** (`SaveBitFlags`/`LoadBitFlags`) — the human-readable names assigned to each of
   the 16 room/object/player/container flag bits (`RBitNames`/`OBitNames`/`PBitNames`/`CBitNames`).
7. **BSX image data** (`SaveBSXImages`/`LoadBSXImages`) — every loaded `BSXImage` blob.

### 1.2 Format-version gating on load

`LoadSystem` conditionally skips newer sections based on `Load_Format` (the version value read from
the header), so **older universe files remain loadable**:

```
format < 1              -> rejected outright ("too old")
format > 10              -> rejected outright ("beyond my knowledge, you need a newer SERVER")
format > 1  (>=2)        -> load flags
format > 2  (>=3)        -> load classes
format > 4  (>=5)        -> load bit-flag names
format > 9  (==10)       -> load BSX images
```

This is a real, working forward-compatibility mechanism — confirmed by reading `ReadHeader` and the
gated calls in `LoadSystem` — but it only guards *whether a section is present*, not per-field
migration within a section; a structural change to, say, `UserFileFormat` or a `Sub_*` struct's
field layout would need a bump to this same version number and a corresponding conditional load
path, which the in-code comment at [System.h:479-486](../System.h) explicitly recommends as the
preferred approach for future substructure changes (new `KEY_*` id rather than reusing an old one
with incompatible layout).

### 1.3 Pointer encoding — items vs. table bytecode (different schemes!)

- **Item cross-references** (e.g. `GENEXIT.ge_Dest[]`, `CONTAINER` links, `it_Parent`) are saved as
  the referenced item's **ordinal position** in `ItemList` (`MasterNumber`, `SaveItem`/`LoadItem`),
  resolved back through the `ItemArray[]` built at `ReadHeader` time. This is portable across
  separate process runs and independent of pointer width/address space layout — confirmed correct
  for the item graph itself.
- **Action-table bytecode operands** that reference a literal item (used when a table hardcodes
  `$item` rather than a parsed noun) instead embed the **raw in-memory `ITEM *` pointer**, packed
  into two 16-bit words via `SetTwo`/`GetTwo` (`SaveLoad.c:99-116`, mirrored in `CompileTable.c` for
  the same purpose at compile time):

  ```c
  static void SetTwo(unsigned short *x, char *v) {
      x[1] = ((unsigned long)v) % 65536;
      x[0] = ((unsigned long)v) / 65536;
  }
  static long GetTwo(unsigned short *x) { return x[1] + 65536L * x[0]; }
  ```

  This only round-trips correctly if `sizeof(ITEM*) <= 4 bytes` (32-bit or smaller address space) —
  see [review-findings.md](review-findings.md) for the confirmed 64-bit consequence. Loaded item
  bytecode pointers are **not fixed up against `ItemArray[]`** the way `SaveItem`/`LoadItem`
  references are — they are used as-is after `GetTwo` reconstitutes them, meaning they are only
  even *conceptually* valid across a save/load boundary in the first place because CPython's/this
  process's heap allocator happened to reuse the same addresses, which in general it does not. This
  is a second, independent risk from the truncation problem: **unconfirmed how far this is actually
  exercised** in typical game content (most tables reference items via parsed noun/adjective, not
  literal embedded pointers), but the mechanism exists and is reachable from the table compiler and
  from `CompileTable.c`'s own use of `SetTwo`.

### 1.4 Save triggers and backup behavior

`SaveSystem()` ([SaveLoad.c:1090](../SaveLoad.c)) renames any pre-existing file at the target path
to `<name>.bak` (via `unlink()` + `rename()`, not an atomic swap) before writing the new file, giving
one level of rollback. Triggers, confirmed by call site:

- In-game `SaveUniverse` command (`Cmd_SaveUniverse`, verb `20`).
- `Act_ForkDump` — forks a child process, logs out all connected users *in the child only*, then
  saves; the parent process and its live connections are unaffected. This is the closest thing to a
  "hot backup" mechanism in this codebase.
- `ErrFunc()`'s crash-rescue path — one-shot `SaveSystem("gonebang.uni")` attempt on the first fatal
  `Error()` call after boot (see [architecture.md](architecture.md) §6).

**There is no automatic periodic save** found anywhere in this codebase (no cron-like table or timer
calling `SaveUniverse`/`SaveSystem` on an interval) — confirmed by grepping all `SaveSystem`/
`Cmd_SaveUniverse` call sites. Any such behavior would have to be configured as game content (an
action table with a recurring `WHEN`), and even then would be subject to the event-queue loss
described in §3 below across restarts. **Operationally this means periodic saves are the operator's
responsibility** — see [operations.md](operations.md).

### 1.5 What is *not* persisted with the universe

- **The pending timer/`WHEN` event queue** (`EventList`, `TimeSched.c`) — entirely in-memory,
  confirmed by the absence of any `EventList` reference in `SaveLoad.c`. All scheduled-but-not-yet-
  fired daemon timers are silently lost on every restart, regardless of cause (planned restart via
  `Run_Aber`, crash, `SaveUniverse` followed by a later restart, …).
- **Live session state** (`UserList[]`, connection sockets, parser pronoun context
  `ParserData[]`) — by nature of being a network/session concept, none of this crosses a save;
  every connected player is disconnected across a restart and must log back in.

## 2. Player account file (`UserFile.c`)

### 2.1 Layout

`UAF` ([System.h](../System.h) `USERFILE`) is a flat file of fixed-size `UFF` records
(`sizeof(UFF)`, currently 176 bytes on a 64-bit LP64 build per this review's build — see
[build-and-run.md](build-and-run.md) — though the struct itself does not pad to a fixed width
explicitly, so this size is **compiler/ABI dependent**, not a guaranteed on-disk constant across
different toolchains/architectures; the in-code warning at [System.h:558-564](../System.h)
explicitly calls this out and recommends a text-based conversion utility for cross-machine moves,
which this repository does not include).

- `LoadPersona(name, &UFF)` (`UserFile.c`) linear-scans the file from the start on every call
  (`FindRecord`/`ReadRecord` in a loop, `stricmp` against `uff_Name`), returning the matching
  record's ordinal index or `-1`. **O(n) per lookup, with n = total ever-registered characters,
  including "deleted" ones** (see §2.3) — every login, `Act_Save`, `Act_KillOff`, and
  `Act_NewText` call pays this cost.
- `SavePersona(&UFF, index)` seeks directly to the record (`FindRecord`) and overwrites it in place.
- `SaveNewPersona(&UFF)` calls `FindFreeRecord()` — itself `LoadPersona(" ", &dummy)`, i.e. it finds
  the *first record whose name is a single space* to reuse — see §2.3.

### 2.2 Endianness handling

`SwapUFFToNeutral`/`SwapUFFToHost` (`UserFile.c`) explicitly `htons`/`htonl`/`ntohs`/`ntohl` every
multi-byte numeric field around each disk write/read, so the `UAF` file's **numeric fields** are
portable across big-/little-endian hosts of the same struct layout — a real, working piece of
portability engineering, confirmed by reading both functions field-by-field. This does **not**
address structure padding/alignment differences (`sizeof(UFF)` itself, per §2.1) or the compiled
action-table bytecode's raw-pointer embedding (§1.3), so overall cross-machine portability is
partial, not complete, despite this one correctly-implemented piece.

### 2.3 "Deletion" is a blank-name sentinel, not a real delete

Dying/quitting a character in most build configurations (`Act_NewText`, `ActionCode.c`, gated by
`#ifdef REGISTER` vs not) sets `uff_Name[0]` to a single space and re-saves the record in place —
this both "frees" the slot for `FindFreeRecord()` reuse and is what the historical comment
"Dying does not delete the persona, only blanks it, so you can't kill someone and steal the name"
(README-5.30 changelog) documents as a deliberate anti-griefing measure. The consequence: the `UAF`
file only ever grows (blanked records are reused, but the file is never compacted/truncated), and
`LoadPersona`'s linear scan cost is proportional to the historical high-water mark of characters
ever created, not the number of live ones.

### 2.4 Concurrency

`UserFile.c` has **no file locking** (`flock`/`fcntl` locks) around its `fopen("r+")` / `fseek` /
`fread`/`fwrite` sequences. The live `server` process is the only writer during normal play, so
within a single running server this is safe by construction (single-threaded, one call site at a
time). The risk is **cross-process**: the standalone `Reg` and `FindPW` tools
([Reg.c](../Reg.c), [FindPW.c](../FindPW.c)) open and write/read the same `UAF` file independently,
with no coordination with a concurrently-running `server` — see
[review-findings.md](review-findings.md).

## 3. Interaction between the two stores

A player's live session state is a *hybrid* of both stores while connected: `UserList[u].us_Record`
holds the `UAF` record index, `UserList[u].us_Item` holds the live in-memory `ITEM*` (part of the
universe graph, saved with it), and specific fields (score, stats, flags, table bindings, password)
are duplicated into a local `UFF` struct and written back to `UAF` on save/logout events
(`Act_Save`, `Act_KillOff`, `Act_NewText`, and the login-time `Check_Password`/`CreatePersona`
paths that copy `UFF` fields *into* the new `ITEM`). **These two saves are never transactionally
linked** — `SaveUniverse` (universe file) and `Act_Save`/character-death (UAF file) are triggered by
entirely separate code paths with no shared commit point, so it is possible for the universe file
and a player's `UAF` record to disagree (e.g. a `SaveUniverse` capturing a player's current score
mid-session, followed by a crash before that player's next `Act_Save`, followed by a restart that
reloads the *older* `UAF` score into a *newer* universe's rebuilt player item — or the reverse). This
is an inherent property of the two-store design as implemented, not a single bug to fix — flagged as
a design-level persistence risk in [review-findings.md](review-findings.md).

## 4. Confirmed vs. unclear

**Confirmed**: file formats, save/load call graph and version gating, the two different pointer
persistence schemes and their differing correctness properties, absence of any locking on `UAF`,
absence of any automatic periodic save.

**Unclear / needs runtime verification**: how often, in practice, game content actually embeds
literal item-pointer operands in compiled tables (vs. always resolving via parsed nouns) — this
determines how exposed real game databases are to the §1.3 pointer-truncation risk; this review did
not have an existing populated universe file to inspect for this pattern.
