"""Parse 5250 inbound (client→server) records.

Format (after GDS header):
  cursor_row(1) cursor_col(1) AID(1) [SBA row col data...]*

Row/col are 1-based on the wire. We keep them 1-based in InboundResult
because the Session layer converts to linear addresses (which needs to know
screen geometry, which the parser doesn't).

RFC 1205 §6.
"""
from dataclasses import dataclass, field
from .gds import unpack_gds
from .constants import ORD_SBA


@dataclass
class InboundResult:
    aid: int
    cursor_row: int   # 1-based, as on wire
    cursor_col: int   # 1-based
    fields: list[tuple[int, int, bytes]] = field(default_factory=list)
    # fields = [(row, col, data), ...] — row/col 1-based


def parse_inbound(record: bytes) -> InboundResult:
    h, payload = unpack_gds(record)

    if len(payload) < 3:
        raise ValueError(f"5250 inbound too short: {len(payload)} bytes")

    cursor_row = payload[0]
    cursor_col = payload[1]
    aid = payload[2]

    fields: list[tuple[int, int, bytes]] = []
    i = 3
    n = len(payload)
    while i < n:
        if payload[i] == ORD_SBA:
            if i + 2 >= n:
                break  # truncated SBA, stop
            row, col = payload[i+1], payload[i+2]
            # Collect data until next SBA or end
            j = i + 3
            while j < n and payload[j] != ORD_SBA:
                j += 1
            data = payload[i+3:j]
            fields.append((row, col, data))
            i = j
        else:
            # Unexpected byte before first SBA — skip
            i += 1

    return InboundResult(aid=aid, cursor_row=cursor_row,
                         cursor_col=cursor_col, fields=fields)
