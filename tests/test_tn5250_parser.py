import pytest
from tn5250to3270.tn5250.parser import parse_inbound, InboundResult
from tn5250to3270.tn5250.gds import pack_gds
from tn5250to3270.tn5250.constants import AID5_ENTER, AID5_F3, AID5_CLEAR, ORD_SBA


def test_parse_aid_only():
    """Just cursor + AID, no fields modified."""
    # cursor row=5 col=10 (1-based), AID=Enter
    payload = bytes([5, 10, AID5_ENTER])
    record = pack_gds(payload, opcode=0)
    r = parse_inbound(record)
    assert r.aid == AID5_ENTER
    assert r.cursor_row == 5
    assert r.cursor_col == 10
    assert r.fields == []


def test_parse_one_field():
    payload = bytes([1, 1, AID5_ENTER,
                     ORD_SBA, 3, 7]) + b"\xc8\xc9"  # field at row 3 col 7, data HI
    record = pack_gds(payload, opcode=0)
    r = parse_inbound(record)
    assert len(r.fields) == 1
    assert r.fields[0] == (3, 7, b"\xc8\xc9")


def test_parse_multiple_fields():
    payload = bytes([1, 1, AID5_F3,
                     ORD_SBA, 2, 5]) + b"\xc1" + \
              bytes([ORD_SBA, 4, 1]) + b"\xc2\xc3\xc4"
    record = pack_gds(payload, opcode=0)
    r = parse_inbound(record)
    assert r.aid == AID5_F3
    assert r.fields == [(2, 5, b"\xc1"), (4, 1, b"\xc2\xc3\xc4")]


def test_parse_bad_gds_raises():
    with pytest.raises(ValueError):
        parse_inbound(b"\x00\x05garbage")


# ── AID translation map ─────────────────────────────────────────────
from tn5250to3270.tn5250.constants import (
    AID_MAP_5250_TO_3270, AID5_F12,
    AID5_ROLL_UP, AID5_ROLL_DOWN, AID5_HELP, AID5_PRINT,
)
from tn5250to3270.tn3270.constants import (
    AID_ENTER, AID_PF3, AID_PF12, AID_PF7, AID_PF8, AID_PF1, AID_CLEAR,
)


def test_aid_map_direct():
    assert AID_MAP_5250_TO_3270[AID5_ENTER] == AID_ENTER
    assert AID_MAP_5250_TO_3270[AID5_F3] == AID_PF3
    assert AID_MAP_5250_TO_3270[AID5_F12] == AID_PF12
    assert AID_MAP_5250_TO_3270[AID5_CLEAR] == AID_CLEAR


def test_aid_map_roll_to_pf78():
    # Roll Up shows next page = PF8 (down) in ISPF convention
    assert AID_MAP_5250_TO_3270[AID5_ROLL_UP] == AID_PF8
    assert AID_MAP_5250_TO_3270[AID5_ROLL_DOWN] == AID_PF7


def test_aid_map_help_to_pf1():
    assert AID_MAP_5250_TO_3270[AID5_HELP] == AID_PF1


def test_aid_map_print_unmapped():
    assert AID5_PRINT not in AID_MAP_5250_TO_3270


@pytest.mark.parametrize("i", range(1, 25))
def test_aid_map_all_24_fkeys(i):
    from tn5250to3270.tn5250 import constants as c5
    from tn5250to3270.tn3270 import constants as c3
    a5 = getattr(c5, f"AID5_F{i}")
    a3 = getattr(c3, f"AID_PF{i}")
    assert AID_MAP_5250_TO_3270[a5] == a3
