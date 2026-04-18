# tn5250to3270

A **protocol-translating** TN5250 ↔ TN3270 proxy. It accepts connections from
an IBM i (AS/400) TN5250 terminal emulator — IBM i Access Client Solutions,
tn5250j, Mocha — and speaks TN3270 / TN3270E to a z/OS or z/VM host on the
back end, translating the data stream in both directions in real time.

```
┌──────────────┐  TN5250   ┌──────────────────────────────┐  TN3270(E)  ┌───────────┐
│ 5250 client  │◄────────►│ tn5250to3270:                │◄──────────►│ z/OS host │
│ ACS, tn5250j │  plain    │  5250 srv ↔ Screen ↔ 3270 cli│  TLS|plain  │ TSO, CICS │
└──────────────┘           └──────────────────────────────┘             └───────────┘
```

This is **not** a transport relay. The 3270 and 5250 data streams are not
wire-compatible — the proxy fully parses one protocol into a neutral screen
model and re-emits the other.

## Why this is hard

3270 and 5250 are both EBCDIC block-mode terminal protocols from IBM, and at a
glance they look similar: a host paints a screen, the user fills in fields and
presses an AID key, the terminal sends back the modified fields. Underneath,
almost nothing lines up:

| Concern | 3270 (GA23-0059, RFC 1576/2355) | 5250 (SC30-3533, RFC 1205/4777) |
|---|---|---|
| **Addressing** | 12-bit or 14-bit *linear buffer offset*, encoded in a 6-bit-per-byte scheme | 1-based *row, column* pairs |
| **Field definition** | Attribute byte occupies a buffer cell; SF/SFE orders inline in the data stream | Field Format Word + optional FCW in a *separate format table*; screen-attribute byte (0x20–0x3F) on screen |
| **Record framing** | Raw stream terminated by telnet EOR; TN3270E adds a 5-byte typed header | Every record wrapped in a GDS header (length + `12A0` + flags + opcode) |
| **Telnet negotiation** | TN3270E option (40): DEVICE-TYPE / FUNCTIONS sub-negotiation, with fallback to BINARY+EOR+TERMINAL-TYPE | NEW-ENVIRON option (39): DEVNAME, KBDTYPE, CODEPAGE, etc. |
| **Order vocabulary** | SF, SBA, IC, PT, RA, EUA, SFE, SA, MF, GE | SOH, RA, EA, SBA, IC, SF, TD, WEA, WDSF |
| **Host interrogation** | Host sends Read-Partition-Query structured field; terminal must reply with usable-area, color, highlighting capabilities | Host trusts the negotiated terminal type |
| **AID set** | Enter, PF1–24, PA1–3, Clear | Enter, F1–24, Roll Up/Down, Help, Print, SysReq, Attn, Record-Backspace |

A correct converter therefore cannot patch bytes — it has to:

1. **Run two independent telnet option state machines** (one acting as a 5250
   *server*, one as a 3270 *client*), including TN3270E DEVICE-TYPE/FUNCTIONS
   negotiation with graceful fallback to basic TN3270.
2. **Decode 12/14-bit 3270 buffer addresses** (the 6-bit-packed encoding from
   GA23-0059 §4.3) into linear offsets, and convert those to 5250 row/column.
3. **Materialise a full virtual screen** — every cell's EBCDIC byte, foreground,
   background, highlight, and field membership — because 5250's Write-To-Display
   needs the *whole* format table, not a delta.
4. **Translate field-attribute semantics** between a 3270 attribute byte
   (protected/numeric/display/MDT bits packed into one byte that *occupies a
   screen cell*) and a 5250 FFW/FCW pair (16+ bits in an off-screen format
   table) without shifting visual layout by a column.
5. **Answer for itself** when the host interrogates the "terminal": synthesise a
   3270 Query-Reply structured field (usable area, color, highlighting,
   implicit-partition) and respond to Read-Buffer from the proxy's own screen
   model — the 5250 client has no idea these exchanges happened.
6. **Map AID keys** across two different sets (Roll-Up→PF8, Help→PF1, SysReq via
   TN3270E if negotiated else PA1) and honour the 3270 AID-register semantics
   that MVS depends on (sending `AID_NO` after Enter triggers an
   *Invalid attention* abend).
7. **Track formatted vs unformatted host screens** — 3270 inbound for an
   unformatted screen must omit SBA orders entirely; getting this wrong abends
   the host application.

