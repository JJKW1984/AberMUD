# AberMUD 5.30 — Communication Protocol

Back to [architecture.md](architecture.md). Related: [runtime-flows.md](runtime-flows.md).

## 1. Transport

Three plain TCP listening sockets are opened at boot by `CreateMPort()`
([IPCDirect.c:372-384](../IPCDirect.c)):

| Socket | Port | Purpose |
|---|---|---|
| Main | `SysPort` (default `TCP_PORT`=5000, [System.h](../System.h); override with `-p <port>`, `Main.c`, must be `>=5000`) | Primary telnet/plain-text client entry point |
| "Alt" | `SysPort+1` | A second, functionally-identical entry point — confirmed identical handling in `MakeConnection()`; **its intended distinguishing purpose is not documented in-code and is unconfirmed** |
| BSX | `SysPort+2` | Entry point for BSXmud graphical clients — connections here are tagged `UserState[u]=2` |

All three are handled by the same `select()` loop in `ReadMPort()`
([IPCDirect.c:559](../IPCDirect.c)); a readable listening fd is routed to `MakeConnection()`, a
readable client fd is routed to `ReadBlock()` via `FindUserFD`/`IsUserFD`.

There is no TLS; connections are unauthenticated plain TCP, with application-level username/password
auth happening in-band after connect (see [runtime-flows.md](runtime-flows.md) §2).

Basic Telnet option negotiation is stripped, not honored: any `IAC` byte seen by `ReadBlock`
consumes and discards the following `WILL`/`WONT`/`DO`/`DONT` option byte
([IPCDirect.c:157-169](../IPCDirect.c)) — the server never actually negotiates telnet options, it
just swallows the client's attempts. Server-initiated `IAC WILL/WONT ECHO` is sent for local echo
control (`PACKET_ECHO` case, state 0, [IPCDirect.c:484-495](../IPCDirect.c)).

## 2. Internal packet framing (`Comms.h`)

Even though this build is single-process, the code still frames all game-layer traffic as discrete
packets, defined in [Comms.h](../Comms.h):

```c
struct Com_Pack1 { short pa_Type; short pa_Sender; short pa_Data[4]; };   /* COMDATA — numeric */
struct Com_Pack2 { short pa_Type; short pa_Sender; char  pa_Data[512]; }; /* COMTEXT — text    */
```

`COMTEXT`/`COMDATA` are the in-memory shapes used by `SendTPacket()`/`SendNPacket()`
([ComServer.c:93-116](../ComServer.c)). A third struct, `PACKET` (`struct SysPacket`), exists with a
516-byte `pa_Data` and an optional leading `long pa_Type` under `MSGPORT` — this is the
message-port-era on-the-wire shape; in the Unix build packets never actually cross a socket in this
framed form (`WriteMPort`/`ReadMPort` operate on `COMTEXT`/line-oriented socket bytes directly), so
`PACKET` in `Comms.h` should be read as **legacy/vestigial** rather than the active wire format —
confirmed by grepping for `PACKET pa_` construction sites, of which there are none outside the
struct definition itself.

### 2.1 Packet types (`PACKET_*`, [Comms.h](../Comms.h))

