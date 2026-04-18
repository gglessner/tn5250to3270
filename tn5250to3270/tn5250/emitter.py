"""Render VirtualScreen → 5250 WTD wire bytes.

This is the load-bearing module. ACS validates strictly — a malformed WTD
gets you a silent disconnect. The strategy here is conservative: full-screen
redraw on every render. Less efficient than delta but bulletproof.

Output structure:
  GDS header (10 bytes)
  [ESC CLEAR_UNIT]                  ← only if erased
  ESC WTD CC1 CC2
    [SBA row col + data...]         ← for each run of non-null cells
    [SF FFW(2) attr(1) len(2)]      ← for each field (input fields only;
                                       output-only fields use just attr byte
                                       in the data stream)
    IC row col                       ← cursor at the end
"""
import logging
from ..screen.model import VirtualScreen
from .gds import pack_gds
from .ffw import encode_ffw, encode_screen_attr
from .constants import (
    ESC, CMD_CLEAR_UNIT, CMD_WTD, CMD_READ_MDT,
    ORD_SBA, ORD_SF, ORD_IC, ORD_RA, ORD_SOH,
    CC1_RESET_MDT_ALL,
    CC2_UNLOCK_KBD, CC2_ALARM,
    OP_PUT_GET, OP_OUTPUT_ONLY,
    SA_GREEN,
)

log = logging.getLogger(__name__)


def render_wtd(screen: VirtualScreen, erased: bool, reset_mdt: bool = False) -> bytes:
    """Build a complete GDS-wrapped WTD record from screen state.

    Args:
      erased: did the host send EW/EWA (vs plain W)? If so, prepend Clear Unit.
      reset_mdt: did the WCC have reset-MDT? Sets CC1 bit.
    """
    payload = bytearray()

    if erased:
        payload += bytes([ESC, CMD_CLEAR_UNIT])

    # ── WTD command + CC bytes ─────────────────────────────────────
    cc1 = CC1_RESET_MDT_ALL if reset_mdt else 0x00
    cc2 = 0x00
    if not screen.keyboard_locked:
        cc2 |= CC2_UNLOCK_KBD
    if screen.alarm:
        cc2 |= CC2_ALARM
    payload += bytes([ESC, CMD_WTD, cc1, cc2])

    # ── SOH: Start of Header — controls F-key data inclusion ───────
    # tn5250j layout (tnvt.java processStartOfHeaderOrder):
    #   len, flag, reserved, reseq, errLine, cmdF17-24, cmdF9-16, cmdF1-8
    # cmdkey bits: 1 = exclude field data with this F-key (send AID only).
    # We want field data on all F-keys → all zeros.
    payload += bytes([
        ORD_SOH,
        0x07,           # 7 bytes follow
        0x00,           # flags (nothing disabled)
        0x00,           # reserved
        0x00,           # resequence (none)
        screen.rows,    # error line = last row
        0x00, 0x00, 0x00,  # cmdkeys: include field data on all F-keys
    ])

    # ── Walk the screen ────────────────────────────────────────────
    payload += _render_screen_body(screen)

    # ── Cursor ─────────────────────────────────────────────────────
    # If cursor lands on a field-attribute cell, bump it forward to the
    # next data position. In 5250 the attr cell isn't typeable; cursor
    # there → ERR_CURSOR_PROTECTED on first keystroke. This happens
    # when the host's IC coincides with where we placed an attr (or
    # didn't send IC at all and EraseAll left cursor at 0).
    cursor = screen.cursor
    if screen.cells[cursor].is_field_attr:
        cursor = screen.wrap(cursor + 1)
    cur_row, cur_col = screen.to_rowcol(cursor)
    payload += bytes([ORD_IC, cur_row + 1, cur_col + 1])

    # ── Read MDT Fields — THE ACTUAL UNLOCK SIGNAL ─────────────────
    # tn5250j (tnvt.java:1525) only sets pendingUnlock=true on receiving
    # CMD_READ_MDT_FIELDS or CMD_READ_INPUT_FIELDS. CC2_UNLOCK_KBD is
    # not enough; PUT_GET opcode is not enough. Without this command,
    # the keyboard stays locked and Enter goes nowhere. Real AS/400s
    # always pair WTD with a Read command.
    if not screen.keyboard_locked:
        payload += bytes([ESC, CMD_READ_MDT, 0x00, 0x00])  # CC bytes ignored

    # ── Wrap in GDS ────────────────────────────────────────────────
    opcode = OP_PUT_GET if not screen.keyboard_locked else OP_OUTPUT_ONLY
    return pack_gds(bytes(payload), opcode=opcode)