The reference material for this is spread across **GA23-0059** (3270 Data
Stream Programmer's Reference, ~400 pages), **SC30-3533** (5250 Functions
Reference), **RFC 1576/1646/2355** (TN3270/TN3270E) and **RFC 1205/2877/4777**
(TN5250) — five documents, two of which predate the web and assume you already
own the hardware. There is no overlap in test tooling: 3270 traffic is captured
with `x3270 -trace` against Hercules, 5250 traffic with Wireshark against a
real IBM i; golden vectors for each side come from different ecosystems.

## Architecture

The hard constraint that makes this tractable: **the two protocol packages
never import each other.** Both speak to a neutral `VirtualScreen` via a small
`ScreenOp` IR, so each side is unit-testable in isolation and the round-trip
property (`3270 bytes → screen → 5250 bytes → screen′; screen == screen′`) is
mechanically checkable.

```
Session (one per connection — owns lock, lifecycle, two reader threads)
   │
   ├── telnet/codec.py          IAC state machine, EOR record framing (shared)
   │
   ├── tn3270/                  Host-facing client
   │     negotiator.py          TN3270E DEVICE-TYPE/FUNCTIONS, basic fallback
   │     parser.py              host bytes → list[ScreenOp]
   │     emitter.py             (AID, cursor, modified-fields) → inbound bytes
   │     addressing.py          12/14-bit buffer address codec
   │     tn3270e.py             5-byte E-mode header
   │     query_reply.py         synthesised Query-Reply structured field
   │
   ├── tn5250/                  Client-facing server
   │     negotiator.py          TERMINAL-TYPE + NEW-ENVIRON (DEVNAME)
   │     emitter.py             VirtualScreen → WTD bytes  (the hard one)
   │     parser.py              client GDS → (AID, cursor, field data)
   │     gds.py / ffw.py        GDS header, Field-Format-Word codec
   │
   └── screen/                  The neutral middle
         ops.py                 ScreenOp IR (EraseAll, SetBufferAddr, WriteText,
                                DefineField, SetCursor, RepeatChar, …)
         model.py               VirtualScreen: cells[], fields[], cursor,
                                apply(op), get_modified_fields()
```

Each `Session` runs a two-phase lifecycle: synchronous negotiation (5250 side
first → geometry lookup → dial upstream → 3270 side), then two reader threads
sharing one lock over the screen model. Socket I/O is outside the lock, so a
slow client never stalls the host reader.

## Features

- TN3270E (RFC 2355) with automatic fallback to basic TN3270 (RFC 1576)
- Implicit TLS (`telnets`) to the upstream host; self-signed-tolerant
- Client-driven geometry: the 5250 terminal type (`IBM-3477-FC`, `IBM-3179-2`,
  …) selects the matching 3270 model and screen size
- Colour, extended highlighting, intensified/hidden fields
- Proxy-side handling of Read-Partition-Query, Read-Buffer, BIND-IMAGE
- Configurable EBCDIC codepage (cp037 default; cp500/cp1047/cp273/cp1140)
- ≤ 50 concurrent sessions, two threads each, stdlib transport only
- Structured (JSON) logging; every unmappable order/SF/AID is logged with hex

## Install & run

```bash
git clone https://github.com/gglessner/tn5250to3270.git
cd tn5250to3270
pip install -e .
python -m tn5250to3270 --config config.yaml
```

`config.yaml` controls the listen address, upstream host/port/TLS, EBCDIC
codepage, and the 5250→3270 geometry map. See
[`docs/superpowers/specs/2026-04-07-tn5250-to-tn3270-proxy-design.md`](docs/superpowers/specs/2026-04-07-tn5250-to-tn3270-proxy-design.md)
for the full design and
[`docs/MANUAL_TESTING.md`](docs/MANUAL_TESTING.md) for the
Hercules / tn5250j / ACS interop checklist.

## Tests

```bash
pip install -e ".[dev]"
pytest                       # 19 unit-test modules
pytest tests/integration     # mock-host end-to-end
```

Unit coverage includes 12/14-bit address round-trip, every `FieldAttrs` → FFW
encoding, golden-vector parser/emitter checks for both protocols, and a
3270→screen→5250→screen round-trip equality property.

## Out of scope (v1)

Client-side TLS · 5250 WDSF GUI windows / scrollbars / mouse · 3270 GDDM
graphics · printer LU (3287/3812) · DBCS EBCDIC · session reconnect.

## References

- IBM GA23-0059 — *3270 Information Display System Data Stream Programmer's Reference*
- IBM SC30-3533 — *5250 Functions Reference*
- RFC 1576, RFC 1646, RFC 2355 — TN3270 / TN3270E
- RFC 1205, RFC 2877, RFC 4777 — TN5250 / 5250 Telnet Enhancements

## Author

Garland Glessner — <gglessner@gmail.com>

## License

GNU General Public License v3.0 or later — see [LICENSE](LICENSE).
