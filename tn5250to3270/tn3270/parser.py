"""3270 outbound (host→terminal) data stream parser.

Input: bytes from a single record (between EORs, after telnet stripping,
       after TN3270E header removal if applicable).
Output: ParseResult with command byte + list[ScreenOp].

GA23-0059 chapter 4 is the reference for order parsing.
"""
import logging
from dataclasses import dataclass, field
from ..screen import ops
from ..screen.field import FieldAttrs
from .addressing import decode_addr
from .constants import (
    CMD_W, CMD_EW, CMD_EWA, CMD_RB, CMD_RM, CMD_RMA, CMD_EAU, CMD_WSF,
    CMD_W_LOCAL, CMD_EW_LOCAL, CMD_EWA_LOCAL, CMD_RB_LOCAL, CMD_RM_LOCAL, CMD_WSF_LOCAL,
    WCC_RESET_MDT, WCC_UNLOCK, WCC_ALARM,
    ORD_SF, ORD_SFE, ORD_SBA, ORD_SA, ORD_MF, ORD_IC, ORD_PT, ORD_RA, ORD_EUA, ORD_GE,
    ORDER_BYTES,
    decode_attr_byte, decode_sfe_pairs,
)

log = logging.getLogger(__name__)


@dataclass
class ParseResult:
    command: int
    # Note: annotation quoted because the field name `ops` shadows the
    # module `ops` during class-body evaluation in Python 3.13.
    ops: "list[ops.ScreenOp]" = field(default_factory=list)
    is_read: bool = False         # RB/RM/RMA — host wants data back
    is_wsf: bool = False          # Write Structured Field — handle separately
    wsf_data: bytes = b""
    warnings: list[str] = field(default_factory=list)


# Normalize local→remote command codes
_CMD_NORMALIZE = {
    CMD_W_LOCAL: CMD_W, CMD_EW_LOCAL: CMD_EW, CMD_EWA_LOCAL: CMD_EWA,
    CMD_RB_LOCAL: CMD_RB, CMD_RM_LOCAL: CMD_RM, CMD_WSF_LOCAL: CMD_WSF,
}
_ERASE_CMDS = {CMD_EW, CMD_EWA}
_WRITE_CMDS = {CMD_W, CMD_EW, CMD_EWA}
_READ_CMDS  = {CMD_RB, CMD_RM, CMD_RMA}


def parse(data: bytes) -> ParseResult:
    if not data:
        return ParseResult(command=0, warnings=["empty record"])

    cmd = data[0]
    norm = _CMD_NORMALIZE.get(cmd, cmd)
    r = ParseResult(command=cmd)

    if norm in _READ_CMDS:
        r.is_read = True
        return r  # nothing else to parse — host wants us to send

    if norm == CMD_WSF:
        r.is_wsf = True
        r.wsf_data = data[1:]
        return r  # caller routes to query_reply.handle_wsf()

    if norm == CMD_EAU:
        # Erase All Unprotected: clear unprotected fields, reset MDT, unlock.
        # We synthesize equivalent ops.
        r.ops.append(ops.EraseUnprotected(to_pos=0))  # 0 = full wrap
        r.ops.append(ops.WccFlags(reset_mdt=True, unlock_kbd=True,
                                   alarm=False, restore=False))
        return r

    if norm not in _WRITE_CMDS:
        r.warnings.append(f"unknown command 0x{cmd:02x}")
        return r

    # ── Write / Erase-Write / Erase-Write-Alternate ────────────────
    if norm in _ERASE_CMDS:
        r.ops.append(ops.EraseAll())

    if len(data) < 2:
        r.warnings.append("write command missing WCC")
        return r

    wcc = data[1]
    r.ops.append(ops.WccFlags(
        reset_mdt = bool(wcc & WCC_RESET_MDT),
        unlock_kbd = bool(wcc & WCC_UNLOCK),
        alarm = bool(wcc & WCC_ALARM),
        restore = False,  # we don't distinguish restore from unlock in v1
    ))

    # ── Order/data stream from byte 2 onward ───────────────────────
    _parse_orders(data, 2, r)
    return r


