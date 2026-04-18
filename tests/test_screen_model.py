import pytest
from tn5250to3270.screen.model import VirtualScreen
from tn5250to3270.screen.cell import Cell, Color
from tn5250to3270.screen.field import Field, FieldAttrs
from tn5250to3270.screen import ops

def test_construct_24x80():
    s = VirtualScreen(rows=24, cols=80)
    assert s.rows == 24
    assert s.cols == 80
    assert s.size == 1920
    assert len(s.cells) == 1920
    assert s.cursor == 0
    assert s.write_ptr == 0
    assert s.fields == []
    assert all(c.char == 0x00 for c in s.cells)

def test_construct_27x132():
    s = VirtualScreen(rows=27, cols=132)
    assert s.size == 3564

def test_erase_all():
    s = VirtualScreen(24, 80)
    # Pollute state
    s.cells[5] = Cell(char=0xC1)
    s.fields.append(Field(start=0, length=10))
    s.cursor = 42
    s.write_ptr = 99
    # Erase
    s.apply(ops.EraseAll())
    assert all(c.char == 0x00 for c in s.cells)
    assert all(c.is_field_attr is False for c in s.cells)
    assert s.fields == []
    assert s.cursor == 0
    assert s.write_ptr == 0

def test_sba_then_write():
    s = VirtualScreen(24, 80)
    s.apply(ops.SetBufferAddr(pos=10))
    assert s.write_ptr == 10
    s.apply(ops.WriteText(data=bytes([0xC1, 0xC2, 0xC3])))  # EBCDIC ABC
    assert s.cells[10].char == 0xC1
    assert s.cells[11].char == 0xC2
    assert s.cells[12].char == 0xC3
    assert s.write_ptr == 13

def test_write_wraps_at_buffer_end():
    s = VirtualScreen(24, 80)  # size 1920
    s.apply(ops.SetBufferAddr(pos=1918))
    s.apply(ops.WriteText(data=bytes([0xC1, 0xC2, 0xC3, 0xC4])))
    assert s.cells[1918].char == 0xC1
    assert s.cells[1919].char == 0xC2
    assert s.cells[0].char == 0xC3   # wrapped
    assert s.cells[1].char == 0xC4
    assert s.write_ptr == 2

def test_write_picks_up_sticky_sa_attrs():
    s = VirtualScreen(24, 80)
    s._sa_fg = Color.RED  # simulate prior SA
    s.apply(ops.WriteText(data=bytes([0xC1])))
    assert s.cells[0].fg == Color.RED

def test_define_field_marks_attr_cell():
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=10, attrs=FieldAttrs(protected=True)))
    assert s.cells[10].is_field_attr is True
    assert s.cells[10].char == 0x00  # attr cell shows as null/blank
    assert s.write_ptr == 11         # pointer moves past attr byte

def test_single_field_wraps_whole_buffer():
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=10, attrs=FieldAttrs()))
    assert len(s.fields) == 1
    assert s.fields[0].start == 10
    assert s.fields[0].length == 1920  # wraps all the way around to itself

def test_two_fields_split_buffer():
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=10, attrs=FieldAttrs()))
    s.apply(ops.DefineField(pos=100, attrs=FieldAttrs(protected=True)))
    # Sorted by start; lengths recomputed
    assert s.fields[0].start == 10
    assert s.fields[0].length == 90      # 100 - 10
    assert s.fields[1].start == 100
    assert s.fields[1].length == 1830    # wraps: 1920 - 100 + 10

def test_define_field_at_existing_position_replaces():
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=10, attrs=FieldAttrs(protected=False)))
    s.apply(ops.DefineField(pos=10, attrs=FieldAttrs(protected=True)))
    assert len(s.fields) == 1
    assert s.fields[0].attrs.protected is True

def test_field_at_lookup():
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=10, attrs=FieldAttrs(protected=True)))
    s.apply(ops.DefineField(pos=100, attrs=FieldAttrs(protected=False)))
    # pos 50 is inside field starting at 10
    assert s.field_at(50).start == 10
    # pos 5 wraps — it's inside the field starting at 100
    assert s.field_at(5).start == 100
    # pos 10 is the attr byte itself — belongs to field starting at 10
    assert s.field_at(10).start == 10

def test_set_cursor():
    s = VirtualScreen(24, 80)
    s.apply(ops.SetCursor(pos=42))
    assert s.cursor == 42

def test_repeat_char():
    s = VirtualScreen(24, 80)
    s.apply(ops.SetBufferAddr(pos=5))
    s.apply(ops.RepeatChar(to_pos=10, char=0x40))  # spaces
    assert all(s.cells[i].char == 0x40 for i in range(5, 10))
    assert s.cells[10].char == 0x00  # to_pos is exclusive
    assert s.write_ptr == 10

def test_repeat_char_wraps():
    s = VirtualScreen(24, 80)
    s.apply(ops.SetBufferAddr(pos=1918))
    s.apply(ops.RepeatChar(to_pos=2, char=0x5C))  # asterisk
    assert s.cells[1918].char == 0x5C
    assert s.cells[1919].char == 0x5C
    assert s.cells[0].char == 0x5C
    assert s.cells[1].char == 0x5C
    assert s.cells[2].char == 0x00
    assert s.write_ptr == 2

