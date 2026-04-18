"""ScreenOp ADT — protocol-agnostic screen mutations.

The 3270 parser produces these. VirtualScreen.apply() consumes them.
The 5250 emitter never sees these — it reads VirtualScreen state directly.

Every dataclass is frozen: ops are immutable values, not stateful objects.
"""
from dataclasses import dataclass
from .field import FieldAttrs


@dataclass(frozen=True, slots=True)
class ScreenOp:
    """Marker base. Use isinstance() or match/case for dispatch."""


@dataclass(frozen=True, slots=True)
class EraseAll(ScreenOp):
    """3270 EW/EWA: clear all cells to null, drop all fields, cursor=0."""


@dataclass(frozen=True, slots=True)
class SetBufferAddr(ScreenOp):
    """3270 SBA: move write pointer. Subsequent WriteText starts here."""
    pos: int


@dataclass(frozen=True, slots=True)
class WriteText(ScreenOp):
    """Literal EBCDIC bytes written at current write pointer.
    Pointer advances by len(data). Wraps at buffer end."""
    data: bytes


@dataclass(frozen=True, slots=True)
class DefineField(ScreenOp):
    """3270 SF/SFE: place field-attribute byte at pos, field starts at pos+1.
    Implicitly: cells[pos].is_field_attr = True. Write pointer moves to pos+1."""
    pos: int
    attrs: FieldAttrs


@dataclass(frozen=True, slots=True)
class SetCursor(ScreenOp):
    """3270 IC: cursor = current write pointer."""
    pos: int  # parser computes this from current pointer at IC time


@dataclass(frozen=True, slots=True)
class RepeatChar(ScreenOp):
    """3270 RA: fill [write_ptr, to_pos) with char. Pointer ends at to_pos."""
    to_pos: int
    char: int


@dataclass(frozen=True, slots=True)
class EraseUnprotected(ScreenOp):
    """3270 EUA: set cells to null in unprotected fields, write_ptr → to_pos."""
    to_pos: int


@dataclass(frozen=True, slots=True)
class ProgramTab(ScreenOp):
    """3270 PT: advance write pointer to start of next unprotected field."""


@dataclass(frozen=True, slots=True)
class SetExtAttr(ScreenOp):
    """3270 SA: set a character-level attribute. Sticky until reset."""
    attr_type: int  # 0x41=hilite, 0x42=fg, 0x45=bg, 0x00=reset
    value: int


@dataclass(frozen=True, slots=True)
class WccFlags(ScreenOp):
    """Decoded 3270 Write Control Character. Apply BEFORE other ops."""
    reset_mdt: bool
    unlock_kbd: bool
    alarm: bool
    restore: bool
