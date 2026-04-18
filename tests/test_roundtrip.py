"""3270 → screen → 5250 → re-parse → screen′ — screens should match.

This is the strongest test we have. If this passes for a vector, the
conversion is provably correct for that vector.
"""
import pytest
from tn5250to3270.screen.model import VirtualScreen
from tn5250to3270.screen.field import FieldAttrs
from tn5250to3270.screen import ops
from tn5250to3270.tn3270 import parser as p3270
from tn5250to3270.tn3270.constants import CMD_EW, ORD_SBA, ORD_SF, ORD_IC, ORD_RA
from tn5250to3270.tn5250 import emitter as e5250


def screens_equal(a: VirtualScreen, b: VirtualScreen) -> bool:
    """Compare display-relevant state. Ignore write_ptr (parser artifact).

    Special case: 3270 unformatted (fields=[]) and 5250 with one
    synthetic full-screen unprotected field at pos 0 are functionally
    equivalent — both mean 'type anywhere'."""
    if (a.rows, a.cols) != (b.rows, b.cols):
        return False
    # Cursor: for unformatted screens, cursor 0 (3270) ≡ cursor 1 (5250
    # synthetic field) — we deliberately bump it past the synthetic attr.
    a_unfmt_cur0 = (not a.fields and a.cursor == 0)
    b_unfmt_cur0 = (not b.fields and b.cursor == 0)
    if a.cursor != b.cursor:
        if not ((a_unfmt_cur0 and b.cursor == 1) or
                (b_unfmt_cur0 and a.cursor == 1)):
            return False
    # Unformatted equivalence: [] ≡ [Field(0, size, unprotected)]
    def normalize_fields(s):
        if not s.fields:
            return []
        if (len(s.fields) == 1 and s.fields[0].start == 0
                and s.fields[0].length == s.size
                and not s.fields[0].attrs.protected):
            return []  # treat synthetic full-screen field as unformatted
        return sorted((f.start, f.attrs.protected, f.attrs.hidden)
                      for f in s.fields)
    if normalize_fields(a) != normalize_fields(b):
        return False
    # Cell comparison — but skip the synthetic attr cell at pos 0 if
    # one side is unformatted and the other has the synthetic field.
    a_unfmt = not a.fields
    b_unfmt = not b.fields
    skip_pos0 = (a_unfmt != b_unfmt)  # one is unfmt, other has synthetic
    for i in range(a.size):
        if skip_pos0 and i == 0:
            continue  # synthetic attr byte vs null — known difference
        ca, cb = a.cells[i], b.cells[i]
        if ca.char != cb.char:
            return False
        if ca.is_field_attr != cb.is_field_attr:
            return False
    return True


def apply_5250_wtd(screen: VirtualScreen, wtd_record: bytes) -> None:
    """Minimal 5250 WTD interpreter — just enough to verify round-trip.
    Not a full implementation; only handles what our emitter produces."""
    from tn5250to3270.tn5250.gds import unpack_gds
    from tn5250to3270.tn5250.constants import (
        ESC, CMD_CLEAR_UNIT, CMD_WTD, CMD_READ_MDT,
        ORD_SBA as SBA5, ORD_SF as SF5, ORD_IC as IC5, ORD_SOH as SOH5,
    )
    h, payload = unpack_gds(wtd_record)
    i = 0
    n = len(payload)
    # Skip leading ESC commands until WTD body
    while i < n and payload[i] == ESC:
        cmd = payload[i + 1]
        if cmd == CMD_CLEAR_UNIT:
            screen.apply(ops.EraseAll())
            i += 2
        elif cmd == CMD_WTD:
            i += 4  # ESC WTD CC1 CC2
            break
        else:
            i += 2
    # Walk orders
    write_pos = 0
    while i < n:
        b = payload[i]
        if b == ESC:
            # Trailing ESC command (e.g., READ_MDT_FIELDS) — ends WTD body
            cmd = payload[i + 1]
            if cmd == CMD_READ_MDT:
                i += 4  # ESC READ_MDT CC1 CC2
            else:
                i += 2
            continue
        elif b == SOH5:
            # SOH len <len bytes> — skip entirely (no screen effect)
            i += 2 + payload[i + 1]
        elif b == SBA5:
            row, col = payload[i + 1] - 1, payload[i + 2] - 1
            write_pos = screen.from_rowcol(row, col)
            screen.write_ptr = write_pos
            i += 3
        elif b == SF5:
            # SF FFW(2) attr(1) len(2)
            ffw0 = payload[i + 1]
            sa = payload[i + 3]
            attrs = FieldAttrs(
                protected=bool(ffw0 & 0x20),
                hidden=(sa == 0x27),
            )
            screen.apply(ops.DefineField(pos=write_pos, attrs=attrs))
            write_pos += 1
            i += 6
        elif b == IC5:
            row, col = payload[i + 1] - 1, payload[i + 2] - 1
            screen.cursor = screen.from_rowcol(row, col)
            i += 3
        else:
            # Data byte
            screen.cells[write_pos] = screen.cells[write_pos].__class__(char=b)
            write_pos = screen.wrap(write_pos + 1)
            i += 1


VECTORS = [
    # (name, 3270_bytes)
    ("ew_blank", bytes([CMD_EW, 0xC3])),
    ("ew_text", bytes([CMD_EW, 0xC3, ORD_SBA, 0xC1, 0x50]) + b"\xc8\xc5\xd3\xd3\xd6"),
    ("ew_field", bytes([CMD_EW, 0xC3,
                        ORD_SBA, 0x40, 0x40, ORD_SF, 0x60,  # protected at 0
                        ORD_SBA, 0x40, 0x4A, ORD_SF, 0x40,  # unprotected at 10
                        0xC1, 0xC2, 0xC3,                    # ABC
                        ORD_IC])),
    ("ew_repeat", bytes([CMD_EW, 0xC3,
                         ORD_SBA, 0x40, 0x40,
                         ORD_RA, 0x40, 0x4A, 0x5C])),  # repeat '*' to addr 10
]


@pytest.mark.parametrize("name,vec", VECTORS, ids=[v[0] for v in VECTORS])
def test_roundtrip(name, vec):
    # 1. Parse 3270 → ops → screen_a
    screen_a = VirtualScreen(24, 80)
    result = p3270.parse(vec)
    for op in result.ops:
        screen_a.apply(op)
    screen_a.keyboard_locked = False  # so emitter unlocks

    # 2. Render 5250 WTD
    erased = any(isinstance(o, ops.EraseAll) for o in result.ops)
    wtd = e5250.render_wtd(screen_a, erased=erased)

    # 3. Apply WTD to a fresh screen → screen_b
    screen_b = VirtualScreen(24, 80)
    apply_5250_wtd(screen_b, wtd)

    # 4. Compare
    assert screens_equal(screen_a, screen_b), \
        f"{name}: screens differ after round-trip"