| Constant | Value | Direction | Meaning |
|---|---|---|---|
| `PACKET_CLEAR` | 0 | either | Parser clear/shutdown a session; also used server→client to disconnect with a message |
| `PACKET_LOOPECHO` | 1 | either | Loopback echo test |
| `PACKET_ECHOBACK` | 2 | server→client | Reply to `PACKET_LOOPECHO` |
| `PACKET_LOGINACCEPT` | 3 | server→client | `p1`=control signal, `p2`=assigned user slot ref |
| `PACKET_CLEARED` | 4 | client→server (internal) | Confirms a requested clear completed |
| `PACKET_ECHO` | 5 | server→client | `p1=1` echo off (password entry), `p1=0` echo on |
| `PACKET_INPUT` | 6 | server→client | `p1=1` permits client to send its next line |
| `PACKET_LOGINREQUEST` | 128 | internal (synthesized by `MakeConnection`) | Text payload `SigCode:UserID:FilePath`-style string carrying source IP and fd-encoded flags |
| `PACKET_OUTPUT` | 129 | server→client | Plain text for display |
| `PACKET_COMMAND` | 130 | client→server | One line of player input |
| `PACKET_SETPROMPT` | 131 | server→client | Sets the prompt string |
| `PACKET_COMMFORCE` | 132 | client→server | Command that always runs from `AWAIT_COMMAND` context (e.g. menu-bar selections) regardless of current state |
| `PACKET_EDIT` | 133 | server→client | Push text into an edit buffer (BSX/menu-mode clients) |
| `PACKET_SNOOPTEXT` | 134 | server→client | Snooped session output relayed to a snooper |
| `PACKET_SETFIELD` | 135 | server→client | Field/column shift for output alignment |
| `PACKET_SETTITLE` | 240 | server→client | Client title-bar text (graphical clients) |
| `PACKET_BSXSCENE` | 200 | either | BSX scene/image request or push |
| `PACKET_BSXOBJECT` | 201 | — | Defined but not referenced as a distinct dispatch case in `ComServer.c`/`IPCDirect.c` — BSX object placement instead reuses `PACKET_BSXSCENE` framing with `@VIO`/`@RMO` tokens (`BSX.c` `Act_BSXObject`). **Confirmed unused as a literal `switch` case; treat as reserved/vestigial.** |
| `PACKET_ABORT` | 140 | client→server (internal) | Remote die request — terminates the whole process (`exit(0)`) |
| `PACKET_SUPERCEDE` | 141 | internal | Sent by a newly-started instance to tell an old one it's been replaced (dead on Unix, see [architecture.md](architecture.md) §4) |
| `PACKET_BONG` | 150 | internal | Synthetic "one second elapsed" tick; `GetPacket()` treats it as "no packet" (`-2`) purely to pace the scheduler |

### 2.2 Session state constants (`AWAIT_*`)

Defined **twice**, identically, in both [Comms.h](../Comms.h) and [User.h](../User.h) — see
[review-findings.md](review-findings.md) for the maintenance risk this duplication creates. Values:
`AWAIT_LOGIN`/`AWAIT_COMMAND`=0, `AWAIT_NAME`=1, `AWAIT_PASSWORD`=2, `AWAIT_PASSRETRY`=3,
`AWAIT_PWSET`=4, `AWAIT_SETSEX`=5, `AWAIT_TEDIT`=6, `AWAIT_EDLIN`=7, `AWAIT_PWVERIFY`=8,
`AWAIT_PWNEW`=9, `AWAIT_PWVERNEW`=10, `AWAIT_ACK`=0 (aliases `AWAIT_LOGIN`; comment marks the
literal `11` as unused), `AWAIT_OE1..OE8`=20..27 (`AWAIT_OEDIT`/`AWAIT_OEND` alias the first/last),
`AWAIT_EMAIL`=28.

## 3. `PACKET_LOGINREQUEST` payload format

Synthesized entirely server-side by `MakeConnection()` ([IPCDirect.c:758-808](../IPCDirect.c)) —
never sent by a real client — as:

```
Internet:<dotted-quad-ip>$<fd + 256*is_alt_port + 512*is_bsx_port>$*
```

`Handle_Login()` ([ComServer.c:143](../ComServer.c)) parses this with
`sscanf(msg,"%ld$%s",&v,ubf)` after splitting off the leading `Internet:<ip>` at the first `$`. The
encoded second field packs the accepted client fd plus two single-bit "which listening socket" tags
into one integer, later unpacked by `Bind_Port()` (`p&255` = fd, `p/256` = port-class →
`UserState[u]`).

A hardcoded IP block-list ("SWANSEA STUFF", [IPCDirect.c:784-797](../IPCDirect.c)) rejects two
literal IPv4 addresses (`137.44.1.1`, `143.52.2.10`) before a `PACKET_LOGINREQUEST` is even
generated — this is dead institutional history (a specific historical abuse source, presumably from
the original Aberystwyth-run instance) rather than a general access-control mechanism; see
[review-findings.md](review-findings.md).

## 4. Output formatting rules

