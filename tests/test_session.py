import pytest
import threading
from unittest.mock import MagicMock, patch
from tn5250to3270.session import Session
from tn5250to3270.config import Config
from tn5250to3270.geometry import GeometryMap, GeometryEntry, UnknownTerminalType
from tn5250to3270.screen.model import VirtualScreen
from tn5250to3270.tn5250.negotiator import NegotiationResult as R5250
from tn5250to3270.tn3270.negotiator import NegotiationResult as R3270


def make_config():
    return Config(
        listen_host="127.0.0.1", listen_port=0,
        upstream_host="fake.host", upstream_port=23,
        tls_enabled=False,
        geometry=GeometryMap({
            "IBM-3179-2": GeometryEntry("IBM-3278-2-E", 24, 80),
        }),
    )


# ─────────────────────────────────────────────────────────────────────
# Task 5.1: negotiation phase
# ─────────────────────────────────────────────────────────────────────

def test_negotiation_sequence():
    """5250 neg → geometry match → connect → 3270 neg → screen sized."""
    cfg = make_config()
    client_sock = MagicMock()
    sess = Session(client_sock, cfg)

    # Stub both negotiators and the upstream connect
    fake_5250_codec = MagicMock()
    fake_3270_codec = MagicMock()
    with patch.object(sess, "_negotiate_5250",
                      return_value=R5250(term_type="IBM-3179-2",
                                         devname="DSP01", codec=fake_5250_codec)), \
         patch.object(sess, "_connect_upstream",
                      return_value=MagicMock()) as mock_connect, \
         patch.object(sess, "_negotiate_3270",
                      return_value=R3270(e_mode=True, device_type="IBM-3278-2-E",
                                         lu_name="TCP01", codec=fake_3270_codec)):
        sess._do_negotiation()

    assert sess.screen is not None
    assert sess.screen.rows == 24
    assert sess.screen.cols == 80
    assert sess.e_mode is True
    assert sess.lu_name == "TCP01"
    assert sess.codec_5250 is fake_5250_codec
    assert sess.codec_3270 is fake_3270_codec
    assert sess.mode14 is False  # 24*80 = 1920 < 4096
    assert mock_connect.called


def test_unknown_term_type_closes_before_dialing():
    """Critical: geometry mismatch must NOT dial the upstream host."""
    cfg = make_config()
    client_sock = MagicMock()
    sess = Session(client_sock, cfg)

    with patch.object(sess, "_negotiate_5250",
                      return_value=R5250(term_type="VT100",
                                         devname=None, codec=MagicMock())), \
         patch.object(sess, "_connect_upstream") as mock_connect:
        with pytest.raises(UnknownTerminalType):
            sess._do_negotiation()
    assert not mock_connect.called  # didn't dial upstream


def test_devname_passed_through_as_lu_name():
    """DEVNAME from 5250 NEW-ENVIRON → CONNECT lu-name in TN3270E."""
    cfg = make_config()
    sess = Session(MagicMock(), cfg)
    captured_lu = []
    with patch.object(sess, "_negotiate_5250",
                      return_value=R5250(term_type="IBM-3179-2",
                                         devname="MYDEV", codec=MagicMock())), \
         patch.object(sess, "_connect_upstream", return_value=MagicMock()), \
         patch.object(sess, "_negotiate_3270",
                      side_effect=lambda sock, dt, lu: captured_lu.append(lu) or
                                  R3270(e_mode=True, device_type=dt, lu_name=lu,
                                        codec=MagicMock())):
        sess._do_negotiation()
    assert captured_lu == ["MYDEV"]


# ─────────────────────────────────────────────────────────────────────
# Task 5.2: host→client path
# ─────────────────────────────────────────────────────────────────────
from tn5250to3270.tn3270.constants import CMD_EW, ORD_SBA, ORD_SF, ORD_IC


def test_host_record_renders_to_client():
    """End-to-end host→client: feed a 3270 EW, expect a 5250 WTD on client."""
    cfg = make_config()
    client_sent = bytearray()
    client_sock = MagicMock()
    client_sock.sendall = lambda d: client_sent.extend(d)

    sess = Session(client_sock, cfg)
    sess.screen = VirtualScreen(24, 80)
    sess.codec_5250 = MagicMock()
    sess.codec_5250.wrap_record = lambda d: d + b"\xff\xef"  # IAC EOR
    sess.e_mode = False
    sess.mode14 = False

    # Build a 3270 record: EW + WCC(unlock+resetMDT) + SBA(0) + SF(unprotected) + 'X' + IC
    record = bytes([CMD_EW, 0xC3,
                    ORD_SBA, 0x40, 0x40,    # SBA 0
                    ORD_SF, 0x40,            # SF unprotected
                    0xE7,                    # 'X'
                    ORD_IC])

    sess._handle_host_record(record)

    # Screen should be updated
    assert sess.screen.cells[0].is_field_attr is True
    assert sess.screen.cells[1].char == 0xE7
    assert sess.screen.keyboard_locked is False
    # Client should have received a WTD
    assert len(client_sent) > 0
    # Verify it's a GDS record
    assert client_sent[2:4] == b"\x12\xa0"


