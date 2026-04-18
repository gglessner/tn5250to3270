"""Field Format Word + screen attribute encoding.

FFW (2 bytes) — SC30-3533 §15.6.13. IBM bit numbering, MSB=bit 0:
  Byte 0:
    bits 0-1: 01 (FFW identifier)
    bit  2:   bypass (= 3270 protected)
    bit  3:   dup enable (we don't set)
    bit  4:   MDT
    bits 5-7: shift/edit spec (000=alpha, 011=numeric only, etc.)
  Byte 1:
    bit  0:   auto-enter (we don't set — no 3270 equivalent)
    bit  1:   field-exit required
    bit  2:   monocase
    bit  3:   reserved
    bit  4:   mandatory-enter
    bits 5-7: right-adjust/mandatory-fill spec (000=none)

Screen attribute (1 byte, 0x20-0x3F) — encodes color/intensity/hilite.
The 5250 color attributes are a fixed lookup, not bit-composed.
"""
from ..screen.field import FieldAttrs
from ..screen.cell import Color, Hilite
from .constants import (
    SA_GREEN, SA_WHITE, SA_NONDISPLAY,
    SA_RED, SA_BLUE, SA_PINK, SA_TURQ_CS, SA_YELLOW_CS,
    SA_GREEN_REV, SA_GREEN_UL,
)


# Bit masks (translating IBM bit-numbering to Python masks for byte 0)
_FFW_ID       = 0x40  # bits 0-1 = 01 → mask for bit 1 set
_FFW_BYPASS   = 0x20  # bit 2
_FFW_DUP      = 0x10  # bit 3
_FFW_MDT      = 0x08  # bit 4
_FFW_SHIFT_MASK = 0x07  # bits 5-7

# Shift values
_SHIFT_ALPHA   = 0x00
_SHIFT_ALPHA_O = 0x01  # alpha only (no numbers)
_SHIFT_NUM_S   = 0x02  # numeric shift (allows alpha too)
_SHIFT_NUM_O   = 0x03  # numeric only — closest to 3270 numeric
_SHIFT_KATA    = 0x04
_SHIFT_DIGIT   = 0x05  # digits only
_SHIFT_IO      = 0x06
_SHIFT_SIGNED  = 0x07


def encode_ffw(attrs: FieldAttrs) -> bytes:
    """Build the 2-byte Field Format Word from canonical attributes.

    We only use the subset that maps from 3270:
      protected → bypass
      numeric   → numeric-only shift
      mdt       → MDT
    Everything else (auto-enter, field-exit, monocase, mandatory) defaults off
    because 3270 has no source for these.
    """
    b0 = _FFW_ID
    if attrs.protected:
        b0 |= _FFW_BYPASS
    if attrs.mdt:
        b0 |= _FFW_MDT
    if attrs.numeric:
        b0 |= _SHIFT_NUM_O

    b1 = 0x00  # no auto-enter, no field-exit, no monocase, no mandatory

    return bytes([b0, b1])


# Canonical Color → 5250 base attribute byte (no hilite)
_COLOR_TO_SA = {
    Color.DEFAULT: SA_GREEN,
    Color.GREEN:   SA_GREEN,
    Color.WHITE:   SA_WHITE,
    Color.RED:     SA_RED,
    Color.BLUE:    SA_BLUE,
    Color.PINK:    SA_PINK,
    Color.TURQ:    SA_TURQ_CS,    # turquoise gets column-sep, closest match
    Color.YELLOW:  SA_YELLOW_CS,
}


def encode_screen_attr(attrs: FieldAttrs) -> int:
    """Build the 1-byte screen attribute (0x20-0x3F).

    Priority order:
      1. hidden → SA_NONDISPLAY (overrides everything)
      2. explicit color from fg
      3. intensified → SA_WHITE
      4. default → SA_GREEN
    Then OR in hilite modifiers where the 5250 palette supports them.
    """
    if attrs.hidden:
        return SA_NONDISPLAY

    if attrs.fg != Color.DEFAULT:
        base = _COLOR_TO_SA.get(attrs.fg, SA_GREEN)
    elif attrs.intensified:
        base = SA_WHITE
    else:
        base = SA_GREEN

    # Hilite modifiers: 5250's palette has reverse (+1) and underline (+4)
    # variants for SOME colors. Not all combos exist. Best-effort:
    if attrs.hilite == Hilite.REVERSE:
        # +1 works for most: 0x20→0x21, 0x22→0x23, 0x28→0x29, etc.
        if base in (SA_GREEN, SA_WHITE, SA_RED, SA_BLUE, SA_PINK):
            base += 1
    elif attrs.hilite == Hilite.UNDERLINE:
        # +4 for the colors that have UL variants
        if base in (SA_GREEN, SA_RED):
            base += 4
        elif base == SA_WHITE:
            base = 0x26  # SA_WHITE_UL
    # BLINK: only red has it (0x2A). Skip for other colors.
    elif attrs.hilite == Hilite.BLINK and base == SA_RED:
        base = 0x2A

    return base