- Plain text (`PACKET_OUTPUT`, `PACKET_CLEAR`'s text payload) goes through `CharPut`/`LineFlush`
  ([IPCDirect.c](../IPCDirect.c)), which word-wraps to 79 columns and always terminates lines with
  `\r\n`.
- Prompts (`PACKET_SETPROMPT`) are stored per-connection (`Prompts[u]`, 64 bytes) and only actually
  sent to the client alongside the next `PACKET_INPUT` — behavior differs by connection class:
  - BSX (`UserState==2`): prompt sent only if it starts with an alpha character (a heuristic to
    avoid pushing raw system prompts like `-}---` at a graphical client — comment: "Only alpha
    prompts are sent to BSX users").
  - "Alt"/client-mode (`UserState==1`): prompt wrapped in `\002P...\003` control framing (a
    lightweight SOH/ETX-style field delimiter for the historical curses `client`/`Client.c`
    front-end, which is **not present in this repository** — see [build-and-run.md](build-and-run.md)).
  - Plain telnet (`UserState==0`): prompt text sent as-is, no control framing.
- Snooped output (`PACKET_SNOOPTEXT`) is separately word-wrapped through `SnoopCharPut`/`SnoopFlush`
  and prefixed with `\002S`/`|` depending on connection class.

## 5. BSX sub-protocol

Active whenever `UserState[u]==2` (a connection accepted on the BSX listening port). BSX frames
travel as ordinary `PACKET_BSXSCENE` text payloads (`WriteSocket`, no additional length-prefixing);
control is expressed via `@`-prefixed tokens embedded directly in the text stream:

| Token | Direction | Meaning |
|---|---|---|
| `@SCE<name>.` | server→client | Load/show scene `<name>` |
| `@RFS` | server→client | Refresh/render current scene state |
| `@PUR<name>.` | server→client | Purge cached copy of object/image `<name>` |
| `@VIO<name>.<2-hex>` | server→client | Place/vary object `<name>` at encoded position/state |
| `@RMO<name>.` | server→client | Remove object `<name>` from the current scene |
| `@DFS<name>.` / `@DFO<name>.` | server→client | Define-scene / define-object header preceding raw hex image data |
| `@TMS` | server→client | Sent appended to a `PACKET_CLEAR` message for BSX clients only |
| `#RQS<name>.` | client→server | Request scene image data for `<name>` |
| `#RQO<name>.` | client→server | Request object image data for `<name>` |

Image payload itself is the raw `BSXImage.bsx_Data` bytes hex-encoded 2 characters per byte
(`BSXDecodePair`), streamed in chunks of ≤510 characters per `PACKET_BSXSCENE` packet
(`BSXDecompSend`, [BSX.c:282-306](../BSX.c)), terminated by a subsequent `@RFS`.

Input handling: any line from a BSX-state connection beginning with `#` is classified
`PACKET_BSXSCENE` instead of `PACKET_COMMAND` at the socket layer
([IPCDirect.c:208-211](../IPCDirect.c)); everything else is a normal game command, still subject to
the full parser/table/command dispatch — i.e. BSX users play the same game as telnet users, with an
additional side-channel for image requests.

`Handle_BSXPacket()` ([BSX.c:308-336](../BSX.c)) only implements `#RQS`/`#RQO`; any other `#`-prefixed
line is silently ignored (`/* Nothing else really matters */`).

**Confirmed limitation**: `Handle_BSXPacket` does no length validation before scanning for a `.`
terminator (`while(*t!='.'&&*t) t++;`) — if the client never sends a `.`, the scan safely stops at
the input's NUL terminator (input lines are always NUL-terminated by `ReadBlock`'s line assembly),
so this specific loop is bounded by the surrounding buffer rather than unbounded; separately, see
[review-findings.md](review-findings.md) for the **actual** unbounded-write defect found in
`ReadBlock` itself, which affects both BSX and plain-text input equally.

## 6. Unconfirmed / needs verification

- Whether a genuine historical BSXmud client still exists anywhere to validate this protocol
  description end-to-end — this document was written entirely from the server-side implementation,
  with no client reference available in this repository.
- The precise intended semantic difference between the main port and the "alt" port beyond
  `UserState` classification — no differing behavior beyond that tag was found in this review.
