import pytest
from tn5250to3270.tn5250.gds import pack_gds, unpack_gds, GDSHeader
from tn5250to3270.tn5250.constants import OP_PUT_GET, OP_OUTPUT_ONLY


def test_pack_minimal():
    """Empty payload, PUT/GET opcode."""
    out = pack_gds(b"", opcode=OP_PUT_GET)
    # 10-byte header: LL(2) + 0x12A0(2) + 0x0000(2) + 0x04(1) + flags(2) + opcode(1)
    assert len(out) == 10
    assert out[0:2] == bytes([0x00, 0x0A])    # LL = 10
    assert out[2:4] == bytes([0x12, 0xA0])
    assert out[4:6] == bytes([0x00, 0x00])    # reserved
    assert out[6] == 0x04                      # var-hdr len
    assert out[7:9] == bytes([0x00, 0x00])    # flags
    assert out[9] == OP_PUT_GET


def test_pack_with_payload():
    out = pack_gds(b"\x04\x11\x00\x08", opcode=OP_PUT_GET)  # ESC WTD CC1 CC2
    assert len(out) == 14
    assert out[0:2] == bytes([0x00, 0x0E])  # LL = 14
    assert out[10:14] == b"\x04\x11\x00\x08"


def test_unpack_roundtrip():
    payload = b"\x04\x11\x00\x08\x11\x01\x01\xc1\xc2"
    packed = pack_gds(payload, opcode=OP_OUTPUT_ONLY)
    h, data = unpack_gds(packed)
    assert h.opcode == OP_OUTPUT_ONLY
    assert h.length == len(packed)
    assert data == payload


def test_unpack_real_acs_capture():
    """Captured from ACS connecting to a real IBM i — first inbound record."""
    # This is a Read MDT response: cursor at (1,1), AID=Enter, no fields
    # GDS hdr (10) + cursor row(1) col(1) + AID(1)
    raw = bytes([0x00, 0x0D, 0x12, 0xA0, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00,
                 0x01, 0x01, 0xF1])
    h, data = unpack_gds(raw)
    assert h.opcode == 0x00  # client doesn't set opcode in inbound
    assert data == bytes([0x01, 0x01, 0xF1])


def test_unpack_bad_signature():
    raw = bytes([0x00, 0x0A, 0xDE, 0xAD, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00])
    with pytest.raises(ValueError, match="GDS"):
        unpack_gds(raw)


def test_unpack_truncated():
    with pytest.raises(ValueError, match="short"):
        unpack_gds(b"\x00\x05")
