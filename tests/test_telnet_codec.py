import pytest
from tn5250to3270.telnet.codec import TelnetCodec
from tn5250to3270.telnet.options import (
    IAC, EOR_CMD, WILL, WONT, DO, DONT, SB, SE,
    OPT_BINARY, OPT_EOR, OPT_TTYPE, TTYPE_IS, TTYPE_SEND
)


def test_plain_data_until_eor():
    c = TelnetCodec()
    records = c.feed(b"hello" + bytes([IAC, EOR_CMD]))
    assert records == [b"hello"]


def test_no_eor_no_record():
    c = TelnetCodec()
    assert c.feed(b"partial") == []
    assert c.feed(b" data") == []
    assert c.feed(bytes([IAC, EOR_CMD])) == [b"partial data"]


def test_iac_iac_unescapes_to_single_ff():
    c = TelnetCodec()
    records = c.feed(bytes([0x01, IAC, IAC, 0x02, IAC, EOR_CMD]))
    assert records == [bytes([0x01, 0xFF, 0x02])]


def test_two_records_in_one_feed():
    c = TelnetCodec()
    payload = b"AAA" + bytes([IAC, EOR_CMD]) + b"BBB" + bytes([IAC, EOR_CMD])
    assert c.feed(payload) == [b"AAA", b"BBB"]


def test_iac_split_across_feeds():
    c = TelnetCodec()
    assert c.feed(b"X" + bytes([IAC])) == []   # IAC dangling
    assert c.feed(bytes([EOR_CMD])) == [b"X"]  # completes EOR


def test_will_do_stripped_and_callback_fired():
    seen = []
    c = TelnetCodec(on_command=lambda cmd, opt: seen.append((cmd, opt)))
    records = c.feed(bytes([IAC, WILL, OPT_BINARY, 0xC1, IAC, EOR_CMD]))
    assert records == [bytes([0xC1])]
    assert seen == [(WILL, OPT_BINARY)]


def test_subnegotiation_stripped_and_callback():
    sb_seen = []
    c = TelnetCodec(on_subneg=lambda opt, data: sb_seen.append((opt, data)))
    # IAC SB TTYPE SEND IAC SE
    payload = bytes([IAC, SB, OPT_TTYPE, TTYPE_SEND, IAC, SE, 0xC1, IAC, EOR_CMD])
    records = c.feed(payload)
    assert records == [bytes([0xC1])]
    assert sb_seen == [(OPT_TTYPE, bytes([TTYPE_SEND]))]


def test_iac_iac_inside_subneg():
    """Subneg data can contain 0xFF — must be escaped as IAC IAC."""
    sb_seen = []
    c = TelnetCodec(on_subneg=lambda opt, data: sb_seen.append((opt, data)))
    # SB TTYPE IS "I\xffM" SE  → on wire: IAC SB 24 0 'I' IAC IAC 'M' IAC SE
    payload = bytes([IAC, SB, OPT_TTYPE, TTYPE_IS, ord('I'), IAC, IAC, ord('M'), IAC, SE, IAC, EOR_CMD])
    c.feed(payload)
    assert sb_seen == [(OPT_TTYPE, bytes([TTYPE_IS, ord('I'), 0xFF, ord('M')]))]


def test_wrap_record_appends_eor():
    c = TelnetCodec()
    assert c.wrap_record(b"hello") == b"hello" + bytes([IAC, EOR_CMD])


def test_wrap_record_escapes_ff():
    c = TelnetCodec()
    out = c.wrap_record(bytes([0x01, 0xFF, 0x02]))
    assert out == bytes([0x01, IAC, IAC, 0x02, IAC, EOR_CMD])


def test_wrap_record_roundtrips_through_feed():
    c1, c2 = TelnetCodec(), TelnetCodec()
    payload = bytes([0xFF, 0x00, 0xFF, 0xFF, 0xC1])  # nasty: lots of FF
    wire = c1.wrap_record(payload)
    assert c2.feed(wire) == [payload]


def test_send_command():
    c = TelnetCodec()
    assert c.send_command(WILL, OPT_BINARY) == bytes([IAC, WILL, OPT_BINARY])


def test_send_subneg_escapes_ff_in_payload():
    c = TelnetCodec()
    out = c.send_subneg(OPT_TTYPE, bytes([TTYPE_IS, 0xFF, ord('X')]))
    assert out == bytes([IAC, SB, OPT_TTYPE, TTYPE_IS, IAC, IAC, ord('X'), IAC, SE])


def test_option_tracking():
    c = TelnetCodec()
    # Initially nothing negotiated
    assert not c.local_enabled(OPT_BINARY)
    assert not c.remote_enabled(OPT_BINARY)
    # Peer says WILL BINARY → they want to enable on their side
    c.feed(bytes([IAC, WILL, OPT_BINARY]))
    # We mark it after WE accept (caller sends DO and tells codec)
    c.note_remote_enabled(OPT_BINARY)
    assert c.remote_enabled(OPT_BINARY)
    # We send WILL BINARY, peer sends DO → we're enabled locally
    c.note_local_enabled(OPT_BINARY)
    assert c.local_enabled(OPT_BINARY)


def test_eor_negotiated_helper():
    c = TelnetCodec()
    assert not c.eor_negotiated()
    c.note_local_enabled(OPT_EOR)
    c.note_remote_enabled(OPT_EOR)
    assert c.eor_negotiated()
