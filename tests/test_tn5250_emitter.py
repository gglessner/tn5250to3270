import pytest
from tn5250to3270.tn5250.emitter import render_wtd
from tn5250to3270.tn5250.constants import (
    ESC, CMD_CLEAR_UNIT, CMD_WTD, ORD_SBA, ORD_SF, ORD_IC,
    CC2_UNLOCK_KBD, CC2_ALARM, SA_GREEN, SA_NONDISPLAY,
    OP_PUT_GET, OP_OUTPUT_ONLY,
)
from tn5250to3270.tn5250.gds import unpack_gds
from tn5250to3270.screen.model import VirtualScreen
from tn5250to3270.screen.field import FieldAttrs
from tn5250to3270.screen import ops


def make_screen(rows=24, cols=80):
    s = VirtualScreen(rows, cols)
    s.apply(ops.EraseAll())
    return s


def test_empty_screen_minimal_wtd():
    """Blank screen, keyboard unlocked → CU + WTD with just CC bytes + IC."""
    s = make_screen()
    s.keyboard_locked = False
    s.cursor = 0
    out = render_wtd(s, erased=True)
    # Should be a GDS record
    h, payload = unpack_gds(out)
    assert h.opcode == OP_PUT_GET  # unlocked → expect input
    # Payload starts: ESC CU ESC WTD CC1 CC2 ...
    assert payload[0] == ESC
    assert payload[1] == CMD_CLEAR_UNIT
    assert payload[2] == ESC
    assert payload[3] == CMD_WTD
    cc1, cc2 = payload[4], payload[5]
    assert cc2 & CC2_UNLOCK_KBD  # unlock bit set


def test_locked_screen_uses_output_only():
    s = make_screen()
    s.keyboard_locked = True
    out = render_wtd(s, erased=True)
    h, _ = unpack_gds(out)
    assert h.opcode == OP_OUTPUT_ONLY


def test_screen_with_text():
    s = make_screen()
    s.apply(ops.SetBufferAddr(pos=80))   # row 1 col 0 (0-based)
    s.apply(ops.WriteText(data=b"\xc8\xc9"))  # HI
    s.keyboard_locked = False
    out = render_wtd(s, erased=True)
    _, payload = unpack_gds(out)
    # No fields → synthetic full-screen input field at (1,1) emitted first.
    # The text data at pos 80 still appears with SBA(2,1). Find THAT one.
    assert bytes([ORD_SBA, 2, 1]) + b"\xc8\xc9" in payload


def test_cursor_bumped_off_attr_cell():
    """If cursor would land on a field-attribute cell, bump it to the
    next position. Attribute cells aren't typeable in 5250 →
    ERR_CURSOR_PROTECTED on first keystroke. Common case: synthetic
    field added at pos 0 by session layer for unformatted screens,
    host's EraseAll left cursor at 0."""
    s = make_screen()
    # Simulate what the session does: add a field at pos 0
    s.apply(ops.DefineField(pos=0, attrs=FieldAttrs(protected=False)))
    s.cursor = 0  # on the attr byte
    s.keyboard_locked = False
    out = render_wtd(s, erased=True)
    _, payload = unpack_gds(out)
    # IC should be at (1,2), not (1,1)
    ic_idx = payload.rindex(ORD_IC)
    assert payload[ic_idx + 1] == 1
    assert payload[ic_idx + 2] == 2  # bumped from col 1 to col 2


def test_screen_with_field():
    """Field at pos 10: emitter places SF order with FFW + attr + length."""
    s = make_screen()
    s.apply(ops.DefineField(pos=10, attrs=FieldAttrs(protected=False)))
    s.apply(ops.DefineField(pos=30, attrs=FieldAttrs(protected=True)))
    s.keyboard_locked = False
    out = render_wtd(s, erased=True)
    _, payload = unpack_gds(out)
    body = payload[6:]
    # Find first SF
    idx = body.index(ORD_SF)
    # SF + FFW(2) + screen-attr(1) + length(2)
    ffw_b0 = body[idx+1]
    assert (ffw_b0 & 0xC0) == 0x40  # FFW marker
    assert (ffw_b0 & 0x20) == 0x00  # not bypass (unprotected)
    sa = body[idx+3]
    assert sa == SA_GREEN
    field_len = (body[idx+4] << 8) | body[idx+5]
    assert field_len == 19  # data_length: 30 - 10 - 1 = 19


def test_hidden_field_uses_nondisplay():
    s = make_screen()
    s.apply(ops.DefineField(pos=10, attrs=FieldAttrs(hidden=True)))
    s.keyboard_locked = False
    out = render_wtd(s, erased=True)
    _, payload = unpack_gds(out)
    body = payload[6:]
    idx = body.index(ORD_SF)
    sa = body[idx+3]
    assert sa == SA_NONDISPLAY


def test_cursor_position():
    s = make_screen()
    s.cursor = 165  # row 2 col 5 (0-based) → 1-based: row 3 col 6
    s.keyboard_locked = False
    out = render_wtd(s, erased=True)
    _, payload = unpack_gds(out)
    # IC should be near the end
    body = payload[6:]
    idx = body.rindex(ORD_IC)
    assert body[idx+1] == 3
    assert body[idx+2] == 6


def test_alarm_flag():
    s = make_screen()
    s.alarm = True
    s.keyboard_locked = False
    out = render_wtd(s, erased=True)
    _, payload = unpack_gds(out)
    cc2 = payload[5]
    assert cc2 & CC2_ALARM


def test_no_clear_unit_when_not_erased():
    """Plain Write (not Erase/Write) → no Clear Unit prefix."""
    s = make_screen()
    s.keyboard_locked = False
    out = render_wtd(s, erased=False)
    _, payload = unpack_gds(out)
    # First command should be WTD, not CU
    assert payload[0] == ESC
    assert payload[1] == CMD_WTD  # NOT CMD_CLEAR_UNIT
