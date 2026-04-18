from dataclasses import dataclass
from enum import IntEnum


class Color(IntEnum):
    """Canonical color. Both 3270 SFE and 5250 attr-byte map to/from this."""
    DEFAULT = 0
    BLUE    = 1
    RED     = 2
    PINK    = 3
    GREEN   = 4
    TURQ    = 5  # turquoise / cyan
    YELLOW  = 6
    WHITE   = 7


class Hilite(IntEnum):
    NONE      = 0
    BLINK     = 1
    REVERSE   = 2
    UNDERLINE = 3


@dataclass(slots=True)
class Cell:
    """One screen position. EBCDIC char + display attrs.

    is_field_attr=True means this position holds a 3270-style field
    attribute byte — it renders as blank, and a field starts at the
    NEXT position.
    """
    char: int = 0x00          # EBCDIC. 0x00=null, 0x40=space
    fg: Color = Color.DEFAULT
    bg: Color = Color.DEFAULT
    hilite: Hilite = Hilite.NONE
    is_field_attr: bool = False
