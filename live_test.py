#!/usr/bin/env python3
"""Live wire test: real proxy subprocess, real TCP, full protocol conversion.

Unlike the e2e tests (which use socketpair + Session directly), this:
  - Runs `python -m tn5250to3270` as a real subprocess
  - Exercises listener.py's accept loop
  - Uses real TCP connect for both legs
  - Decodes the converted screen to show what a user would actually see
"""
import socket
import subprocess
import sys
import time
import textwrap
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tests.integration.mock_host import MockHost
from tn5250to3270.telnet.codec import TelnetCodec
from tn5250to3270.telnet.options import (
    IAC, WILL, WONT, DO, SB, SE,
    OPT_BINARY, OPT_EOR, OPT_TTYPE, OPT_NEW_ENVIRON,
    TTYPE_IS, TTYPE_SEND,
)
from tn5250to3270.tn5250.gds import unpack_gds, pack_gds
from tn5250to3270.tn5250.constants import (
    ESC, CMD_CLEAR_UNIT, CMD_WTD, ORD_SBA, ORD_SF, ORD_IC, AID5_ENTER,
)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def render_5250_screen(wtd_payload: bytes, rows: int, cols: int) -> str:
    """Decode a WTD payload into a human-readable screen.
    Just enough to show the demo — not a full 5250 client."""
    grid = [[" "] * cols for _ in range(rows)]
    i = 0
    n = len(wtd_payload)
    # Skip ESC commands until we hit WTD body
    while i < n and wtd_payload[i] == ESC:
        cmd = wtd_payload[i + 1]
        if cmd == CMD_CLEAR_UNIT:
            i += 2
        elif cmd == CMD_WTD:
            i += 4  # ESC WTD CC1 CC2
            break
        else:
            i += 2
    # Walk orders
    r, c = 0, 0
    cursor_r, cursor_c = 0, 0
    while i < n:
        b = wtd_payload[i]
        if b == ORD_SBA:
            r, c = wtd_payload[i + 1] - 1, wtd_payload[i + 2] - 1
            i += 3
        elif b == ORD_SF:
            # SF FFW(2) attr(1) len(2) → mark with [
            grid[r][c] = "["
            c += 1
            if c >= cols:
                c, r = 0, r + 1
            i += 6
        elif b == ORD_IC:
            cursor_r, cursor_c = wtd_payload[i + 1] - 1, wtd_payload[i + 2] - 1
            i += 3
        else:
            # data — EBCDIC byte
            ch = bytes([b]).decode("cp037", errors="replace")
            if 0 <= r < rows and 0 <= c < cols:
                grid[r][c] = ch
            c += 1
            if c >= cols:
                c, r = 0, r + 1
            i += 1
    out = []
    for ri, row in enumerate(grid[:5]):  # show first 5 rows
        line = "".join(row).rstrip()
        marker = " ◄ cursor" if ri == cursor_r else ""
        out.append(f"  {ri+1:2d}│{line}{marker}")
    return "\n".join(out)


