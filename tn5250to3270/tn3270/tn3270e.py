"""TN3270E 5-byte record header. RFC 2355 §3.5.

Layout:
  byte 0: data-type
  byte 1: request-flag
  byte 2: response-flag
  byte 3-4: sequence number (big-endian)
"""
import struct
from dataclasses import dataclass

# Data types (RFC 2355 §3.5.1)
DT_3270_DATA    = 0x00
DT_SCS_DATA     = 0x01   # printer — we drop these
DT_RESPONSE     = 0x02
DT_BIND_IMAGE   = 0x03
DT_UNBIND       = 0x04
DT_NVT_DATA     = 0x05
DT_REQUEST      = 0x06
DT_SSCP_LU_DATA = 0x07
DT_PRINT_EOJ    = 0x08

# Request flags
RQ_NONE   = 0x00   # no response wanted
RQ_ERROR  = 0x01   # respond only on error
RQ_ALWAYS = 0x02   # always respond

# Response flags (in DT_RESPONSE records)
RSP_POSITIVE = 0x00   # POSITIVE-DEVICE-END
RSP_NEGATIVE = 0x01   # NEGATIVE-DEVICE-END (followed by 1 byte reason in data)


@dataclass(frozen=True, slots=True)
class EHeader:
    data_type: int
    request_flag: int
    response_flag: int
    seq: int


def pack_header(h: EHeader) -> bytes:
    return struct.pack(">BBBH", h.data_type, h.request_flag,
                       h.response_flag, h.seq)


def unpack_header(b: bytes) -> EHeader:
    dt, rq, rsp, seq = struct.unpack(">BBBH", b[:5])
    return EHeader(data_type=dt, request_flag=rq, response_flag=rsp, seq=seq)


def build_positive_response(seq: int) -> bytes:
    """Build a complete POSITIVE-DEVICE-END response record (header + 1 data byte)."""
    h = EHeader(data_type=DT_RESPONSE, request_flag=0,
                response_flag=RSP_POSITIVE, seq=seq)
    return pack_header(h) + bytes([0x00])  # PDE has 1 trailing byte = 0x00
