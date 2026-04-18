# TN5250 ↔ TN3270 Protocol-Converting Proxy — Design Spec

**Date:** 2026-04-07
**Status:** Approved for implementation planning

---

## 1. Problem statement

Build a multi-threaded Python TCP proxy that lets a TN5250 terminal emulator
(IBM i Access Client Solutions, tn5250j) connect to an IBM mainframe that
speaks TN3270/TN3270E. The proxy performs full bidirectional protocol
conversion — not just transport relay.

```
┌──────────────┐  TN5250    ┌─────────────────────────────┐  TN3270(E)  ┌───────────┐
│ 5250 Client  │◄─────────►│ Proxy:                      │◄──────────►│ Mainframe │
│ ACS, tn5250j │  plaintext │  5250 srv ↔ Screen ↔ 3270 cli│  TLS|plain │ z/OS, z/VM│
└──────────────┘            └─────────────────────────────┘             └───────────┘
```

The 3270 and 5250 data streams are **not wire-compatible**:

| Aspect              | 3270                          | 5250                              |
|---------------------|-------------------------------|-----------------------------------|
| Addressing          | 12/14-bit linear buffer addr  | 1-based row/column                |
| Field definition    | Attribute byte in buffer      | FFW/FCW in separate format table  |
| Record framing      | Raw stream / TN3270E header   | GDS header (len + 0x12A0 + opcode)|
| Telnet enhancement  | TN3270E (opt 40)              | NEW-ENVIRON (opt 39)              |
| Orders              | SF SBA IC PT RA EUA SFE SA MF | SOH RA EA SBA IC SF TD WEA WDSF   |

A correct converter must fully parse one stream, materialize an intermediate
screen representation, and re-emit the other. There is no shortcut.

---

## 2. Requirements (locked)

| Aspect       | Decision                                                            |
|--------------|---------------------------------------------------------------------|
| Server side  | TN3270E (RFC 2355) with fallback to basic TN3270 (RFC 1576)         |
| Server TLS   | Optional — implicit TLS (telnets) when configured                   |
| Client side  | TN5250 (RFC 1205/2877), plaintext only                              |
| Client compat| Must work with IBM ACS and tn5250j                                  |
| Fidelity     | Production: color, extended attributes, multiple geometries         |
| Geometry     | Client-driven — 5250 term-type maps to matching 3270 model          |
| Unmappable   | Proxy answers structured fields itself; drop+log everything else    |
| Scale        | ≤50 concurrent sessions; two-threads-per-session                    |
| Python       | 3.11+, stdlib only for transport (socket, ssl, threading)           |

---

## 3. Architecture: layered pipeline

Three independent layers connected by a single intermediate type (`ScreenOp`):

```
┌────────────────────────────────────────────────────────────────────┐
│  Session  (one per connection — owns lock, lifecycle, threads)    │
│                                                                    │
│   ┌──────────────┐                      ┌──────────────┐          │
│   │ TelnetCodec  │                      │ TelnetCodec  │          │
│   │ (5250 side)  │                      │ (3270 side)  │          │
│   └──────┬───────┘                      └──────┬───────┘          │
│          │                                     │                   │
│   ┌──────▼───────┐                      ┌──────▼───────┐          │
│   │ tn5250       │                      │ tn3270       │          │
│   │  negotiator  │                      │  negotiator  │          │
│   │  parser      │                      │  parser      │          │
│   │  emitter     │                      │  emitter     │          │
│   └──────┬───────┘                      └──────┬───────┘          │
│          │                                     │                   │
│          │         ┌─────────────────┐         │                   │
│          └────────►│  VirtualScreen  │◄────────┘                   │
│                    │  (screen.model) │                             │
│                    └─────────────────┘                             │
│                            ▲                                       │
│                  consumes ScreenOp values                          │
│                  (screen.ops — the IR contract)                    │
└────────────────────────────────────────────────────────────────────┘
```

**Coupling rule:** `tn3270/` and `tn5250/` never import each other. Both import
`screen.ops`. The screen model imports neither protocol package. This is the
seam that makes each piece independently testable.

