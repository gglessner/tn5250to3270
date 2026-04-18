"""Thin wrapper around stdlib codecs for EBCDIC pages."""
import codecs


class Codec:
    def __init__(self, codepage: str = "cp037"):
        self._info = codecs.lookup(codepage)  # validates, raises LookupError
        self.codepage = codepage

    def encode(self, s: str) -> bytes:
        return s.encode(self.codepage)

    def decode(self, b: bytes) -> str:
        return b.decode(self.codepage, errors="replace")
