"""3270 data stream constants. GA23-0059 chapter/table refs in comments."""

# ── Commands (first byte of host→terminal stream) ──────────────────
# GA23-0059 §3.3. The "remote" column — these are what arrives over TN3270.
CMD_W   = 0xF1  # Write
CMD_EW  = 0xF5  # Erase/Write
CMD_EWA = 0x7E  # Erase/Write Alternate
CMD_RB  = 0xF2  # Read Buffer
CMD_RM  = 0xF6  # Read Modified
CMD_RMA = 0x6E  # Read Modified All
CMD_EAU = 0x6F  # Erase All Unprotected
CMD_WSF = 0xF3  # Write Structured Field
# Local (SNA) variants — also accepted, some hosts send these:
CMD_W_LOCAL   = 0x01
CMD_EW_LOCAL  = 0x05
CMD_EWA_LOCAL = 0x0D
CMD_RB_LOCAL  = 0x02
CMD_RM_LOCAL  = 0x06
CMD_WSF_LOCAL = 0x11

# ── Write Control Character bits (second byte after W/EW/EWA) ──────
# GA23-0059 §3.4. Bit 0 (MSB) and bit 1 are reserved/format.
# Useful bits, expressed as masks:
WCC_RESET     = 0x40  # bit 1 — reset (rarely matters for us)
WCC_PRINT_FMT = 0x30  # bits 2-3 — printer format, ignored
WCC_START_PRT = 0x08  # bit 4 — start printer, ignored
WCC_ALARM     = 0x04  # bit 5 — sound alarm
WCC_UNLOCK    = 0x02  # bit 6 — restore (unlock) keyboard
WCC_RESET_MDT = 0x01  # bit 7 — reset all MDT bits

# ── Order codes (appear in W/EW data after WCC) ────────────────────
# GA23-0059 §4.3
ORD_SF  = 0x1D  # Start Field — followed by 1 attr byte
ORD_SFE = 0x29  # Start Field Extended — followed by count + type/value pairs
ORD_SBA = 0x11  # Set Buffer Address — followed by 2 addr bytes
ORD_SA  = 0x28  # Set Attribute — followed by type + value (char-level)
ORD_MF  = 0x2C  # Modify Field — followed by count + type/value pairs
ORD_IC  = 0x13  # Insert Cursor — no operand
ORD_PT  = 0x05  # Program Tab — no operand
ORD_RA  = 0x3C  # Repeat to Address — followed by 2 addr bytes + 1 char
ORD_EUA = 0x12  # Erase Unprotected to Address — followed by 2 addr bytes
ORD_GE  = 0x08  # Graphic Escape — followed by 1 byte (APL char)

# Order bytes form a sparse set — anything else in the stream that's
# >= 0x40 is data (EBCDIC printable). Bytes 0x00-0x3F not in the order
# set are technically invalid but should be passed through as data
# (some hosts send nulls in the stream as padding).
ORDER_BYTES = {ORD_SF, ORD_SFE, ORD_SBA, ORD_SA, ORD_MF,
               ORD_IC, ORD_PT, ORD_RA, ORD_EUA, ORD_GE}

# ── Field Attribute byte bits (operand of SF) ──────────────────────
# GA23-0059 §4.4.1. The byte uses 3270-character-set encoding so bits
# 0-1 are always set; we mask them out before testing.
ATTR_PROTECT  = 0x20  # bit 2: 0=unprotected, 1=protected
ATTR_NUMERIC  = 0x10  # bit 3: 0=alphanumeric, 1=numeric-only
ATTR_DISPLAY  = 0x0C  # bits 4-5: see below
ATTR_RESERVED = 0x02  # bit 6: must be 0
ATTR_MDT      = 0x01  # bit 7: modified data tag

# Display bits (4-5):
#   00 = normal, not detectable
#   01 = normal, detectable (light pen — we treat same as normal)
#   10 = intensified, detectable
#   11 = non-display, non-detectable (HIDDEN)
ATTR_DISP_NORMAL     = 0x00
ATTR_DISP_NORMAL_DET = 0x04
ATTR_DISP_INTENSE    = 0x08
ATTR_DISP_HIDDEN     = 0x0C

# ── Extended attribute types (SFE/SA/MF type bytes) ────────────────
# GA23-0059 §4.4.5
XA_ALL     = 0x00  # reset all to default
XA_3270    = 0xC0  # the basic field attribute (same as SF operand)
XA_VALID   = 0xC1  # field validation — ignored
XA_OUTLINE = 0xC2  # field outlining — ignored
XA_HILITE  = 0x41
XA_FG      = 0x42
XA_CHARSET = 0x43
XA_BG      = 0x45
XA_TRANSP  = 0x46  # transparency — ignored

