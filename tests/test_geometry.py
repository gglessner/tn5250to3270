import pytest
from tn5250to3270.geometry import GeometryMap, GeometryEntry, UnknownTerminalType


def test_match_known_type():
    m = GeometryMap({
        "IBM-3477-FC": GeometryEntry("IBM-3278-5-E", 27, 132),
        "IBM-3179-2":  GeometryEntry("IBM-3278-2-E", 24, 80),
    })
    e = m.match("IBM-3477-FC")
    assert e.tn3270_type == "IBM-3278-5-E"
    assert e.rows == 27
    assert e.cols == 132


def test_match_case_insensitive():
    m = GeometryMap({"IBM-3179-2": GeometryEntry("IBM-3278-2-E", 24, 80)})
    assert m.match("ibm-3179-2").rows == 24


def test_match_unknown_raises():
    m = GeometryMap({})
    with pytest.raises(UnknownTerminalType):
        m.match("VT100")


def test_default_map_has_common_types():
    m = GeometryMap.default()
    assert m.match("IBM-3477-FC").rows == 27
    assert m.match("IBM-5251-11").rows == 24
