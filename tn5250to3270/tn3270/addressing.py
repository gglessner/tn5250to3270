"""3270 buffer address encoding. GA23-0059 §4.3.1.

12-bit mode (buffers ≤ 4096): each 6-bit half indexes a translation table.
The table is the EBCDIC graphics for 6-bit values — designed so addresses
look like printable EBCDIC characters in dumps.

14-bit mode (buffers > 4096): byte1 = high 6 bits (top 2 bits = 00),
byte2 = low 8 bits. The decoder distinguishes by checking byte1's top bits.
"""

# The 64-entry code table. Index = 6-bit value, entry = wire byte.
# This is GA23-0059 Figure D-1, also in every 3270 reference ever written.
_CODE_TABLE = bytes([
    0x40, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7,
    0xC8, 0xC9, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F,
    0x50, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7,
    0xD8, 0xD9, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F,
    0x60, 0x61, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7,
    0xE8, 0xE9, 0x6A, 0x6B, 0x6C, 0x6D, 0x6E, 0x6F,
    0xF0, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7,
    0xF8, 0xF9, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E, 0x7F,
])

# Inverse: wire byte → 6-bit value. Built once at import.
_DECODE_TABLE = {b: i for i, b in enumerate(_CODE_TABLE)}


def encode_addr(addr: int, mode14: bool) -> bytes:
    """Encode a buffer offset into 2 wire bytes."""
    if mode14:
        if not 0 <= addr < 16384:
            raise ValueError(f"14-bit address out of range: {addr}")
        return bytes([(addr >> 8) & 0x3F, addr & 0xFF])
    else:
        if not 0 <= addr < 4096:
            raise ValueError(f"12-bit address out of range: {addr}")
        hi = (addr >> 6) & 0x3F
        lo = addr & 0x3F
        return bytes([_CODE_TABLE[hi], _CODE_TABLE[lo]])


def decode_addr(b: bytes) -> int:
    """Decode 2 wire bytes into a buffer offset. Auto-detects 12/14-bit."""
    b1, b2 = b[0], b[1]
    if (b1 & 0xC0) == 0x00:
        # 14-bit mode: top 2 bits clear
        return ((b1 & 0x3F) << 8) | b2
    else:
        # 12-bit mode: look up both bytes in the table
        hi = _DECODE_TABLE.get(b1)
        lo = _DECODE_TABLE.get(b2)
        if hi is None or lo is None:
            raise ValueError(f"invalid 12-bit address bytes: {b1:02x} {b2:02x}")
        return (hi << 6) | lo
