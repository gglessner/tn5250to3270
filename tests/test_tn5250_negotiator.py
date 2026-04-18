import pytest
from tn5250to3270.tn5250.negotiator import TN5250Negotiator, NegotiationResult
from tn5250to3270.telnet.options import (
    IAC, WILL, WONT, DO, DONT, SB, SE,
    OPT_BINARY, OPT_EOR, OPT_TTYPE, OPT_NEW_ENVIRON,
    TTYPE_IS, TTYPE_SEND, NE_IS, NE_SEND, NE_USERVAR, NE_VALUE,
)


class FakeSocket:
    """Records what's sent; serves canned responses."""
    def __init__(self, responses: list[bytes]):
        self.sent = bytearray()
        self._responses = list(responses)
    def sendall(self, data):
        self.sent += data
    def recv(self, n):
        if not self._responses:
            return b""
        return self._responses.pop(0)
    def settimeout(self, t):
        pass


def test_negotiate_minimal():
    """Client agrees to BINARY/EOR, sends term-type, no DEVNAME."""
    sock = FakeSocket([
        # We send DO BINARY/EOR/TTYPE/NEW-ENVIRON, WILL BINARY/EOR.
        # Client says WILL BINARY/EOR/TTYPE, DO BINARY/EOR.
        bytes([IAC, WILL, OPT_BINARY, IAC, WILL, OPT_EOR, IAC, WILL, OPT_TTYPE,
               IAC, DO, OPT_BINARY, IAC, DO, OPT_EOR,
               IAC, WONT, OPT_NEW_ENVIRON]),  # client doesn't do NEW-ENVIRON
        # We send SB TTYPE SEND. Client replies SB TTYPE IS IBM-3179-2.
        bytes([IAC, SB, OPT_TTYPE, TTYPE_IS]) + b"IBM-3179-2" + bytes([IAC, SE]),
    ])
    n = TN5250Negotiator(sock, timeout=5.0)
    r = n.negotiate()
    assert r.term_type == "IBM-3179-2"
    assert r.devname is None
    # Verify we sent DO TTYPE
    assert bytes([IAC, DO, OPT_TTYPE]) in sock.sent
    # Verify we sent SB TTYPE SEND
    assert bytes([IAC, SB, OPT_TTYPE, TTYPE_SEND, IAC, SE]) in sock.sent


def test_negotiate_with_devname():
    """Client supports NEW-ENVIRON and sends a DEVNAME."""
    sock = FakeSocket([
        bytes([IAC, WILL, OPT_BINARY, IAC, WILL, OPT_EOR, IAC, WILL, OPT_TTYPE,
               IAC, DO, OPT_BINARY, IAC, DO, OPT_EOR,
               IAC, WILL, OPT_NEW_ENVIRON]),
        # NEW-ENVIRON: SB 39 IS USERVAR "DEVNAME" VALUE "DSP01" SE
        bytes([IAC, SB, OPT_NEW_ENVIRON, NE_IS, NE_USERVAR]) +
            b"DEVNAME" + bytes([NE_VALUE]) + b"DSP01" +
            bytes([IAC, SE]),
        # Then term-type
        bytes([IAC, SB, OPT_TTYPE, TTYPE_IS]) + b"IBM-3477-FC" + bytes([IAC, SE]),
    ])
    n = TN5250Negotiator(sock, timeout=5.0)
    r = n.negotiate()
    assert r.term_type == "IBM-3477-FC"
    assert r.devname == "DSP01"
