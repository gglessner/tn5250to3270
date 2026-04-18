"""GDS (General Data Stream) header. RFC 1205 §4.

Every TN5250 record:
  bytes 0-1: LL — total length (big-endian, includes these 2 bytes)
  bytes 2-3: 0x12A0 — GDS record type
  bytes 4-5: reserved (0x0000)
  byte  6:   variable header length (always 0x04 for us)
  bytes 7-8: flags (bit 0 of byte 7 = ERR, bit 1 = ATN, others reserved)
  byte  9:   opcode
  bytes 10+: payload
"""
import struct
from dataclasses import dataclass
from .constants import GDS_TYPE, GDS_VARHDR_LEN

HEADER_LEN = 10


@dataclass(frozen=True, slots=True)
class GDSHeader:
    length: int
    flags: int
    opcode: int
    # RFC 1205 §4.3 byte 7 (high byte of flags as >H), IBM bit numbering:
    err: bool = False   # bit 0 = 0x8000
    atn: bool = False   # bit 1 = 0x4000  Attention
    srq: bool = False   # bit 5 = 0x0400  System Request (Esc key)
    trq: bool = False   # bit 6 = 0x0200  Test Request
    hlp: bool = False   # bit 7 = 0x0100  Help


def pack_gds(payload: bytes, opcode: int, err: bool = False, atn: bool = False) -> bytes:
    flags = 0
    if err:
        flags |= 0x8000
    if atn:
        flags |= 0x4000
    total = HEADER_LEN + len(payload)
    return struct.pack(">HHHBHB",
        total, GDS_TYPE, 0x0000, GDS_VARHDR_LEN, flags, opcode
    ) + payload


def unpack_gds(record: bytes) -> tuple[GDSHeader, bytes]:
    if len(record) < HEADER_LEN:
        raise ValueError(f"GDS record too short: {len(record)} bytes")
    ll, sig, _resv, vhl, flags, opcode = struct.unpack(">HHHBHB", record[:HEADER_LEN])
    if sig != GDS_TYPE:
        raise ValueError(f"not a GDS record: signature 0x{sig:04x}")
    if vhl != GDS_VARHDR_LEN:
        # Some implementations use different var-hdr lengths. Be tolerant:
        # the actual var-hdr is bytes 7..(7+vhl). Recompute payload start.
        payload_start = 7 + vhl
    else:
        payload_start = HEADER_LEN
    h = GDSHeader(
        length=ll, flags=flags, opcode=opcode,
        err=bool(flags & 0x8000),
        atn=bool(flags & 0x4000),
        srq=bool(flags & 0x0400),
        trq=bool(flags & 0x0200),
        hlp=bool(flags & 0x0100),
    )
    return h, record[payload_start:ll]
