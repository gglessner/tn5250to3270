"""5250 data stream constants. RFC 1205 + SC30-3533."""

# ── GDS (General Data Stream) header ───────────────────────────────
GDS_TYPE = 0x12A0   # bytes 2-3 of every record
GDS_VARHDR_LEN = 0x04  # byte 6: length of (flags + opcode)

# ── Opcodes (byte 9 of GDS header) ─────────────────────────────────
OP_NOOP            = 0x00  # No-op (we send this on connect to satisfy ACS)
OP_INVITE          = 0x01
OP_OUTPUT_ONLY     = 0x02  # Server→client, no input expected
OP_PUT_GET         = 0x03  # Server→client, input expected after
OP_SAVE_SCREEN     = 0x04
OP_RESTORE_SCREEN  = 0x05
OP_READ_IMMEDIATE  = 0x06
OP_READ_SCREEN     = 0x08
OP_CANCEL_INVITE   = 0x0A
OP_MSG_LIGHT_ON    = 0x0B
OP_MSG_LIGHT_OFF   = 0x0C

# ── Commands (after GDS header, ESC + cmd byte) ────────────────────
ESC = 0x04
CMD_CLEAR_UNIT       = 0x40
CMD_CLEAR_UNIT_ALT   = 0x20
CMD_CLEAR_FMT_TABLE  = 0x50
CMD_WTD              = 0x11  # Write to Display
CMD_WTD_SF           = 0x13  # WTD with structured fields (we don't emit)
CMD_RESTORE_SCREEN   = 0x12
CMD_READ_INPUT       = 0x42
CMD_READ_MDT         = 0x52
CMD_READ_MDT_ALT     = 0x82
CMD_READ_SCREEN      = 0x62
CMD_SAVE_SCREEN      = 0x02
CMD_ROLL             = 0x23

# ── WTD CC1 byte (control char 1) — IBM bit numbering, MSB=bit0 ────
# RFC 1205 §5.1. Use mask values, not bit numbers.
CC1_LOCK_KBD         = 0x00  # 0 in bits 5-7 = lock (this is the absence of unlock)
CC1_RESET_PENDING_AID = 0x40
CC1_RESET_MDT_NONBYP = 0x20  # Reset MDT in non-bypass fields
CC1_RESET_MDT_ALL    = 0x10  # Reset MDT in ALL fields
CC1_NULL_NONBYP_MDT  = 0x08
CC1_NULL_NONBYP_ALL  = 0x04
# bits 0-1 reserved, bit 2 reserved
CC1_CLEAR_MASK       = 0x00  # what we send when we don't need any of these

# ── WTD CC2 byte ───────────────────────────────────────────────────
CC2_UNLOCK_KBD       = 0x08  # THIS unlocks. Not CC1.
CC2_ALARM            = 0x04
CC2_MSG_LIGHT_OFF    = 0x02
CC2_MSG_LIGHT_ON     = 0x01
CC2_NO_CURSOR_MOVE   = 0x20
CC2_RESET_BLINK      = 0x10
CC2_SET_BLINK        = 0x40

# ── WTD orders (after CC1 CC2) ─────────────────────────────────────
ORD_SOH = 0x01  # Start of Header
ORD_RA  = 0x02  # Repeat to Address: row(1) col(1) char(1)
ORD_EA  = 0x03  # Erase to Address: row(1) col(1) attr(1)
ORD_TD  = 0x10  # Transparent Data: len(2) + data
ORD_SBA = 0x11  # Set Buffer Address: row(1) col(1)
ORD_WEA = 0x12  # Write Extended Attribute: type(1) val(1)
ORD_IC  = 0x13  # Insert Cursor: row(1) col(1)
ORD_MC  = 0x14  # Move Cursor: row(1) col(1) (same as IC for our purposes)
ORD_WDSF = 0x15 # Write to Display Structured Field — we don't emit
ORD_SF  = 0x1D  # Start Field

# ── SOH sub-orders ─────────────────────────────────────────────────
SOH_RESEQ = 0x00  # Resequence (we don't use)
SOH_ERR   = 0x80  # Error line — we don't use (no 3270 equivalent)

