import pytest
from tn5250to3270.tn3270.constants import decode_attr_byte, decode_sfe_pairs
from tn5250to3270.screen.field import FieldAttrs
from tn5250to3270.screen.cell import Color, Hilite


def test_decode_attr_unprotected_normal():
    # 0x40: bits 0-1 set (always), rest clear
    a = decode_attr_byte(0x40)
    assert a.protected is False
    assert a.numeric is False
    assert a.hidden is False
    assert a.intensified is False
    assert a.mdt is False


def test_decode_attr_protected_intensified():
    # 0xE8: protect(0x20) + intensified-display(0x08)
    a = decode_attr_byte(0xE8)
    assert a.protected is True
    assert a.intensified is True
    assert a.hidden is False


def test_decode_attr_hidden():
    # 0x4C: display bits = 11 = non-display
    a = decode_attr_byte(0x4C)
    assert a.hidden is True
    assert a.intensified is False  # hidden overrides


def test_decode_attr_mdt():
    a = decode_attr_byte(0x41)  # MDT bit
    assert a.mdt is True


def test_decode_sfe_color():
    # SFE pairs: [(XA_3270, 0x40), (XA_FG, 0xF2)]
    a = decode_sfe_pairs([(0xC0, 0x40), (0x42, 0xF2)])
    assert a.fg == Color.RED
    assert a.protected is False


def test_decode_sfe_hilite():
    a = decode_sfe_pairs([(0xC0, 0x60), (0x41, 0xF4)])  # protected + underline
    assert a.protected is True
    assert a.hilite == Hilite.UNDERLINE


# ── Task 3.4: parser core — command dispatch + simple orders ───────

from tn5250to3270.tn3270.parser import parse, ParseResult
from tn5250to3270.tn3270.constants import (
    CMD_EW, CMD_W, ORD_SBA, ORD_SF, ORD_IC,
    WCC_UNLOCK, WCC_RESET_MDT, WCC_ALARM,
)
from tn5250to3270.screen import ops


def test_parse_ew_minimal():
    """Smallest valid stream: EW + WCC, no orders."""
    stream = bytes([CMD_EW, 0xC3])  # WCC: reset_mdt + unlock
    r = parse(stream)
    assert r.command == CMD_EW
    assert isinstance(r.ops[0], ops.EraseAll)  # EW always erases first
    assert isinstance(r.ops[1], ops.WccFlags)
    assert r.ops[1].reset_mdt is True
    assert r.ops[1].unlock_kbd is True
    assert r.ops[1].alarm is False


def test_parse_w_does_not_erase():
    stream = bytes([CMD_W, 0xC2])  # plain Write, unlock only
    r = parse(stream)
    assert not any(isinstance(o, ops.EraseAll) for o in r.ops)
    assert isinstance(r.ops[0], ops.WccFlags)
    assert r.ops[0].unlock_kbd is True


def test_parse_sba_text():
    """EW, WCC, SBA to (1,1)=80, then text 'HI'."""
    # addr 80 → table[1]=0xC1, table[16]=0x50
    stream = bytes([CMD_EW, 0xC3, ORD_SBA, 0xC1, 0x50, 0xC8, 0xC9])
    r = parse(stream)
    # ops: EraseAll, WccFlags, SetBufferAddr(80), WriteText(b'\xc8\xc9')
    assert isinstance(r.ops[2], ops.SetBufferAddr)
    assert r.ops[2].pos == 80
    assert isinstance(r.ops[3], ops.WriteText)
    assert r.ops[3].data == b"\xc8\xc9"


def test_parse_sf():
    """SF places a field attribute."""
    # SF + attr byte 0x60 (protected, no MDT)
    stream = bytes([CMD_EW, 0xC3, ORD_SBA, 0x40, 0x40, ORD_SF, 0x60])
    r = parse(stream)
    df = [o for o in r.ops if isinstance(o, ops.DefineField)][0]
    assert df.pos == 0
    assert df.attrs.protected is True


def test_parse_ic():
    """IC sets cursor at current write pointer."""
    stream = bytes([CMD_EW, 0xC3, ORD_SBA, 0xC1, 0x50, ORD_IC])  # SBA 80, IC
    r = parse(stream)
    sc = [o for o in r.ops if isinstance(o, ops.SetCursor)][0]
    assert sc.pos == 80