---

## 4. Module layout

```
tn5250to3270/
├── __main__.py          CLI entry, config load, start listener
├── config.py            Config dataclass, YAML loader
├── listener.py          Accept loop → spawn Session per connection
├── session.py           Orchestrator: negotiation phase, then reader threads
│
├── telnet/
│   ├── codec.py         TelnetCodec — IAC FSM, option neg, EOR record framing
│   └── options.py       IAC, WILL/WONT/DO/DONT, SB/SE, option number constants
│
├── tn3270/
│   ├── negotiator.py    TN3270E DEVICE-TYPE/FUNCTIONS handshake; basic fallback
│   ├── parser.py        bytes → list[ScreenOp]  (W/EW/EWA + all orders)
│   ├── emitter.py       (aid, cursor, modified_fields) → inbound bytes
│   ├── addressing.py    12/14-bit buffer address ↔ linear offset
│   ├── tn3270e.py       5-byte E-mode record header pack/unpack
│   ├── query_reply.py   Synthesize Query Reply structured field
│   └── constants.py     Order codes, AID codes, WCC bits, attr-byte bits
│
├── tn5250/
│   ├── negotiator.py    TERMINAL-TYPE + NEW-ENVIRON (DEVNAME) handshake
│   ├── parser.py        Inbound GDS → (aid, cursor, [(row,col,data),...])
│   ├── emitter.py       VirtualScreen → WTD bytes  (the hardest module)
│   ├── gds.py           GDS header (len, 0x12A0, flags, opcode) pack/unpack
│   ├── ffw.py           Field Format Word / FCW encode from canonical attrs
│   └── constants.py     Order codes, AID codes, screen attrs, opcodes
│
├── screen/
│   ├── ops.py           ScreenOp ADT — the IR contract
│   ├── model.py         VirtualScreen: cells[], fields[], cursor, .apply(op)
│   ├── cell.py          Cell: ebcdic_byte + canonical fg/bg/hilite
│   └── field.py         Field: start, len, protected/numeric/hidden/intensified/mdt
│
├── ebcdic.py            Codec wrapper (cp037 default, configurable)
└── geometry.py          5250 term-type → (rows, cols, 3270 model) lookup
```

**Size budget:** `tn3270/parser.py` ~400 lines, `tn5250/emitter.py` ~450 lines,
`screen/model.py` ~300 lines. Everything else under 200.

---

## 5. The ScreenOp contract

The 3270 parser emits these. The screen model consumes them. Protocol-agnostic.

```python
# screen/ops.py — frozen dataclasses

EraseAll                          # 3270 EW/EWA: clear cells, drop fields, cursor=0
SetBufferAddr(pos: int)           # 3270 SBA: move write pointer
WriteText(data: bytes)            # Literal EBCDIC bytes at current write pointer
DefineField(pos: int,             # 3270 SF/SFE: place attr byte at pos,
            attrs: FieldAttrs)    #   field data starts at pos+1
SetCursor(pos: int)               # 3270 IC
RepeatChar(to_pos: int, ch: int)  # 3270 RA: fill [current..to_pos) with ch
EraseUnprotected(to_pos: int)     # 3270 EUA: nulls in unprotected up to to_pos
ProgramTab                        # 3270 PT: advance to next unprotected field
SetExtAttr(typ: int, val: int)    # 3270 SA: char-level attr, sticky until changed
WccFlags(reset_mdt: bool,         # Decoded WCC byte
         unlock_kbd: bool,
         alarm: bool,
         restore: bool)
```

`FieldAttrs` is a canonical dataclass: `protected`, `numeric`, `hidden`,
`intensified`, `mdt`, plus extended `fg`, `bg`, `hilite` (defaults if SF, set
if SFE). Both protocols' attribute bytes decode to this.

---

## 6. VirtualScreen