# ── Screen attribute bytes (0x20-0x3F) ─────────────────────────────
# These OCCUPY a screen position, just like 3270 field attrs.
# RFC 1205 §5.4 / SC30-3533. 5-bit value: bit2=col-sep, bit3=blink,
# bit4=underline, bit5=intensity, bit6=reverse, bit7 always 0, bits 0-1 = 01.
# But for COLOR terminals, the meaning shifts. We use the color mapping:
SA_GREEN         = 0x20  # normal
SA_GREEN_REV     = 0x21
SA_WHITE         = 0x22
SA_WHITE_REV     = 0x23
SA_GREEN_UL      = 0x24
SA_GREEN_UL_REV  = 0x25
SA_WHITE_UL      = 0x26
SA_NONDISPLAY    = 0x27  # ← this is HIDDEN
SA_RED           = 0x28
SA_RED_REV       = 0x29
SA_RED_BLINK     = 0x2A
SA_RED_REV_BLINK = 0x2B
SA_RED_UL        = 0x2C
SA_RED_UL_REV    = 0x2D
SA_RED_UL_BLINK  = 0x2E
SA_NONDISPLAY_2  = 0x2F
SA_TURQ_CS       = 0x30  # column separator
SA_TURQ_CS_REV   = 0x31
SA_YELLOW_CS     = 0x32
SA_YELLOW_CS_REV = 0x33
SA_TURQ_UL       = 0x34
SA_TURQ_UL_REV   = 0x35
SA_YELLOW_UL     = 0x36
SA_NONDISPLAY_3  = 0x37
SA_PINK          = 0x38
SA_PINK_REV      = 0x39
SA_BLUE          = 0x3A
SA_BLUE_REV      = 0x3B
SA_PINK_UL       = 0x3C
SA_PINK_UL_REV   = 0x3D
SA_BLUE_UL       = 0x3E
SA_NONDISPLAY_4  = 0x3F

# ── 5250 AID codes (client→server, in inbound records) ─────────────
# RFC 1205 §6
AID5_ENTER       = 0xF1
AID5_HELP        = 0xF3
AID5_ROLL_DOWN   = 0xF4  # = page UP (shows previous)
AID5_ROLL_UP     = 0xF5  # = page DOWN (shows next)
AID5_PRINT       = 0xF6
AID5_REC_BACKSP  = 0xF8
AID5_AUTO_ENTER  = 0x3F
AID5_CLEAR       = 0xBD
AID5_PA1         = 0x6C
AID5_PA2         = 0x6E
AID5_PA3         = 0x6B
# F1-F24
AID5_F1  = 0x31
AID5_F2  = 0x32
AID5_F3  = 0x33
AID5_F4  = 0x34
AID5_F5  = 0x35
AID5_F6  = 0x36
AID5_F7  = 0x37
AID5_F8  = 0x38
AID5_F9  = 0x39
AID5_F10 = 0x3A
AID5_F11 = 0x3B
AID5_F12 = 0x3C
AID5_F13 = 0xB1
AID5_F14 = 0xB2
AID5_F15 = 0xB3
AID5_F16 = 0xB4
AID5_F17 = 0xB5
AID5_F18 = 0xB6
AID5_F19 = 0xB7
AID5_F20 = 0xB8
AID5_F21 = 0xB9
AID5_F22 = 0xBA
AID5_F23 = 0xBB
AID5_F24 = 0xBC


# ── AID translation: 5250 → 3270 ───────────────────────────────────
# Spec §8. Anything NOT in this map gets dropped+logged by the session layer.
# This is the ONE place tn5250/ imports from tn3270/ — constants only, no logic.
from ..tn3270 import constants as _c3

AID_MAP_5250_TO_3270: dict[int, int] = {
    AID5_ENTER:     _c3.AID_ENTER,
    AID5_CLEAR:     _c3.AID_CLEAR,
    AID5_PA1:       _c3.AID_PA1,
    AID5_PA2:       _c3.AID_PA2,
    AID5_PA3:       _c3.AID_PA3,
    # Roll keys → PF7/8 per ISPF convention
    AID5_ROLL_DOWN: _c3.AID_PF7,   # roll down = previous page = PF7 UP
    AID5_ROLL_UP:   _c3.AID_PF8,   # roll up = next page = PF8 DOWN
    AID5_HELP:      _c3.AID_PF1,
    # F1-F24 → PF1-PF24
    AID5_F1:  _c3.AID_PF1,  AID5_F2:  _c3.AID_PF2,  AID5_F3:  _c3.AID_PF3,
    AID5_F4:  _c3.AID_PF4,  AID5_F5:  _c3.AID_PF5,  AID5_F6:  _c3.AID_PF6,
    AID5_F7:  _c3.AID_PF7,  AID5_F8:  _c3.AID_PF8,  AID5_F9:  _c3.AID_PF9,
    AID5_F10: _c3.AID_PF10, AID5_F11: _c3.AID_PF11, AID5_F12: _c3.AID_PF12,
    AID5_F13: _c3.AID_PF13, AID5_F14: _c3.AID_PF14, AID5_F15: _c3.AID_PF15,
    AID5_F16: _c3.AID_PF16, AID5_F17: _c3.AID_PF17, AID5_F18: _c3.AID_PF18,
    AID5_F19: _c3.AID_PF19, AID5_F20: _c3.AID_PF20, AID5_F21: _c3.AID_PF21,
    AID5_F22: _c3.AID_PF22, AID5_F23: _c3.AID_PF23, AID5_F24: _c3.AID_PF24,
    AID5_AUTO_ENTER: _c3.AID_ENTER,  # treat auto-enter as regular Enter
    # Deliberately absent: AID5_PRINT, AID5_REC_BACKSP — dropped+logged
}
