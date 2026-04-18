import pytest
from tn5250to3270.tn5250.ffw import encode_ffw, encode_screen_attr
from tn5250to3270.tn5250.constants import SA_GREEN, SA_WHITE, SA_NONDISPLAY, SA_RED, SA_BLUE
from tn5250to3270.screen.field import FieldAttrs
from tn5250to3270.screen.cell import Color, Hilite


def test_ffw_unprotected_alpha():
    """Default unprotected field: bypass=0, alpha shift."""
    a = FieldAttrs(protected=False, numeric=False, mdt=False)
    ffw = encode_ffw(a)
    assert len(ffw) == 2
    # Top 2 bits of byte 0 must be 01 (FFW marker)
    assert (ffw[0] & 0xC0) == 0x40
    # Bypass bit (bit 2 of byte 0) clear
    assert (ffw[0] & 0x20) == 0x00
    # Shift bits = 000 (alpha)
    assert (ffw[0] & 0x07) == 0x00
    # MDT bit clear
    assert (ffw[0] & 0x08) == 0x00


def test_ffw_protected_means_bypass():
    a = FieldAttrs(protected=True)
    ffw = encode_ffw(a)
    assert (ffw[0] & 0x20) == 0x20  # bypass set


def test_ffw_numeric():
    a = FieldAttrs(numeric=True)
    ffw = encode_ffw(a)
    # Numeric-only shift = 011 in bits 5-7 of byte 0
    assert (ffw[0] & 0x07) == 0x03


def test_ffw_mdt():
    a = FieldAttrs(mdt=True)
    ffw = encode_ffw(a)
    assert (ffw[0] & 0x08) == 0x08


def test_screen_attr_default():
    a = FieldAttrs()
    assert encode_screen_attr(a) == SA_GREEN


def test_screen_attr_intensified():
    a = FieldAttrs(intensified=True)
    assert encode_screen_attr(a) == SA_WHITE


def test_screen_attr_hidden():
    a = FieldAttrs(hidden=True)
    assert encode_screen_attr(a) == SA_NONDISPLAY


def test_screen_attr_color_red():
    a = FieldAttrs(fg=Color.RED)
    assert encode_screen_attr(a) == SA_RED


def test_screen_attr_color_blue():
    a = FieldAttrs(fg=Color.BLUE)
    assert encode_screen_attr(a) == SA_BLUE


def test_screen_attr_hidden_overrides_color():
    a = FieldAttrs(hidden=True, fg=Color.RED)
    assert encode_screen_attr(a) == SA_NONDISPLAY
