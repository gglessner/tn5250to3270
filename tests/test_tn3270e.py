import pytest
from tn5250to3270.tn3270.tn3270e import (
    EHeader, pack_header, unpack_header,
    DT_3270_DATA, DT_RESPONSE, DT_BIND_IMAGE, DT_SSCP_LU_DATA,
    RQ_NONE, RQ_ERROR, RQ_ALWAYS,
    RSP_POSITIVE, RSP_NEGATIVE,
)


def test_pack_unpack_roundtrip():
    h = EHeader(data_type=DT_3270_DATA, request_flag=RQ_ALWAYS,
                response_flag=0, seq=0x1234)
    packed = pack_header(h)
    assert len(packed) == 5
    assert unpack_header(packed) == h


def test_pack_3270_data():
    h = EHeader(data_type=DT_3270_DATA, request_flag=RQ_NONE,
                response_flag=0, seq=0)
    assert pack_header(h) == bytes([0x00, 0x00, 0x00, 0x00, 0x00])


def test_unpack_real_capture():
    # Captured from x3270 trace: 3270-DATA, no response requested, seq 1
    raw = bytes([0x00, 0x00, 0x00, 0x00, 0x01])
    h = unpack_header(raw)
    assert h.data_type == DT_3270_DATA
    assert h.seq == 1


def test_pack_response():
    h = EHeader(data_type=DT_RESPONSE, request_flag=0,
                response_flag=RSP_POSITIVE, seq=42)
    p = pack_header(h)
    assert p[0] == DT_RESPONSE
    assert p[2] == RSP_POSITIVE


# ── Query Reply synthesizer ────────────────────────────────────────

from tn5250to3270.tn3270.query_reply import (
    is_read_partition_query, build_query_reply,
    QR_SUMMARY, QR_USABLE_AREA, QR_COLOR, QR_HIGHLIGHT,
    QR_REPLY_MODES, QR_IMPLICIT_PARTITION,
)


def test_detect_read_partition_query():
    # WSF data: length(2) + SFID=0x01 (Read Partition) + PID=0xFF + type=0x02 (Query)
    wsf = bytes([0x00, 0x05, 0x01, 0xFF, 0x02])
    assert is_read_partition_query(wsf) is True


def test_detect_query_list_also_handled():
    # type=0x03 = Query List — we also handle this
    wsf = bytes([0x00, 0x05, 0x01, 0xFF, 0x03])
    assert is_read_partition_query(wsf) is True
    # But not other SFIDs
    wsf_other = bytes([0x00, 0x05, 0x02, 0xFF, 0x02])
    assert is_read_partition_query(wsf_other) is False
    # And not too-short data
    assert is_read_partition_query(b"\x00\x03\x01") is False


def test_build_query_reply_structure():
    reply = build_query_reply(rows=24, cols=80, color=True)
    # Reply is: AID_SF (0x88) + structured fields,
    # each: len(2) + SFID(0x81) + QCODE + data
    assert reply[0] == 0x88
    # First SF should be Summary (lists which QCODEs we support)
    assert reply[3] == 0x81  # SFID = Query Reply
    assert reply[4] == QR_SUMMARY
    # Summary data should include the QCODEs we claim
    summary_len = (reply[1] << 8) | reply[2]
    summary_data = reply[5:1 + summary_len]
    assert QR_USABLE_AREA in summary_data
    assert QR_COLOR in summary_data  # color=True
    assert QR_HIGHLIGHT in summary_data
    assert QR_REPLY_MODES in summary_data
    assert QR_IMPLICIT_PARTITION in summary_data


def test_build_query_reply_usable_area():
    reply = build_query_reply(rows=27, cols=132, color=True)
    # Find the Usable Area SF and check it encodes 132x27.
    # Walk the structured fields. SF layout: len(2) + 0x81 + qcode + data.
    # Within data, struct >BBHHBLLBBI: B B H H ... → cols at SF+6, rows at SF+8.
    i = 1  # skip AID
    found = False
    while i < len(reply):
        sf_len = (reply[i] << 8) | reply[i + 1]
        if reply[i + 3] == QR_USABLE_AREA:
            width = (reply[i + 6] << 8) | reply[i + 7]
            height = (reply[i + 8] << 8) | reply[i + 9]
            assert width == 132
            assert height == 27
            found = True
            break
        i += sf_len
    assert found
