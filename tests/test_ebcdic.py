import pytest
from tn5250to3270 import ebcdic


def test_ebcdic_roundtrip_cp037():
    e = ebcdic.Codec("cp037")
    assert e.encode("HELLO") == b"\xc8\xc5\xd3\xd3\xd6"
    assert e.decode(b"\xc8\xc5\xd3\xd3\xd6") == "HELLO"


def test_ebcdic_unknown_codepage_raises():
    with pytest.raises(LookupError):
        ebcdic.Codec("cp99999")
