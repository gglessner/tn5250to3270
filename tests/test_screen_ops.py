import pytest
from tn5250to3270.screen.cell import Cell, Color, Hilite


def test_cell_defaults():
    c = Cell()
    assert c.char == 0x00
    assert c.fg == Color.DEFAULT
    assert c.bg == Color.DEFAULT
    assert c.hilite == Hilite.NONE
    assert c.is_field_attr is False


def test_cell_is_value_type():
    assert Cell(char=0x40) == Cell(char=0x40)
    assert Cell(char=0x40) != Cell(char=0x41)


from tn5250to3270.screen.field import Field, FieldAttrs


def test_fieldattrs_defaults():
    a = FieldAttrs()
    assert a.protected is False
    assert a.numeric is False
    assert a.hidden is False
    assert a.intensified is False
    assert a.mdt is False
    assert a.fg == Color.DEFAULT


def test_field_holds_attrs_and_geometry():
    f = Field(start=10, length=20, attrs=FieldAttrs(protected=True))
    assert f.start == 10
    assert f.length == 20
    assert f.attrs.protected is True
    assert f.data_start == 11
    assert f.data_length == 19


from tn5250to3270.screen import ops


def test_ops_are_frozen():
    op = ops.SetCursor(pos=42)
    with pytest.raises(Exception):  # FrozenInstanceError
        op.pos = 0


def test_all_ops_constructible():
    ops.EraseAll()
    ops.SetBufferAddr(pos=0)
    ops.WriteText(data=b"\xc1\xc2\xc3")
    ops.DefineField(pos=5, attrs=FieldAttrs(protected=True))
    ops.SetCursor(pos=10)
    ops.RepeatChar(to_pos=80, char=0x40)
    ops.EraseUnprotected(to_pos=160)
    ops.ProgramTab()
    ops.SetExtAttr(attr_type=0x41, value=0xF2)
    ops.WccFlags(reset_mdt=True, unlock_kbd=True, alarm=False, restore=False)