```python
@dataclass
class Cell:
    char: int           # EBCDIC byte; 0x00=null, 0x40=space
    fg: Color           # Canonical enum (8 colors + default)
    bg: Color
    hilite: Hilite      # NONE, BLINK, REVERSE, UNDERLINE
    is_field_attr: bool # True → this position holds a field attribute,
                        #   not displayable data

@dataclass
class Field:
    start: int          # Linear offset of the attribute byte
    length: int         # Distance to next field's attr byte (wraps)
    protected: bool
    numeric: bool
    hidden: bool        # Non-display
    intensified: bool
    mdt: bool

class VirtualScreen:
    rows: int
    cols: int
    cells: list[Cell]           # Flat, len = rows*cols
    fields: list[Field]         # Sorted by start
    cursor: int                 # Linear offset
    write_ptr: int              # Current SBA position (parser state)
    keyboard_locked: bool
    alarm: bool

    def apply(self, op: ScreenOp) -> None: ...
    def get_modified_fields(self) -> list[tuple[int, bytes]]: ...
        # Returns [(field_data_start, ebcdic_bytes)] for each MDT-set field.
        # Nulls stripped per 3270 inbound rules.
    def reset_mdt(self) -> None: ...
    def set_field_data(self, pos: int, data: bytes) -> None: ...
        # Client wrote into a field — store data, set MDT.
    def field_at(self, pos: int) -> Field | None: ...
```

**Critical mapping detail.** In 3270, the field-attribute byte occupies a buffer
position — it displays as a blank but takes up a cell. In 5250, the screen-
attribute byte (0x20–0x3F) also occupies a position. The 5250 emitter places
a screen-attribute byte at exactly the position where 3270 had its field-attr
byte; the FFW goes into the format table separately. Visual layout is preserved
without offset arithmetic.

---

## 7. Connection lifecycle

### Phase 1 — Negotiation (synchronous, single thread)

```
client connects
  │
  ├─► TN5250 telnet negotiation
  │     DO/WILL BINARY, DO/WILL EOR
  │     DO TERMINAL-TYPE → SB SEND → client replies "IBM-3477-FC"
  │     DO NEW-ENVIRON   → SB SEND → client replies DEVNAME=xxx (optional)
  │
  ├─► geometry.match("IBM-3477-FC") → ("IBM-3278-5-E", rows=27, cols=132)
  │   VirtualScreen(27, 132)
  │
  ├─► socket.connect(upstream); ssl.wrap_socket() if tls.enabled
  │
  ├─► TN3270E negotiation
  │     WILL TN3270E
  │     ┌─ host says DO ──────────────────────────────────┐
  │     │ SB DEVICE-TYPE REQUEST IBM-3278-5-E             │
  │     │   [CONNECT <devname>]   ← pass through from 5250│
  │     │ host: DEVICE-TYPE IS … CONNECT <luname>         │
  │     │ SB FUNCTIONS REQUEST BIND-IMAGE RESPONSES SYSREQ│
  │     │ host: FUNCTIONS IS …                            │
  │     └─────────────────────────────────────────────────┘
  │     ┌─ host says WONT ────────────────────────────────┐
  │     │ Fall back: DO/WILL BINARY+EOR,                  │
  │     │   TERMINAL-TYPE = IBM-3278-5                    │
  │     └─────────────────────────────────────────────────┘
  │
  └─► spawn host_reader_thread + client_reader_thread → Phase 2
```

If the client's terminal type isn't in the geometry map, close the client
**before** connecting upstream — don't waste a host connection.

### Phase 2 — Runtime (two threads, one lock)

**Host → Client** (`host_reader_thread`):

```
host_sock.recv()
  → telnet_host.feed()                  Strip IAC, buffer until EOR
  → [TN3270E] tn3270e.unpack_header()   Extract data-type, request flag, seqno
  → tn3270.parser.parse(payload)        → list[ScreenOp]
       │
       ├─ Intercept: Read Partition Query structured field?
       │  → query_reply.synthesize(rows, cols, color=True)
       │  → send to host directly. Do NOT touch screen.
       │  → continue (skip rest of pipeline for this record).
       │
       └─ Normal data:
            with session.lock:
                for op in ops: screen.apply(op)
                wtd = tn5250.emitter.render(screen, wcc_flags)
            gds = tn5250.gds.wrap(wtd, opcode=PUT_GET if unlock else OUTPUT_ONLY)
            client_sock.sendall(telnet_client.wrap(gds))
            [TN3270E] if RESPONSES negotiated and request flag set:
                send POSITIVE-DEVICE-END to host
```

