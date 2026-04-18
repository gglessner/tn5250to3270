from dataclasses import dataclass


class UnknownTerminalType(Exception):
    pass


@dataclass(frozen=True, slots=True)
class GeometryEntry:
    tn3270_type: str
    rows: int
    cols: int


class GeometryMap:
    """5250 terminal-type string → 3270 model + dimensions."""

    def __init__(self, entries: dict[str, GeometryEntry]):
        self._entries = {k.upper(): v for k, v in entries.items()}

    def match(self, term_type_5250: str) -> GeometryEntry:
        key = term_type_5250.upper()
        if key not in self._entries:
            raise UnknownTerminalType(
                f"5250 terminal type {term_type_5250!r} not in geometry map; "
                f"known types: {sorted(self._entries.keys())}"
            )
        return self._entries[key]

    @classmethod
    def default(cls) -> "GeometryMap":
        return cls({
            "IBM-3477-FC":  GeometryEntry("IBM-3278-5-E", 27, 132),
            "IBM-3477-FG":  GeometryEntry("IBM-3278-5-E", 27, 132),
            "IBM-3180-2":   GeometryEntry("IBM-3278-5-E", 27, 132),
            "IBM-3179-2":   GeometryEntry("IBM-3278-2-E", 24, 80),
            "IBM-3196-A1":  GeometryEntry("IBM-3278-2-E", 24, 80),
            "IBM-5251-11":  GeometryEntry("IBM-3278-2",   24, 80),
            "IBM-5291-1":   GeometryEntry("IBM-3278-2",   24, 80),
        })