def main():
    print("═══ LIVE WIRE TEST ═══════════════════════════════════════\n")

    # ── 1. Start mock 3270 host ─────────────────────────────────────
    host = MockHost()
    host.start()
    print(f"  [1] Mock TN3270 host listening on 127.0.0.1:{host.port}")
    print(f"      Will send: 'USER:' label + input field, basic TN3270 (no E-mode)\n")

    # ── 2. Write config + start proxy subprocess ────────────────────
    proxy_port = free_port()
    cfg = Path("/tmp/proxy_test.yaml")
    cfg.write_text(textwrap.dedent(f"""
        listen:
          host: 127.0.0.1
          port: {proxy_port}
        upstream:
          host: 127.0.0.1
          port: {host.port}
          tls:
            enabled: false
        geometry:
          IBM-3179-2: [IBM-3278-2, 24, 80]
        logging:
          level: INFO
    """))
    proxy = subprocess.Popen(
        [".venv/bin/python", "-m", "tn5250to3270", "-c", str(cfg)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    # Wait for listener to bind
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", proxy_port), timeout=0.5)
            s.close()
            break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.05)
    else:
        proxy.terminate()
        raise RuntimeError("proxy never started listening")
    print(f"  [2] Proxy subprocess running, listening on 127.0.0.1:{proxy_port}")
    print(f"      Config: 5250 client (IBM-3179-2) → 3270 host (IBM-3278-2)\n")

    # ── 3. Connect as a 5250 client ─────────────────────────────────
    client = socket.create_connection(("127.0.0.1", proxy_port), timeout=10)
    codec = TelnetCodec()
    print(f"  [3] Connected to proxy as TN5250 client\n")

    # ── 4. Drive 5250 telnet negotiation ────────────────────────────
    # Proxy sends DO BINARY/EOR/TTYPE/NEW-ENVIRON, WILL BINARY/EOR
    # We respond: WILL BINARY/EOR/TTYPE, DO BINARY/EOR, WONT NEW-ENVIRON
    client.sendall(bytes([
        IAC, WILL, OPT_BINARY, IAC, WILL, OPT_EOR, IAC, WILL, OPT_TTYPE,
        IAC, DO, OPT_BINARY, IAC, DO, OPT_EOR,
        IAC, WONT, OPT_NEW_ENVIRON,
    ]))
    # Wait for SB TTYPE SEND
    buf = b""
    target = bytes([IAC, SB, OPT_TTYPE, TTYPE_SEND, IAC, SE])
    while target not in buf:
        buf += client.recv(256)
    # Reply with our terminal type
    client.sendall(bytes([IAC, SB, OPT_TTYPE, TTYPE_IS]) +
                   b"IBM-3179-2" + bytes([IAC, SE]))
    print(f"  [4] TN5250 negotiation complete: term-type = IBM-3179-2\n")

    # ── 5. Receive and decode the converted screen ──────────────────
    deadline = time.monotonic() + 5
    records = []
    while not records and time.monotonic() < deadline:
        chunk = client.recv(4096)
        records.extend(codec.feed(chunk))
    assert records, "no WTD arrived"

    h, payload = unpack_gds(records[0])
    print(f"  [5] Received TN5250 WTD record:")
    print(f"      GDS: length={h.length} opcode=0x{h.opcode:02x} "
          f"({'PUT_GET' if h.opcode == 3 else 'OUTPUT_ONLY'})")
    print(f"      Payload: {len(payload)} bytes, hex preview: "
          f"{payload[:20].hex()}...")
    print(f"\n      ┌─ Decoded screen (first 5 rows, 80 cols) ─────────────")
    print(render_5250_screen(payload, 24, 80))
    print(f"      └──────────────────────────────────────────────────────\n")

    # Verify the conversion worked
    assert b"\xE4\xE2\xC5\xD9" in payload, "USER (EBCDIC) not in payload"
    print(f"  ✓ 'USER' (EBCDIC E4 E2 C5 D9) found in 5250 payload\n")

    # ── 6. Send Enter, verify host receives 3270 inbound ────────────
    enter_payload = bytes([1, 7, AID5_ENTER])  # cursor row 1 col 7, Enter
    client.sendall(codec.wrap_record(pack_gds(enter_payload, opcode=0)))
    print(f"  [6] Sent TN5250 Enter (AID 0xF1) at cursor (1,7)\n")

    # Wait for mock host to receive
    deadline = time.monotonic() + 3
    while not host.received and time.monotonic() < deadline:
        time.sleep(0.01)

    assert host.received, "host received nothing"
    inbound = host.received[0]
    print(f"  [7] Mock host received TN3270 inbound: {inbound.hex()}")
    print(f"      AID byte: 0x{inbound[0]:02x} "
          f"({'AID_ENTER ✓' if inbound[0] == 0x7D else 'WRONG'})")
    print(f"      Cursor addr: {inbound[1]:02x} {inbound[2]:02x} "
          f"(decoded: position {((inbound[1]-0x40)*64)+(inbound[2]-0x40) if inbound[1]>=0x40 else '?'})")
    assert inbound[0] == 0x7D, f"expected AID_ENTER (0x7D), got 0x{inbound[0]:02x}"
    print(f"\n  ✓ 5250 AID 0xF1 → 3270 AID 0x7D conversion verified\n")

    # ── 7. Verify proxy negotiated correct 3270 type with host ──────
    print(f"  [8] Mock host received term-type from proxy: {host.term_type!r}")
    assert host.term_type == "IBM-3278-2", f"geometry mismatch: {host.term_type}"
    print(f"  ✓ Geometry mapping IBM-3179-2 → IBM-3278-2 verified\n")

    # ── Cleanup ─────────────────────────────────────────────────────
    client.close()
    proxy.terminate()
    proxy.wait(timeout=2)
    host.stop()
    cfg.unlink()

    # Show some proxy log output
    log_output = proxy.stdout.read()
    print(f"  ─── Proxy log (last lines) ───")
    for line in log_output.splitlines()[-6:]:
        print(f"      {line}")

    print(f"\n═══ ALL LIVE CHECKS PASSED ═══════════════════════════════")
    print(f"    3270 EW → ScreenOp[] → VirtualScreen → 5250 WTD: ✓")
    print(f"    5250 Enter → 3270 AID_ENTER: ✓")
    print(f"    Geometry matching: ✓")
    print(f"    Real TCP, real subprocess, real listener: ✓")


if __name__ == "__main__":
    main()
