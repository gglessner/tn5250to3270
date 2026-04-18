import pytest
from tn5250to3270.tn3270.addressing import encode_addr, decode_addr


def test_encode_zero():
    # Address 0: high6=0, low6=0 → table[0], table[0] = 0x40, 0x40
    assert encode_addr(0, mode14=False) == bytes([0x40, 0x40])


def test_decode_zero():
    assert decode_addr(bytes([0x40, 0x40])) == 0


def test_encode_decode_roundtrip_12bit():
    for addr in range(4096):
        enc = encode_addr(addr, mode14=False)
        assert len(enc) == 2
        assert decode_addr(enc) == addr


def test_encode_addr_80():
    # Row 1, col 0 (24x80) = address 80
    # 80 = 0b000001_010000 → high6=1, low6=16
    # table[1] = 0xC1, table[16] = 0x50
    assert encode_addr(80, mode14=False) == bytes([0xC1, 0x50])


def test_encode_14bit_large():
    # 14-bit: addr 5000 doesn't fit in 12 bits
    # encoding: byte1 = (addr >> 8) & 0x3F, byte2 = addr & 0xFF
    # high two bits of byte1 must be 00 to indicate 14-bit mode
    enc = encode_addr(5000, mode14=True)
    assert (enc[0] & 0xC0) == 0x00  # 14-bit flag
    assert decode_addr(enc) == 5000


def test_decode_auto_detects_mode():
    # 12-bit: first byte has bits 6-7 != 00 (table values are >= 0x40)
    # 14-bit: first byte has bits 6-7 == 00
    assert decode_addr(bytes([0x40, 0x40])) == 0       # 12-bit
    assert decode_addr(bytes([0x00, 0x50])) == 80      # 14-bit: 0<<8 | 80
    assert decode_addr(bytes([0x13, 0x88])) == 5000    # 14-bit: 0x13<<8 | 0x88


def test_encode_addr_too_large_raises():
    with pytest.raises(ValueError):
        encode_addr(20000, mode14=True)  # > 16383