def test_erase_unprotected():
    s = VirtualScreen(24, 80)
    # Field 1: protected at 0, length 10 (data 1-9)
    # Field 2: unprotected at 10, length 1910 (data 11-1919, wraps)
    s.apply(ops.DefineField(pos=0, attrs=FieldAttrs(protected=True)))
    s.apply(ops.DefineField(pos=10, attrs=FieldAttrs(protected=False)))
    # Fill everything with X
    for i in range(1920):
        if not s.cells[i].is_field_attr:
            s.cells[i] = Cell(char=0xE7)  # X
    # EUA from current pos (which is 11 after last DefineField) to pos 50
    s.apply(ops.SetBufferAddr(pos=0))  # reset pointer
    s.apply(ops.EraseUnprotected(to_pos=50))
    # Protected data (1-9) untouched
    assert s.cells[5].char == 0xE7
    # Unprotected data (11-49) nulled
    assert s.cells[15].char == 0x00
    assert s.cells[49].char == 0x00
    # Past to_pos: untouched
    assert s.cells[50].char == 0xE7
    assert s.write_ptr == 50

def test_program_tab_to_next_unprotected():
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=0, attrs=FieldAttrs(protected=True)))
    s.apply(ops.DefineField(pos=20, attrs=FieldAttrs(protected=False)))
    s.apply(ops.DefineField(pos=40, attrs=FieldAttrs(protected=True)))
    s.apply(ops.SetBufferAddr(pos=5))  # inside protected field
    s.apply(ops.ProgramTab())
    assert s.write_ptr == 21  # data start of next unprotected field

def test_program_tab_no_unprotected_goes_to_zero():
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=0, attrs=FieldAttrs(protected=True)))
    s.apply(ops.SetBufferAddr(pos=5))
    s.apply(ops.ProgramTab())
    assert s.write_ptr == 0

def test_set_ext_attr_sticky():
    s = VirtualScreen(24, 80)
    # 0x42 = foreground color, 0xF2 = red (3270 color codes)
    s.apply(ops.SetExtAttr(attr_type=0x42, value=0xF2))
    s.apply(ops.WriteText(data=bytes([0xC1])))
    assert s.cells[0].fg == Color.RED
    # Reset all
    s.apply(ops.SetExtAttr(attr_type=0x00, value=0x00))
    s.apply(ops.WriteText(data=bytes([0xC2])))
    assert s.cells[1].fg == Color.DEFAULT

def test_wcc_flags():
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=0, attrs=FieldAttrs(mdt=True)))
    s.keyboard_locked = True
    s.apply(ops.WccFlags(reset_mdt=True, unlock_kbd=True, alarm=True, restore=False))
    assert s.fields[0].attrs.mdt is False
    assert s.keyboard_locked is False
    assert s.alarm is True

def test_get_modified_fields_returns_only_mdt():
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=0, attrs=FieldAttrs(mdt=False)))
    s.apply(ops.DefineField(pos=20, attrs=FieldAttrs(mdt=True)))
    # Put data in the MDT field (pos 21..39)
    s.apply(ops.SetBufferAddr(pos=21))
    s.apply(ops.WriteText(data=b"\xc8\xc5\xd3\xd3\xd6"))  # HELLO
    mods = s.get_modified_fields()
    assert len(mods) == 1
    start, data = mods[0]
    assert start == 21
    assert data == b"\xc8\xc5\xd3\xd3\xd6"

def test_get_modified_fields_strips_trailing_nulls():
    """3270 inbound rule: nulls are not transmitted."""
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=0, attrs=FieldAttrs(mdt=True)))
    s.apply(ops.SetBufferAddr(pos=1))
    s.apply(ops.WriteText(data=b"\xc1\xc2\x00\x00"))  # AB then nulls
    mods = s.get_modified_fields()
    assert mods[0][1] == b"\xc1\xc2"  # nulls stripped

def test_get_modified_fields_strips_embedded_nulls():
    """Per GA23-0059: ALL nulls are suppressed in Read Modified, not just trailing."""
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=0, attrs=FieldAttrs(mdt=True)))
    s.apply(ops.SetBufferAddr(pos=1))
    s.apply(ops.WriteText(data=b"\xc1\x00\xc2"))  # A null B
    mods = s.get_modified_fields()
    assert mods[0][1] == b"\xc1\xc2"

def test_set_field_data_sets_mdt():
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=10, attrs=FieldAttrs(protected=False, mdt=False)))
    s.set_field_data(11, b"\xc1\xc2\xc3")
    assert s.cells[11].char == 0xC1
    assert s.cells[12].char == 0xC2
    assert s.cells[13].char == 0xC3
    assert s.fields[0].attrs.mdt is True

def test_set_field_data_clamps_to_field_length():
    s = VirtualScreen(24, 80)
    s.apply(ops.DefineField(pos=10, attrs=FieldAttrs()))
    s.apply(ops.DefineField(pos=15, attrs=FieldAttrs()))  # field at 10 has data_length=4
    s.set_field_data(11, b"\xc1\xc2\xc3\xc4\xc5\xc6")  # 6 bytes, only 4 fit
    assert s.cells[14].char == 0xC4
    assert s.cells[15].is_field_attr  # not overwritten
