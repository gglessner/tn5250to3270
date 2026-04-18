"""Build 3270 inbound (terminal→host) data streams.

Two flavors:
- build_inbound: normal AID transmission (Read Modified semantics)
- build_read_buffer_reply: response to host RB command (full buffer dump)
"""
from .addressing import encode_addr
from .constants import (
    ORD_SBA, ORD_SF, SHORT_AIDS, AID_NO,
    ATTR_PROTECT, ATTR_NUMERIC, ATTR_DISP_INTENSE, ATTR_DISP_HIDDEN, ATTR_MDT,
)
from ..screen.model import VirtualScreen


def build_inbound(
    aid: int,
    cursor: int,
    modified: list[tuple[int, bytes]],
    mode14: bool,
    unformatted: bool = False,
) -> bytes:
    """3270 inbound stream. Three formats verified against real wire
    trace (pentest.db, terminal → 10.0.0.207:3270):

    Short AID (Clear, PA1-3):  AID byte ONLY. No cursor.
        Trace rec 35: 6D FFEF — Clear is one byte. GA23-0059 §3.5.6:
        "only the AID byte itself is transferred inbound."

    Unformatted screen:        AID + cursor + raw_data. NO SBA.
        Trace rec 39: 7D 40C4 D4C3C7D4 — Enter, cursor=4, "MCGM" raw.
        Trace rec 101: 7D 40C4 D3C9E2E3... — "LISTDS 'DVCA.SOURCE'..."
        The host's screen has no fields; it expects buffer content as
        one stream. Sending SBA here means the host reads 0x11 as DATA
        (DC1 control char) → garbage command → ABEND.

    Formatted screen:          AID + cursor + [SBA addr data]* per field.
        Trace rec 13: 7D 5BF4 11 5BF0 C4E5C3C140 — SBA present.
        Trace rec 65: multiple SBA blocks for multiple modified fields.
    """
    out = bytearray()
    out.append(aid)

    if aid in SHORT_AIDS:
        return bytes(out)  # AID only — no cursor, no data

    out += encode_addr(cursor, mode14)

    if unformatted:
        for _, data in modified:
            out += data
        return bytes(out)

    for start, data in modified:
        out.append(ORD_SBA)
        out += encode_addr(start, mode14)
        out += data

    return bytes(out)


def _encode_attr_byte(f) -> int:
    """Inverse of decode_attr_byte. Builds the SF operand."""
    b = 0xC0  # bits 0-1 always set (graphic-character encoding)
    if f.attrs.protected:
        b |= ATTR_PROTECT
    if f.attrs.numeric:
        b |= ATTR_NUMERIC
    if f.attrs.hidden:
        b |= ATTR_DISP_HIDDEN
    elif f.attrs.intensified:
        b |= ATTR_DISP_INTENSE
    if f.attrs.mdt:
        b |= ATTR_MDT
    return b


def build_read_buffer_reply(screen: VirtualScreen, mode14: bool) -> bytes:
    """Response to RB command: AID_NO + cursor + entire buffer.

    Field attribute positions become SF + attr-byte (2 bytes replace 1).
    Everything else is the cell's char byte. Nulls included (RB doesn't
    suppress nulls, only RM does).

    GA23-0059 §3.5.2.
    """
    out = bytearray()
    out.append(AID_NO)
    out += encode_addr(screen.cursor, mode14)

    # Build a quick lookup: position → Field
    field_at_pos = {f.start: f for f in screen.fields}

    for pos in range(screen.size):
        if pos in field_at_pos:
            out.append(ORD_SF)
            out.append(_encode_attr_byte(field_at_pos[pos]))
        else:
            out.append(screen.cells[pos].char)

    return bytes(out)
