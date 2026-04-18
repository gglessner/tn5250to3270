import pytest
from tn5250to3270.tn3270.emitter import build_inbound, build_read_buffer_reply
from tn5250to3270.tn3270.constants import AID_ENTER, AID_CLEAR, AID_PF3, ORD_SBA, ORD_SF
from tn5250to3270.tn3270.addressing import encode_addr
from tn5250to3270.screen.model import VirtualScreen
from tn5250to3270.screen.field import FieldAttrs
from tn5250to3270.screen import ops


def test_short_aid_sends_aid_byte_only():
    """Clear/PA1-3: AID byte ONLY. No cursor. Verified against real wire
    trace (pentest.db rec 35: Clear = 6D FFEF, just 1 byte + EOR).
    GA23-0059 §3.5.6: 'only the AID byte itself is transferred inbound.'"""
    out = build_inbound(AID_CLEAR, cursor=80, modified=[(0, b"\xc1\xc2")],
                        mode14=False)
    assert out == bytes([AID_CLEAR])  # 1 byte, period


def test_unformatted_inbound_no_sba():
    """Unformatted screen: AID + cursor + raw data, NO SBA orders.
    Verified against pentest.db rec 39: 7D 40C4 D4C3C7D4 = Enter,
    cursor=4, 'MCGM' raw. Sending SBA on unformatted → host reads
    0x11 as data (DC1 control char) → ABEND."""
    out = build_inbound(AID_ENTER, cursor=4,
                        modified=[(1, b"\xd4\xc3\xc7\xd4")],  # MCGM
                        mode14=False, unformatted=True)
    # AID + cursor + raw data — NO 0x11 SBA byte
    assert out == bytes([AID_ENTER]) + encode_addr(4, mode14=False) + b"\xd4\xc3\xc7\xd4"
    assert 0x11 not in out[3:]  # no SBA after cursor


def test_formatted_inbound_uses_sba():
    """Formatted screen: SBA per field. pentest.db rec 13:
    7D 5BF4 11 5BF0 C4E5C3C140 — SBA present before field data."""
    out = build_inbound(AID_ENTER, cursor=42,
                        modified=[(10, b"\xc1\xc2")],
                        mode14=False, unformatted=False)
    assert ORD_SBA in out  # 0x11 must be present


def test_enter_with_one_field():
    out = build_inbound(AID_ENTER, cursor=42,
                        modified=[(10, b"\xc8\xc9")], mode14=False)
    expected = (
        bytes([AID_ENTER]) +
        encode_addr(42, mode14=False) +
        bytes([ORD_SBA]) + encode_addr(10, mode14=False) +
        b"\xc8\xc9"
    )
    assert out == expected


def test_enter_with_multiple_fields():
    out = build_inbound(AID_ENTER, cursor=0,
                        modified=[(10, b"\xc1"), (50, b"\xc2\xc3")], mode14=False)
    expected = (
        bytes([AID_ENTER]) +
        encode_addr(0, mode14=False) +
        bytes([ORD_SBA]) + encode_addr(10, mode14=False) + b"\xc1" +
        bytes([ORD_SBA]) + encode_addr(50, mode14=False) + b"\xc2\xc3"
    )
    assert out == expected


def test_enter_no_modified_fields():
    """Even with no MDT fields, send AID + cursor."""
    out = build_inbound(AID_ENTER, cursor=0, modified=[], mode14=False)
    assert out == bytes([AID_ENTER]) + encode_addr(0, mode14=False)


def test_14bit_mode():
    """Large screens use 14-bit addressing throughout."""
    out = build_inbound(AID_PF3, cursor=5000, modified=[], mode14=True)
    assert out == bytes([AID_PF3]) + encode_addr(5000, mode14=True)


def test_read_buffer_reply():
    """RB reply: AID_NO + cursor + entire buffer with SF orders for fields."""
    s = VirtualScreen(24, 80)
    s.apply(ops.EraseAll())
    s.apply(ops.DefineField(pos=0, attrs=FieldAttrs(protected=True)))
    s.apply(ops.SetBufferAddr(pos=1))
    s.apply(ops.WriteText(data=b"\xc1\xc2"))
    s.cursor = 5
    out = build_read_buffer_reply(s, mode14=False)
    # Starts with AID_NO + cursor
    assert out[0] == 0x60  # AID_NO
    assert out[1:3] == encode_addr(5, mode14=False)
    # Then SF + attr at pos 0
    assert out[3] == ORD_SF
    # Attr byte: 0xC0 base | 0x20 PROTECT = 0xE0
    assert out[4] == 0xE0
    # Then data — A B then nulls...
    assert out[5] == 0xC1
    assert out[6] == 0xC2
    assert out[7] == 0x00  # null
    # Length: 3 header + (SF + attr) replaces 1 cell + 1919 remaining cells
    # = 3 + 2 + 1919 = 1924
    assert len(out) == 3 + 2 + 1919
