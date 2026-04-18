"""End-to-end integration: MockHost ← proxy ← fake 5250 client.

Real sockets, real threads, real negotiation. The only thing faked is
the host (we control what 3270 it sends and what it receives).
"""
import socket
import threading
import time
import pytest
from tests.integration.mock_host import MockHost
from tn5250to3270.config import Config
from tn5250to3270.session import Session
from tn5250to3270.geometry import GeometryMap, GeometryEntry
from tn5250to3270.telnet.codec import TelnetCodec
from tn5250to3270.telnet.options import (
    IAC, WILL, WONT, DO, DONT, SB, SE, EOR_CMD,
    OPT_BINARY, OPT_EOR, OPT_TTYPE, OPT_NEW_ENVIRON,
    TTYPE_IS, TTYPE_SEND,
)
from tn5250to3270.tn5250.gds import pack_gds, unpack_gds
from tn5250to3270.tn5250.constants import AID5_ENTER, ESC, CMD_WTD


@pytest.fixture
def mock_host():
    h = MockHost()
    h.start()
    yield h
    h.stop()


@pytest.fixture
def proxy_session(mock_host):
    """Returns (client_socket, session_thread). Caller drives client_socket."""
    # socketpair: one end is the 'client', other end is what Session sees
    client_end, session_end = socket.socketpair()
    cfg = Config(
        listen_host="127.0.0.1", listen_port=0,
        upstream_host="127.0.0.1", upstream_port=mock_host.port,
        tls_enabled=False, negotiate_timeout=5.0, connect_timeout=5.0,
        geometry=GeometryMap({
            "IBM-3179-2": GeometryEntry("IBM-3278-2", 24, 80),
        }),
    )
    sess = Session(session_end, cfg)
    t = threading.Thread(target=sess.run, daemon=True)
    t.start()
    yield client_end, sess
    try:
        client_end.close()
    except OSError:
        pass
    t.join(timeout=2)


def play_5250_client(sock):
    """Drive the 5250 side of negotiation. Returns the codec for further use."""
    codec = TelnetCodec()
    sock.settimeout(5.0)
    # Proxy sends DO BINARY/EOR/TTYPE/NEW-ENVIRON, WILL BINARY/EOR.
    # We respond: WILL BINARY/EOR/TTYPE, DO BINARY/EOR, WONT NEW-ENVIRON.
    # Send proactively — proxy's negotiator pumps and buffers, doesn't
    # care about ordering relative to its own DOs/WILLs.
    sock.sendall(bytes([
        IAC, WILL, OPT_BINARY, IAC, WILL, OPT_EOR, IAC, WILL, OPT_TTYPE,
        IAC, DO, OPT_BINARY, IAC, DO, OPT_EOR,
        IAC, WONT, OPT_NEW_ENVIRON,
    ]))
    # Drain proxy's negotiation, wait for SB TTYPE SEND.
    # Accumulate raw bytes — we're scanning for a wire pattern, not
    # using the codec (codec callbacks would complicate this).
    buf = b""
    want = bytes([IAC, SB, OPT_TTYPE, TTYPE_SEND, IAC, SE])
    deadline = time.monotonic() + 5.0
    while want not in buf:
        if time.monotonic() > deadline:
            raise TimeoutError("never saw SB TTYPE SEND from proxy")
        buf += sock.recv(256)
    # Reply with our term-type
    sock.sendall(bytes([IAC, SB, OPT_TTYPE, TTYPE_IS]) +
                 b"IBM-3179-2" + bytes([IAC, SE]))
    return codec


def _recv_records(sock, codec, deadline):
    """Pump socket→codec until at least one record arrives or deadline passes."""
    records = []
    while not records and time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            break
        records.extend(codec.feed(chunk))
    return records


def test_e2e_screen_arrives(proxy_session, mock_host):
    """Mock host sends 3270 EW → proxy converts → we receive 5250 WTD."""
    client, sess = proxy_session
    codec = play_5250_client(client)

    # Wait for the WTD to arrive
    deadline = time.monotonic() + 5.0
    records = _recv_records(client, codec, deadline)
    assert records, "no WTD received within 5s"

    # Verify it's a valid GDS record with WTD command
    h, payload = unpack_gds(records[0])
    assert ESC in payload
    # CMD_WTD must appear right after some ESC byte
    found_wtd = any(
        payload[i] == ESC and payload[i + 1] == CMD_WTD
        for i in range(len(payload) - 1)
    )
    assert found_wtd, f"no ESC+WTD in payload: {payload.hex()}"

    # Verify "USER" appears (still EBCDIC — converter preserves bytes)
    assert b"\xE4\xE2\xC5\xD9" in payload  # USER in EBCDIC

    # Verify mock host got our matched 3270 term-type. By the time the
    # WTD arrives at us, the host-side negotiation (which produces
    # term_type) has already completed — it's strictly earlier in the
    # session flow. No sleep needed.
    assert mock_host.term_type == "IBM-3278-2"


def test_e2e_input_reaches_host(proxy_session, mock_host):
    """Send 5250 Enter → host receives 3270 inbound with AID_ENTER."""
    client, sess = proxy_session
    codec = play_5250_client(client)

    # Drain the WTD first — must consume it so the proxy's host reader
    # has finished processing the screen (and the screen has fields).
    deadline = time.monotonic() + 5.0
    records = _recv_records(client, codec, deadline)
    assert records, "no WTD received — can't proceed to input test"

    # Send Enter at cursor (1,1) with no fields
    payload = bytes([1, 1, AID5_ENTER])
    record = pack_gds(payload, opcode=0)
    client.sendall(codec.wrap_record(record))

    # Wait for host to receive — poll mock_host.received with a deadline.
    # Path: client_sock → proxy._client_reader_loop → host_sock → MockHost
    # recv loop. Three thread hops on localhost; sub-millisecond typical.
    deadline = time.monotonic() + 5.0
    while not mock_host.received and time.monotonic() < deadline:
        time.sleep(0.01)
    assert mock_host.received, "host received nothing within 5s"
    # 3270 AID_ENTER is 0x7D — first byte of inbound stream
    assert mock_host.received[0][0] == 0x7D
