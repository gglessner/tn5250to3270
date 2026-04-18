from dataclasses import dataclass, field
from .cell import Color, Hilite


@dataclass(slots=True)
class FieldAttrs:
    """Canonical field attributes. Decoded from 3270 attr byte / SFE,
    encoded into 5250 FFW + screen attr."""
    protected: bool = False
    numeric: bool = False
    hidden: bool = False
    intensified: bool = False
    mdt: bool = False
    fg: Color = Color.DEFAULT
    bg: Color = Color.DEFAULT
    hilite: Hilite = Hilite.NONE


@dataclass(slots=True)
class Field:
    """A field on the screen. start = position of the attribute byte.
    Field data occupies [start+1, start+length). length includes the attr byte.
    Wraps around the buffer end (3270 buffers are circular)."""
    start: int
    length: int
    attrs: FieldAttrs = field(default_factory=FieldAttrs)

    @property
    def data_start(self) -> int:
        return self.start + 1

    @property
    def data_length(self) -> int:
        return self.length - 1