**Client → Host** (`client_reader_thread`):

```
client_sock.recv()
  → telnet_client.feed()                Strip IAC, buffer until EOR
  → tn5250.gds.unpack()                 → (opcode, payload)
  → tn5250.parser.parse_inbound()       → (aid_5250, cur_row, cur_col, fields)
       fields = [(row, col, ebcdic_data), ...]
  → with session.lock:
        for (r, c, data) in fields:
            screen.set_field_data(rc_to_linear(r, c), data)  Sets MDT
        screen.cursor = rc_to_linear(cur_row, cur_col)
        modified = screen.get_modified_fields()
  → aid_3270 = AID_MAP[aid_5250]
  → inbound = tn3270.emitter.build(aid_3270, screen.cursor, modified)
       Format: AID + cursor_addr(2) + [SBA + addr(2) + data]* per modified field
  → [TN3270E] prepend 5-byte header (data-type=3270-DATA)
  → host_sock.sendall(telnet_host.wrap(inbound))
```

**Lock discipline.** Lock covers only screen read/write. Socket `sendall()` is
outside the lock — a slow client doesn't stall the host reader. No deadlock:
each thread blocks on `recv()` while not holding the lock; no circular wait.

---

## 8. AID key mapping

| 5250 AID         | 3270 AID    | Notes                                       |
|------------------|-------------|---------------------------------------------|
| Enter (0xF1)     | Enter (0x7D)|                                             |
| F1–F24           | PF1–PF24    | Direct map                                  |
| Clear (0xBD)     | Clear (0x6D)|                                             |
| Roll Up (0xF4)   | PF8         | Page-down convention                        |
| Roll Down (0xF5) | PF7         | Page-up convention                          |
| Help (0xF3)      | PF1         | Convention                                  |
| Print (0xF6)     | — drop+log  | No clean 3270 equivalent                    |
| SysReq           | TN3270E SYSREQ if negotiated, else PA1                    |
| Attn             | TN3270E ATTN (IAC BRK), else PA2                          |
| Record Backspace | — drop+log  |                                             |

---

## 9. Unmappable feature policy

The proxy is a 3270 terminal as far as the host knows. It must answer for itself.