def _parse_orders(data: bytes, i: int, r: ParseResult) -> None:
    """Walk the order/data stream. Tracks write_ptr to compute IC position.

    write_ptr here is the PARSER's notion, used only for SetCursor.pos and
    DefineField.pos. The screen will recompute its own write_ptr from the
    ops anyway — but IC needs to know where the pointer is AT PARSE TIME."""
    write_ptr = 0
    text_run = bytearray()
    n = len(data)

    def flush_text():
        nonlocal text_run, write_ptr
        if text_run:
            r.ops.append(ops.WriteText(data=bytes(text_run)))
            write_ptr += len(text_run)
            text_run = bytearray()

    while i < n:
        b = data[i]

        if b == ORD_SBA:
            flush_text()
            if i + 2 >= n:
                r.warnings.append(f"truncated SBA at offset {i}")
                return
            addr = decode_addr(data[i+1:i+3])
            r.ops.append(ops.SetBufferAddr(pos=addr))
            write_ptr = addr
            i += 3

        elif b == ORD_SF:
            flush_text()
            if i + 1 >= n:
                r.warnings.append(f"truncated SF at offset {i}")
                return
            attrs = decode_attr_byte(data[i+1])
            r.ops.append(ops.DefineField(pos=write_ptr, attrs=attrs))
            write_ptr += 1
            i += 2

        elif b == ORD_IC:
            flush_text()
            r.ops.append(ops.SetCursor(pos=write_ptr))
            i += 1

        elif b in ORDER_BYTES:
            # Other orders — handled in Task 3.5
            flush_text()
            consumed = _parse_complex_order(data, i, r, write_ptr)
            if consumed == 0:
                r.warnings.append(f"unhandled order 0x{b:02x} at offset {i}")
                i += 1
            else:
                # Some orders move the write pointer
                last = r.ops[-1]
                if isinstance(last, ops.SetBufferAddr):
                    write_ptr = last.pos
                elif isinstance(last, (ops.RepeatChar, ops.EraseUnprotected)):
                    write_ptr = last.to_pos
                elif isinstance(last, ops.DefineField):
                    write_ptr = last.pos + 1
                elif isinstance(last, ops.WriteText):
                    # GE emits WriteText; advance by what it wrote.
                    write_ptr += len(last.data)
                i += consumed

        else:
            # Data byte — accumulate into text run
            text_run.append(b)
            i += 1

    flush_text()


def _parse_complex_order(data: bytes, i: int, r: ParseResult, write_ptr: int) -> int:
    """Parse one order at data[i]. Returns bytes consumed (incl. order byte).
    Returns 0 if the order byte isn't handled here (shouldn't happen if
    ORDER_BYTES is complete)."""
    b = data[i]
    n = len(data)

    if b == ORD_SFE:
        # SFE: count + count*(type,value)
        if i + 1 >= n:
            r.warnings.append(f"truncated SFE at {i}")
            return 1
        count = data[i+1]
        need = 2 + count * 2
        if i + need > n:
            r.warnings.append(f"truncated SFE pairs at {i}")
            return n - i
        pairs = [(data[i+2+k*2], data[i+3+k*2]) for k in range(count)]
        attrs = decode_sfe_pairs(pairs)
        r.ops.append(ops.DefineField(pos=write_ptr, attrs=attrs))
        return need

    if b == ORD_MF:
        # Modify Field: same wire format as SFE, but modifies the EXISTING
        # field at write_ptr instead of creating one. We emit DefineField
        # anyway — VirtualScreen replaces fields at the same position.
        # The semantic difference (MF doesn't move write_ptr, doesn't reset
        # cell to attr) is small enough to ignore in v1.
        if i + 1 >= n:
            r.warnings.append(f"truncated MF at {i}")
            return 1
        count = data[i+1]
        need = 2 + count * 2
        if i + need > n:
            r.warnings.append(f"truncated MF pairs at {i}")
            return n - i
        pairs = [(data[i+2+k*2], data[i+3+k*2]) for k in range(count)]
        attrs = decode_sfe_pairs(pairs)
        r.ops.append(ops.DefineField(pos=write_ptr, attrs=attrs))
        r.warnings.append(f"MF treated as SFE at {i}")  # log the simplification
        return need

    if b == ORD_RA:
        # RA: addr(2) + char(1), or addr(2) + GE + char(1)
        if i + 3 >= n:
            r.warnings.append(f"truncated RA at {i}")
            return n - i
        addr = decode_addr(data[i+1:i+3])
        char_byte = data[i+3]
        consumed = 4
        if char_byte == ORD_GE:
            if i + 4 >= n:
                r.warnings.append(f"truncated RA+GE at {i}")
                return n - i
            # GE char → substitute '?' per unmappable policy
            char_byte = 0x6F
            consumed = 5
        r.ops.append(ops.RepeatChar(to_pos=addr, char=char_byte))
        return consumed

    if b == ORD_EUA:
        if i + 2 >= n:
            r.warnings.append(f"truncated EUA at {i}")
            return n - i
        addr = decode_addr(data[i+1:i+3])
        r.ops.append(ops.EraseUnprotected(to_pos=addr))
        return 3

    if b == ORD_PT:
        r.ops.append(ops.ProgramTab())
        return 1

    if b == ORD_SA:
        if i + 2 >= n:
            r.warnings.append(f"truncated SA at {i}")
            return n - i
        r.ops.append(ops.SetExtAttr(attr_type=data[i+1], value=data[i+2]))
        return 3

    if b == ORD_GE:
        # GE in inline text: substitute next byte with '?'.
        # Emit it as a single-byte WriteText so it merges into the run.
        if i + 1 >= n:
            r.warnings.append(f"truncated GE at {i}")
            return 1
        r.ops.append(ops.WriteText(data=bytes([0x6F])))
        return 2

    return 0  # not handled — caller logs