def test_host_query_intercepted():
    """WSF Read Partition Query → answer host directly, client sees nothing."""
    cfg = make_config()
    client_sent = bytearray()
    host_sent = bytearray()
    client_sock = MagicMock()
    client_sock.sendall = lambda d: client_sent.extend(d)

    sess = Session(client_sock, cfg)
    sess.screen = VirtualScreen(24, 80)
    sess.codec_5250 = MagicMock()
    sess.codec_3270 = MagicMock()
    sess.codec_3270.wrap_record = lambda d: d + b"\xff\xef"
    sess.host_sock = MagicMock()
    sess.host_sock.sendall = lambda d: host_sent.extend(d)
    sess.e_mode = False
    sess.mode14 = False

    # WSF: cmd 0xF3 + len(2) + SFID 0x01 + PID 0xFF + type 0x02 (Query)
    record = bytes([0xF3, 0x00, 0x05, 0x01, 0xFF, 0x02])
    sess._handle_host_record(record)

    assert len(client_sent) == 0  # client never sees it
    assert len(host_sent) > 0     # we replied to host
    assert host_sent[0] == 0x88   # AID_SF — Query Reply


# ─────────────────────────────────────────────────────────────────────
# Task 5.3: client→host path + ATN handling
# ─────────────────────────────────────────────────────────────────────
from tn5250to3270.tn5250.gds import pack_gds
from tn5250to3270.tn5250.constants import AID5_ENTER, AID5_PRINT, ORD_SBA as ORD_SBA_5
from tn5250to3270.tn3270.constants import AID_ENTER as AID_ENTER_3, AID_PA1
from tn5250to3270.screen.field import FieldAttrs
from tn5250to3270.screen import ops


def test_client_input_translates_to_3270():
    """Enter + field data → host gets AID_ENTER + SBA + data."""
    cfg = make_config()
    host_sent = bytearray()
    sess = Session(MagicMock(), cfg)
    sess.screen = VirtualScreen(24, 80)
    # Set up a field at pos 10 (row 0 col 10 → 1-based row 1 col 11)
    sess.screen.apply(ops.DefineField(pos=10, attrs=FieldAttrs(protected=False)))
    sess.codec_3270 = MagicMock()
    sess.codec_3270.wrap_record = lambda d: d + b"\xff\xef"
    sess.host_sock = MagicMock()
    sess.host_sock.sendall = lambda d: host_sent.extend(d)
    sess.e_mode = False
    sess.mode14 = False

    # Build 5250 inbound: cursor (1,11), Enter, SBA (1,12) + data 'AB'
    # (1,12) is the data start of the field at pos 10 → linear 11
    payload = bytes([1, 11, AID5_ENTER,
                     ORD_SBA_5, 1, 12]) + b"\xc1\xc2"
    record = pack_gds(payload, opcode=0)

    sess._handle_client_record(record)

    # Screen MDT should be set, data stored
    assert sess.screen.fields[0].attrs.mdt is True
    assert sess.screen.cells[11].char == 0xC1
    assert sess.screen.cells[12].char == 0xC2
    assert sess.screen.keyboard_locked is True  # locked after submit
    # Host should have received: AID_ENTER + cursor + SBA + addr + data
    assert host_sent[0] == AID_ENTER_3
    # Verify field data made it through
    assert b"\xc1\xc2" in bytes(host_sent)


def test_client_unmappable_aid_dropped():
    """AID5_PRINT has no 3270 mapping → silently dropped, host sees nothing."""
    cfg = make_config()
    host_sent = bytearray()
    sess = Session(MagicMock(), cfg)
    sess.screen = VirtualScreen(24, 80)
    sess.codec_3270 = MagicMock()
    sess.host_sock = MagicMock()
    sess.host_sock.sendall = lambda d: host_sent.extend(d)
    sess.e_mode = False
    sess.mode14 = False

    payload = bytes([1, 1, AID5_PRINT])  # Print — no 3270 mapping
    record = pack_gds(payload, opcode=0)
    sess._handle_client_record(record)
    assert len(host_sent) == 0


def test_client_atn_flag_sends_pa1():
    """GDS atn=True (non-E-mode) → host receives PA1 short read."""
    cfg = make_config()
    host_sent = bytearray()
    sess = Session(MagicMock(), cfg)
    sess.screen = VirtualScreen(24, 80)
    sess.screen.cursor = 42  # arbitrary
    sess.codec_3270 = MagicMock()
    sess.codec_3270.wrap_record = lambda d: d + b"\xff\xef"
    sess.host_sock = MagicMock()
    sess.host_sock.sendall = lambda d: host_sent.extend(d)
    sess.e_mode = False  # non-E-mode → PA1 fallback path
    sess.mode14 = False

    # GDS record with ATN flag set, empty payload
    record = pack_gds(b"", opcode=0, atn=True)
    sess._handle_client_record(record)

    # Host should receive PA1 — AID byte ONLY (no cursor), per wire trace.
    # pentest.db rec 45: PA1 = 6C FFEF, just 1 byte + EOR.
    assert len(host_sent) > 0
    assert host_sent[0] == AID_PA1
    assert len(host_sent) == 3  # AID(1) + IAC EOR(2)


# ─────────────────────────────────────────────────────────────────────
# Task 5.4: run() lifecycle + shutdown
# ─────────────────────────────────────────────────────────────────────

def test_shutdown_closes_both_idempotent():
    """_shutdown can be called from both reader threads' finally blocks."""
    cfg = make_config()
    client_sock = MagicMock()
    sess = Session(client_sock, cfg)
    sess.host_sock = MagicMock()
    sess._shutdown()
    sess._shutdown()  # second call shouldn't raise
    assert client_sock.close.called
    assert sess.host_sock.close.called
    # Idempotent: close called exactly once each (second _shutdown is a no-op)
    assert client_sock.close.call_count == 1
    assert sess.host_sock.close.call_count == 1


def test_run_handles_negotiation_failure_gracefully():
    """run() catches exceptions, closes client, never raises to listener."""
    cfg = make_config()
    client_sock = MagicMock()
    sess = Session(client_sock, cfg)
    with patch.object(sess, "_negotiate_5250",
                      side_effect=Exception("boom")):
        sess.run()  # should not raise
    assert client_sock.close.called
