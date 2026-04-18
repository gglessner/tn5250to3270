"""Synthesize Query Reply structured fields.

When the host sends Read Partition Query, it's asking 'what can this
terminal do?'. The 5250 client can't answer that — WE answer, describing
our virtual terminal.

GA23-0059 chapter 6 (Query Reply structured fields).
"""
import struct
from .constants import AID_SF

# Structured Field IDs
SFID_READ_PARTITION = 0x01
SFID_QUERY_REPLY    = 0x81

# Read Partition types
RP_QUERY      = 0x02
RP_QUERY_LIST = 0x03

# Query Reply codes (QCODEs) — what we claim to support
QR_SUMMARY            = 0x80
QR_USABLE_AREA        = 0x81
QR_ALPHANUMERIC_PARTS = 0x84
QR_CHARSETS           = 0x85
QR_COLOR              = 0x86
QR_HIGHLIGHT          = 0x87
QR_REPLY_MODES        = 0x88
QR_DDM                = 0x95
QR_RPQ_NAMES          = 0xA1
QR_IMPLICIT_PARTITION = 0xA6
QR_NULL               = 0xFF


def is_read_partition_query(wsf_data: bytes) -> bool:
    """Detect Read Partition Query / Query List in WSF data.

    Format: len(2) SFID PID type [...]
    SFID = 0x01 (Read Partition), type = 0x02 (Query) or 0x03 (Query List).
    """
    if len(wsf_data) < 5:
        return False
    sfid = wsf_data[2]
    rp_type = wsf_data[4]
    return sfid == SFID_READ_PARTITION and rp_type in (RP_QUERY, RP_QUERY_LIST)


def _sf(qcode: int, data: bytes) -> bytes:
    """Build one Query Reply structured field: len(2) + 0x81 + qcode + data."""
    body = bytes([SFID_QUERY_REPLY, qcode]) + data
    length = len(body) + 2
    return struct.pack(">H", length) + body


def build_query_reply(rows: int, cols: int, color: bool) -> bytes:
    """Build complete Query Reply: AID_SF + Summary + each supported SF.

    We claim a minimal but useful set:
    - Summary (required — lists what else we'll send)
    - Usable Area (required — screen dimensions)
    - Color (if color)
    - Highlighting
    - Reply Modes
    - Implicit Partition (default partition geometry)
    """
    out = bytearray([AID_SF])

    supported = [QR_SUMMARY, QR_USABLE_AREA, QR_HIGHLIGHT,
                 QR_REPLY_MODES, QR_IMPLICIT_PARTITION]
    if color:
        supported.insert(2, QR_COLOR)

    # ── Summary: just the list of QCODEs ───────────────────────────
    out += _sf(QR_SUMMARY, bytes(supported))

    # ── Usable Area: GA23-0059 §6.42 ───────────────────────────────
    # flags(1) + flags(1) + width(2) + height(2) + units(1) + Xr(4) + Yr(4) +
    # AW(1) + AH(1) + buffer(4)
    # Most hosts only care about width/height.
    # TODO verify Xr/Yr encoding against real host — GA23-0059 says 4-byte
    # fixed-point (2.2); if hosts complain, switch to >BBHHBHHHHBBI (2-byte halves).
    ua = struct.pack(">BBHHBLLBBI",
        0x01,           # flags: 12/14-bit addressing supported
        0x00,           # flags2
        cols, rows,     # width, height in cells
        0x01,           # units: cells (not mm)
        1, 1,           # Xr, Yr (aspect ratio numerator/denom — dummy)
        0x09, 0x0C,     # AW, AH: cell width/height in 1/72" — dummy 9x12
        rows * cols,    # buffer size
    )
    out += _sf(QR_USABLE_AREA, ua)

    # ── Color: GA23-0059 §6.13 ─────────────────────────────────────
    if color:
        # flags(1) + np(1) + np*(CAV(1)+CI(1))
        # We support 8 color pairs: default + 7 colors
        pairs = bytearray()
        # CAV = color attribute value (0xF1-0xF7), CI = color identifier (same)
        for cav in [0x00, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7]:
            pairs.append(cav)
            pairs.append(cav)  # we support each color as itself
        out += _sf(QR_COLOR, bytes([0x00, 8]) + bytes(pairs))

    # ── Highlighting: GA23-0059 §6.23 ──────────────────────────────
    # np(1) + np*(HAV(1)+HI(1))
    hl_pairs = bytes([
        0x00, 0xF0,  # default → normal
        0xF1, 0xF1,  # blink
        0xF2, 0xF2,  # reverse
        0xF4, 0xF4,  # underline
    ])
    out += _sf(QR_HIGHLIGHT, bytes([4]) + hl_pairs)

    # ── Reply Modes: GA23-0059 §6.34 ───────────────────────────────
    # List of reply modes we support: 0=Field, 1=Extended Field, 2=Character
    out += _sf(QR_REPLY_MODES, bytes([0x00, 0x01, 0x02]))

    # ── Implicit Partition: GA23-0059 §6.25 ────────────────────────
    # flags(2) + SDP for default + alternate dimensions
    # SDP: len(1) + id(1) + width(2) + height(2) = 6 bytes
    sdp_default = struct.pack(">BBHH", 0x06, 0x01, 80, 24)
    sdp_alt     = struct.pack(">BBHH", 0x06, 0x02, cols, rows)
    out += _sf(QR_IMPLICIT_PARTITION, bytes([0x00, 0x00]) + sdp_default + sdp_alt)

    return bytes(out)