# Color values for XA_FG / XA_BG (Table 4-3)
COLOR_DEFAULT = 0x00
COLOR_NEUTRAL_LO = 0xF0  # neutral / black-ish
COLOR_BLUE    = 0xF1
COLOR_RED     = 0xF2
COLOR_PINK    = 0xF3
COLOR_GREEN   = 0xF4
COLOR_TURQ    = 0xF5
COLOR_YELLOW  = 0xF6
COLOR_NEUTRAL_HI = 0xF7  # neutral / white-ish

# Hilite values for XA_HILITE
HILITE_DEFAULT   = 0x00
HILITE_NORMAL    = 0xF0
HILITE_BLINK     = 0xF1
HILITE_REVERSE   = 0xF2
HILITE_UNDERLINE = 0xF4

# ── AID codes (first byte of inbound terminal→host stream) ─────────
# GA23-0059 §3.5.6
AID_NO       = 0x60  # No AID generated (used for Read Buffer reply)
AID_SF       = 0x88  # Structured field
AID_RP       = 0x61  # Read Partition
AID_PA1      = 0x6C
AID_PA2      = 0x6E
AID_PA3      = 0x6B
AID_CLEAR    = 0x6D
AID_SYSREQ   = 0xF0
AID_ENTER    = 0x7D
# PF1-PF24
AID_PF1  = 0xF1
AID_PF2  = 0xF2
AID_PF3  = 0xF3
AID_PF4  = 0xF4
AID_PF5  = 0xF5
AID_PF6  = 0xF6
AID_PF7  = 0xF7
AID_PF8  = 0xF8
AID_PF9  = 0xF9
AID_PF10 = 0x7A
AID_PF11 = 0x7B
AID_PF12 = 0x7C
AID_PF13 = 0xC1
AID_PF14 = 0xC2
AID_PF15 = 0xC3
AID_PF16 = 0xC4
AID_PF17 = 0xC5
AID_PF18 = 0xC6
AID_PF19 = 0xC7
AID_PF20 = 0xC8
AID_PF21 = 0xC9
AID_PF22 = 0x4A
AID_PF23 = 0x4B
AID_PF24 = 0x4C

# Short AIDs — these send AID + cursor only, NO field data:
SHORT_AIDS = {AID_CLEAR, AID_PA1, AID_PA2, AID_PA3}


# ── Attribute decoders ─────────────────────────────────────────────

from ..screen.field import FieldAttrs
from ..screen.cell import Color, Hilite


_COLOR_MAP = {
    COLOR_DEFAULT: Color.DEFAULT, COLOR_NEUTRAL_LO: Color.DEFAULT,
    COLOR_BLUE: Color.BLUE, COLOR_RED: Color.RED, COLOR_PINK: Color.PINK,
    COLOR_GREEN: Color.GREEN, COLOR_TURQ: Color.TURQ,
    COLOR_YELLOW: Color.YELLOW, COLOR_NEUTRAL_HI: Color.WHITE,
}
_HILITE_MAP = {
    HILITE_DEFAULT: Hilite.NONE, HILITE_NORMAL: Hilite.NONE,
    HILITE_BLINK: Hilite.BLINK, HILITE_REVERSE: Hilite.REVERSE,
    HILITE_UNDERLINE: Hilite.UNDERLINE,
}


def decode_attr_byte(b: int) -> FieldAttrs:
    """Decode a 3270 SF attribute byte into canonical FieldAttrs.
    GA23-0059 §4.4.1."""
    disp = b & ATTR_DISPLAY
    return FieldAttrs(
        protected = bool(b & ATTR_PROTECT),
        numeric   = bool(b & ATTR_NUMERIC),
        hidden    = (disp == ATTR_DISP_HIDDEN),
        intensified = (disp == ATTR_DISP_INTENSE),
        mdt       = bool(b & ATTR_MDT),
    )


def decode_sfe_pairs(pairs: list[tuple[int, int]]) -> FieldAttrs:
    """Decode SFE/MF type-value pairs. The XA_3270 pair (if present)
    sets the basic attrs; other pairs override/extend."""
    attrs = FieldAttrs()
    for typ, val in pairs:
        if typ == XA_3270:
            base = decode_attr_byte(val)
            attrs.protected = base.protected
            attrs.numeric = base.numeric
            attrs.hidden = base.hidden
            attrs.intensified = base.intensified
            attrs.mdt = base.mdt
        elif typ == XA_FG:
            attrs.fg = _COLOR_MAP.get(val, Color.DEFAULT)
        elif typ == XA_BG:
            attrs.bg = _COLOR_MAP.get(val, Color.DEFAULT)
        elif typ == XA_HILITE:
            attrs.hilite = _HILITE_MAP.get(val, Hilite.NONE)
        # XA_VALID, XA_OUTLINE, XA_CHARSET, XA_TRANSP: ignored (logged by caller)
    return attrs
