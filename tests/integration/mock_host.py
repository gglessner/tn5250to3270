"""Mock TN3270 host for integration tests.

Speaks basic TN3270 (no E-mode), sends one canned screen, validates inbound.
"""
import socket
import threading
from tn5250to3270.telnet.options import (
    IAC, WILL, WONT, DO, DONT, SB, SE, EOR_CMD,
    OPT_BINARY, OPT_EOR, OPT_TTYPE, OPT_TN3270E,
    TTYPE_IS, TTYPE_SEND,
)
from tn5250to3270.telnet.codec import TelnetCodec
from tn5250to3270.tn3270.constants import CMD_EW, ORD_SBA, ORD_SF, ORD_IC


class MockHost:
    """Listens, accepts one connection, runs negotiation, sends one screen.

    Records anything inbound for assertions.
    """

    def __init__(self, screen_data: bytes | None = None):
        self.screen_data = screen_data or self._default_screen()
        self.received: list[bytes] = []
        self.term_type: str | None = None
        self.port: int = 0
        self._sock: socket.socket | None = None
        self._client: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def _default_screen(self) -> bytes:
        """A trivial logon-ish screen: one protected label, one input field."""
        return bytes([
            CMD_EW, 0xC3,                    # EW, WCC: unlock+resetMDT
            ORD_SBA, 0x40, 0x40,             # SBA 0
            ORD_SF, 0xE0,                    # SF protected
        ]) + b"\xE4\xE2\xC5\xD9\x7A" + bytes([  # "USER:" in EBCDIC
            ORD_SF, 0x40,                    # SF unprotected
            ORD_IC,                          # cursor here
        ])

    def start(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        self.port = s.getsockname()[1]
        self._sock = s
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for s in (self._client, self._sock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        if self._thread:
            self._thread.join(timeout=2)

    def _serve(self) -> None:
        try:
            client, _ = self._sock.accept()
        except OSError:
            return
        self._client = client
        codec = TelnetCodec()
        try:
            # Refuse TN3270E. WONT is a valid proactive refusal; the proxy's
            # _wait_for_cmd accepts either WONT or DONT for the same option.
            client.sendall(bytes([IAC, WONT, OPT_TN3270E]))

            # Drive basic neg: DO BINARY/EOR/TTYPE, WILL BINARY/EOR.
            # Send everything upfront — the proxy buffers and processes
            # in whatever order it pumps. Order-tolerant on both ends.
            for opt in (OPT_TTYPE, OPT_BINARY, OPT_EOR):
                client.sendall(bytes([IAC, DO, opt]))
            for opt in (OPT_BINARY, OPT_EOR):
                client.sendall(bytes([IAC, WILL, opt]))

            # Drain whatever the proxy sends in response (WILL TN3270E,
            # WILL BINARY/EOR/TTYPE, DO BINARY/EOR — five sendalls plus
            # the initial WILL TN3270E). Use a short timeout per recv;
            # bail when the wire goes quiet. We don't validate content
            # here — just clear the pipe so the term-type read below
            # doesn't pick up stale negotiation bytes.
            client.settimeout(0.3)
            drained = b""
            while True:
                try:
                    chunk = client.recv(256)
                except socket.timeout:
                    break
                if not chunk:
                    return  # peer closed
                drained += chunk

            # Ask for term-type
            client.sendall(bytes([IAC, SB, OPT_TTYPE, TTYPE_SEND, IAC, SE]))

            # Read term-type reply: IAC SB TTYPE IS <name> IAC SE.
            # Reuse anything left over from the drain (proxy may have raced
            # and sent more negotiation bytes after our timeout fired).
            client.settimeout(5.0)
            buf = drained
            marker = bytes([IAC, SB, OPT_TTYPE, TTYPE_IS])
            while marker not in buf or bytes([IAC, SE]) not in buf[buf.index(marker):]:
                chunk = client.recv(256)
                if not chunk:
                    return
                buf += chunk
            start = buf.index(marker) + 4
            end = buf.index(bytes([IAC, SE]), start)
            self.term_type = buf[start:end].decode("ascii")

            # Send the screen
            client.sendall(codec.wrap_record(self.screen_data))

            # Read whatever comes back, framed by the codec.
            client.settimeout(0.5)
            while not self._stop.is_set():
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                for rec in codec.feed(chunk):
                    self.received.append(rec)
        except OSError:
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass


if __name__ == "__main__":
    h = MockHost()
    h.start()
    print(f"mock host listening on 127.0.0.1:{h.port}")
    import time; time.sleep(60)