def _render_screen_body(screen: VirtualScreen) -> bytes:
    """Walk cells linearly. Emit:
       - SBA when we need to position
       - SF when we hit a field-attr cell
       - data bytes for non-null cells
       - Skip null cells (5250 doesn't need them; CU already cleared)

    The 5250 SF order is more complex than 3270's:
      SF + FFW(2) + [FCW...] + screen_attr(1) + length(2)
    We never emit FCW (no 3270 source for those features).

    Unformatted screens (fields=[]) are NOT handled here — the session
    layer adds a synthetic field to the model BEFORE calling us. That
    way set_field_data() finds it and the user's input flows back to
    the host. We tried emitting wire-only synthetic SF orders, but the
    field existed in tn5250j and not in our model: typed input was
    silently dropped by set_field_data, host got blank, "Invalid" loop.
    """
    out = bytearray()
    pos = 0
    cursor_at = -1  # where we last positioned via SBA; -1 = need SBA

    # Quick lookup: position → Field
    field_at_pos = {f.start: f for f in screen.fields}

    # ── 3270 wrap-around attribute synthesis ───────────────────────
    # In 3270, fields wrap circularly: position 0 belongs to the LAST
    # field if there's no field starting at 0. That field's color
    # extends into row 0. 5250 has no wrap — after Clear Unit, row 0
    # stays at default (green) unless we explicitly paint it.
    #
    # Verified against pentest.db: records 40/42/50/60 have first field
    # at pos 80+ (row 1), row 0 all nulls. The wrapping field is RED/
    # WHITE/BLUE. Real 3270 terminal shows row 0 in that color; we
    # showed green.
    #
    # Fix: when pos 0 is null AND has no field AND the wrapping field
    # is non-default, emit an inline attribute byte at (1,1). tn5250j's
    # setAttr() propagates it forward until the next attribute place
    # (the first real SF). Cost: pos 0 becomes the attr byte (renders
    # blank) — but it was already null, so no visible change.
    if (screen.fields
            and 0 not in field_at_pos
            and screen.cells[0].char == 0x00):
        wrap_field = screen.field_at(0)
        if wrap_field is not None:
            wrap_attr = encode_screen_attr(wrap_field.attrs)
            if wrap_attr != SA_GREEN:  # only if it'd change anything
                out += bytes([ORD_SBA, 1, 1, wrap_attr])
                # tn5250j's setAttr advances lastPos, so we're now at
                # pos 1. Skip pos 0 in our walk (it's the attr byte now).
                pos = 1
                cursor_at = 1

    while pos < screen.size:
        cell = screen.cells[pos]

        if pos in field_at_pos:
            # Field starts here. Position then emit SF.
            f = field_at_pos[pos]
            row, col = screen.to_rowcol(pos)
            if cursor_at != pos:
                out += bytes([ORD_SBA, row + 1, col + 1])
            # SF FFW(2) screen_attr(1) length(2)
            ffw = encode_ffw(f.attrs)
            sa = encode_screen_attr(f.attrs)
            data_len = f.data_length
            out += bytes([ORD_SF])
            out += ffw
            out.append(sa)
            out += bytes([(data_len >> 8) & 0xFF, data_len & 0xFF])
            # SF positions us at the cell AFTER the attribute
            cursor_at = pos + 1
            pos += 1
            continue

        if cell.char == 0x00:
            # Null cell: skip. Next non-null will need an SBA.
            cursor_at = -1
            pos += 1
            continue

        # Non-null data cell. Position if needed, then emit a run.
        if cursor_at != pos:
            row, col = screen.to_rowcol(pos)
            out += bytes([ORD_SBA, row + 1, col + 1])
            cursor_at = pos
        # Collect a run of non-null, non-field-attr cells
        run_start = pos
        while (pos < screen.size and
               screen.cells[pos].char != 0x00 and
               pos not in field_at_pos):
            pos += 1
        run = bytes(screen.cells[i].char for i in range(run_start, pos))
        out += run
        cursor_at = pos

    return bytes(out)