def test_parse_text_runs_coalesced():
    """Consecutive data bytes become one WriteText, not many."""
    stream = bytes([CMD_W, 0xC2]) + b"\xc1\xc2\xc3\xc4"
    r = parse(stream)
    texts = [o for o in r.ops if isinstance(o, ops.WriteText)]
    assert len(texts) == 1
    assert texts[0].data == b"\xc1\xc2\xc3\xc4"


def test_parse_local_command_variants():
    """Some hosts send 0x05 instead of 0xF5 for EW."""
    stream = bytes([0x05, 0xC3])  # CMD_EW_LOCAL
    r = parse(stream)
    assert r.command == 0x05
    assert isinstance(r.ops[0], ops.EraseAll)


# ── Task 3.5: remaining orders — SFE, RA, EUA, PT, SA, MF, GE ──────

from tn5250to3270.tn3270.constants import ORD_SFE, ORD_RA, ORD_EUA, ORD_PT, ORD_SA, ORD_GE


def test_parse_sfe():
    """SFE: count byte + (type,value) pairs."""
    # SFE, 2 pairs: (XA_3270=0xC0, attr=0x60), (XA_FG=0x42, RED=0xF2)
    stream = bytes([CMD_EW, 0xC3, ORD_SFE, 0x02, 0xC0, 0x60, 0x42, 0xF2])
    r = parse(stream)
    df = [o for o in r.ops if isinstance(o, ops.DefineField)][0]
    assert df.attrs.protected is True
    assert df.attrs.fg == Color.RED


def test_parse_ra():
    """RA: 2 addr bytes + 1 char to repeat."""
    # SBA 0, RA to addr 10, char 0x40 (space)
    stream = bytes([CMD_EW, 0xC3,
                    ORD_SBA, 0x40, 0x40,        # SBA 0
                    ORD_RA, 0x40, 0x4A, 0x40])  # RA to 10 (table[0]=0x40, table[10]=0x4A), space
    r = parse(stream)
    ra = [o for o in r.ops if isinstance(o, ops.RepeatChar)][0]
    assert ra.to_pos == 10
    assert ra.char == 0x40


def test_parse_ra_with_ge_char():
    """RA can repeat a GE character: RA addr addr GE char."""
    stream = bytes([CMD_EW, 0xC3, ORD_RA, 0x40, 0x4A, ORD_GE, 0xAD])
    r = parse(stream)
    ra = [o for o in r.ops if isinstance(o, ops.RepeatChar)][0]
    # GE char gets substituted to '?' (0x6F) per spec §9 unmappable policy
    assert ra.char == 0x6F


def test_parse_eua():
    stream = bytes([CMD_W, 0xC2, ORD_EUA, 0x40, 0x4A])  # EUA to addr 10
    r = parse(stream)
    eua = [o for o in r.ops if isinstance(o, ops.EraseUnprotected)][0]
    assert eua.to_pos == 10


def test_parse_pt():
    stream = bytes([CMD_W, 0xC2, ORD_PT])
    r = parse(stream)
    assert any(isinstance(o, ops.ProgramTab) for o in r.ops)


def test_parse_sa():
    """SA: type + value, char-level attribute."""
    stream = bytes([CMD_W, 0xC2, ORD_SA, 0x42, 0xF4])  # SA fg=green
    r = parse(stream)
    sa = [o for o in r.ops if isinstance(o, ops.SetExtAttr)][0]
    assert sa.attr_type == 0x42
    assert sa.value == 0xF4


def test_parse_ge_inline():
    """GE in text stream → substitute '?' (0x6F)."""
    stream = bytes([CMD_W, 0xC2, 0xC1, ORD_GE, 0xAD, 0xC2])  # A <GE x> B
    r = parse(stream)
    text = b"".join(o.data for o in r.ops if isinstance(o, ops.WriteText))
    assert text == b"\xC1\x6F\xC2"


def test_parse_sub_0x40_passed_through():
    """Bytes < 0x40 not in ORDER_BYTES are data, not unknown orders.
    Some hosts pad with nulls/low bytes — pass them through."""
    # 0x06 is not in ORDER_BYTES → treated as data, no warning.
    stream = bytes([CMD_W, 0xC2, 0xC1, 0x06, 0xC2])
    r = parse(stream)
    text = b"".join(o.data for o in r.ops if isinstance(o, ops.WriteText))
    assert text == b"\xC1\x06\xC2"  # passed through as data
    assert not any("0x06" in w for w in r.warnings)
