import pytest
from tn5250to3270.tn3270.negotiator import TN3270Negotiator, NegotiationResult
from tn5250to3270.telnet.options import (
    IAC, WILL, WONT, DO, DONT, SB, SE,
    OPT_BINARY, OPT_EOR, OPT_TTYPE, OPT_TN3270E,
    TTYPE_IS, TTYPE_SEND,
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
        r = self._responses.pop(0)
        return r
    def settimeout(self, t):
        pass


def test_hercules_style_ttype_gated_binary_eor():
    """Real-world regression: Hercules-style hosts refuse BINARY/EOR until
    TTYPE is settled, then re-offer. The old negotiator deadlocked here:
    it waited for DO BINARY (which got DONT'd) before answering TTYPE,
    while the host waited for TTYPE before sending DO BINARY.

    Captured from 10.0.0.207:3270 — actual wire trace.
    """
    sock = FakeSocket([
        # Host's first response to our WILL TN3270E
        bytes([IAC, DO, OPT_TTYPE, IAC, DONT, OPT_TN3270E]),
        # After we WILL TTYPE, host sends SB TTYPE SEND.
        # It also REFUSES BINARY/EOR at this point — doesn't trust us yet.
        bytes([IAC, DONT, OPT_BINARY, IAC, DONT, OPT_EOR,
               IAC, WONT, OPT_BINARY, IAC, WONT, OPT_EOR,
               IAC, SB, OPT_TTYPE, TTYPE_SEND, IAC, SE]),
        # After we send TTYPE IS IBM-3278-2, host changes its mind.
        # NOW it offers BINARY/EOR.
        bytes([IAC, WILL, OPT_BINARY, IAC, DO, OPT_BINARY,
               IAC, WILL, OPT_EOR, IAC, DO, OPT_EOR]),
    ])
    n = TN3270Negotiator(sock, device_type="IBM-3278-2",
                         lu_name=None, timeout=5.0)
    result = n.negotiate()

    assert result.e_mode is False
    assert result.device_type == "IBM-3278-2"
    # The early DONT/WONT must NOT have prevented eventual agreement
    assert result.codec.eor_negotiated()
    assert result.codec.local_enabled(OPT_BINARY)
    assert result.codec.remote_enabled(OPT_BINARY)
    # We must have sent TTYPE IS (proves we got past the deadlock)
    assert b"IBM-3278-2" in sock.sent


def test_basic_tn3270_when_host_refuses_e():
    """Host says WONT TN3270E -> fall back to BINARY+EOR+TTYPE."""
    sock = FakeSocket([
        # We send WILL TN3270E. Host says WONT.
        bytes([IAC, WONT, OPT_TN3270E]),
        # Then host does the basic dance: DO TTYPE, DO BINARY, DO EOR
        bytes([IAC, DO, OPT_TTYPE, IAC, DO, OPT_BINARY, IAC, DO, OPT_EOR,
               IAC, WILL, OPT_BINARY, IAC, WILL, OPT_EOR]),
        # Host asks for terminal type
        bytes([IAC, SB, OPT_TTYPE, TTYPE_SEND, IAC, SE]),
        # That's it -- negotiation done
    ])
    n = TN3270Negotiator(sock, device_type="IBM-3278-2", lu_name=None, timeout=5.0)
    result = n.negotiate()
    assert result.e_mode is False
    assert result.device_type == "IBM-3278-2"
    # We should have sent WILL TN3270E, then WILL BINARY/EOR/TTYPE, then SB TTYPE IS
    assert bytes([IAC, WILL, OPT_TN3270E]) in sock.sent
    assert b"IBM-3278-2" in sock.sent


def test_tn3270e_full_handshake():
    """Host accepts TN3270E, full DEVICE-TYPE + FUNCTIONS exchange."""
    # TN3270E subneg constants (RFC 2355 sec 3)
    E_DEVICE_TYPE = 0x02
    E_FUNCTIONS   = 0x03
    E_IS          = 0x04
    E_REQUEST     = 0x07
    E_CONNECT     = 0x01

    sock = FakeSocket([
        # Host: DO TN3270E
        bytes([IAC, DO, OPT_TN3270E]),
        # We send DEVICE-TYPE REQUEST. Host replies DEVICE-TYPE IS ... CONNECT lu
        bytes([IAC, SB, OPT_TN3270E, E_DEVICE_TYPE, E_IS]) +
            b"IBM-3278-2-E" + bytes([E_CONNECT]) + b"TCP00001" +
            bytes([IAC, SE]),
        # We send FUNCTIONS REQUEST. Host replies FUNCTIONS IS [...]
        bytes([IAC, SB, OPT_TN3270E, E_FUNCTIONS, E_IS, 0x00, 0x02, 0x04, IAC, SE]),
            # 0x00=BIND-IMAGE, 0x02=RESPONSES, 0x04=SYSREQ
    ])
    n = TN3270Negotiator(sock, device_type="IBM-3278-2-E", lu_name="MYDEV", timeout=5.0)
    result = n.negotiate()
    assert result.e_mode is True
    assert result.lu_name == "TCP00001"
    assert "RESPONSES" in result.functions
    # Verify we sent DEVICE-TYPE REQUEST with our LU name
    assert b"IBM-3278-2-E" in sock.sent
    assert b"MYDEV" in sock.sent