| Host sends                | Proxy action                                    |
|---------------------------|-------------------------------------------------|
| Read Partition Query (SF) | Synthesize Query Reply: usable area, color,     |
|                           | highlighting, implicit partition. Send to host. |
|                           | Client never sees it.                           |
| Read Buffer (RB)          | Build full buffer dump from VirtualScreen,      |
|                           | send to host. Client never sees it.             |
| SCS printer data          | Drop. WARNING log with hex dump.                |
| GE (Graphic Escape)       | Substitute EBCDIC `?` (0x6F). DEBUG log.        |
| Unknown structured field  | Drop. WARNING log with SFID.                    |
| Unknown order byte        | Skip 1 byte. WARNING log with offset+hex.       |
| BIND-IMAGE                | Parse, log, discard (we don't need it).         |

| Client sends              | Proxy action                                    |
|---------------------------|-------------------------------------------------|
| 5250 SysReq w/o E-mode    | Translate to PA1.                               |
| 5250 Test Request         | Drop. WARNING log.                              |
| Field with no MDT         | Don't transmit (matches 3270 RM semantics).     |

---

## 10. Configuration

```yaml
# config.yaml

listen:
  host: 0.0.0.0
  port: 2323

upstream:
  host: mainframe.example.com
  port: 992                    # 23 plain, 992 telnets
  tls:
    enabled: true
    verify: false              # Mainframe certs are usually self-signed
    ca_bundle: null            # Optional path to CA PEM
  connect_timeout: 10.0
  negotiate_timeout: 10.0

ebcdic:
  codepage: cp037              # cp500, cp1047, cp273, cp1140 also valid

geometry:
  # 5250 client term-type → (3270 device-type, rows, cols)
  IBM-3477-FC:  [IBM-3278-5-E, 27, 132]
  IBM-3477-FG:  [IBM-3278-5-E, 27, 132]
  IBM-3180-2:   [IBM-3278-5-E, 27, 132]
  IBM-3179-2:   [IBM-3278-2-E, 24,  80]
  IBM-3196-A1:  [IBM-3278-2-E, 24,  80]
  IBM-5251-11:  [IBM-3278-2,   24,  80]   # Mono, no -E suffix

logging:
  level: INFO
  format: json                 # or "text"
  unmappable_level: WARNING
```

CLI: `python -m tn5250to3270 --config config.yaml`. CLI flags override YAML.

---

## 11. Error handling

| Failure                       | Action                                       |
|-------------------------------|----------------------------------------------|
| Client connect, bad term-type | Close client, ERROR log, no upstream dial    |
| Upstream connect timeout      | Close client, ERROR log                      |
| TLS handshake fail            | Close client, ERROR log; honor `verify:false`|
| TN3270E rejected              | INFO log, fall back to basic TN3270          |
| Basic TN3270 also fails       | Close both, ERROR log                        |
| Negotiation timeout (10s)     | Close both, ERROR log                        |
| Unknown 3270 order            | WARNING, skip byte, keep parsing             |
| Truncated record (no EOR)     | ERROR, drop record, resync on next EOR       |
| Socket error mid-session      | Close peer, join threads, INFO log           |
| Parse exception               | ERROR log w/ hex dump, close session         |

Sessions are independent — one crash never affects another. The listener thread
catches all exceptions from `Session.run()` and logs.

---

## 12. Testing strategy

### Unit (per module, no I/O)

| Module                | Test                                                   |
|-----------------------|--------------------------------------------------------|
| `tn3270/addressing`   | Round-trip every valid 12/14-bit address               |
| `tn3270/parser`       | Golden vectors → expected `list[ScreenOp]`             |
| `tn3270/emitter`      | Known (aid, cursor, fields) → exact byte assertion     |
| `tn5250/gds`          | Round-trip header pack/unpack                          |
| `tn5250/ffw`          | Every `FieldAttrs` combo → expected FFW bytes          |
| `tn5250/emitter`      | Known `VirtualScreen` → exact WTD bytes (golden file)  |
| `tn5250/parser`       | Captured ACS inbound → expected (aid, cursor, fields)  |
| `screen/model`        | Apply op sequence → assert cells/fields/cursor state   |
| `telnet/codec`        | IAC sequences (incl. IAC-IAC escaping) → clean records |

### Golden vector sources

- x3270 `-trace` against Hercules — captures real 3270 host output
- Wireshark capture of ACS → real IBM i — captures real 5250 client inbound
- Hand-built minimal vectors per RFC for edge cases (empty WTD, single field, max-size)

### Round-trip property tests

```
3270_bytes → parse → screen_A
                       │
                       └─► emit_5250 → parse_5250 → screen_B
assert screen_A == screen_B   # Cells, fields, cursor all match
```

### Integration

- `MockHost`: TCP server that sends a canned 3270 EW (TSO logon screen) and
  validates the inbound bytes it receives.
- Spawn proxy pointed at MockHost.
- Spawn tn5250j as subprocess pointed at proxy.
- Drive tn5250j via its scripting interface; assert screen text + assert
  MockHost received correct AID + field data.

### Interop (manual checklist)

- Hercules MVS 3.8j (basic TN3270) ↔ proxy ↔ tn5250j: log into TSO
- Hercules with TN3270E patches ↔ proxy ↔ ACS: log in, navigate ISPF
- TLS: stunnel-wrapped Hercules ↔ proxy ↔ ACS

---

## 13. Out of scope (v1)

- TLS on the client side
- TN5250 enhanced features: WDSF GUI windows, scrollbars, mouse
- 3270 graphics (GDDM)
- Printer LU support (3287 / 3812)
- Session persistence / reconnect
- Hot config reload
- Web admin UI
- DBCS / double-byte EBCDIC
