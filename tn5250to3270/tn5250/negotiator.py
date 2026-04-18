"""TN5250 server-side telnet negotiation. RFC 1205 §3 + RFC 2877.

We're the server. We drive: send DO/WILL, ask for term-type, optionally
ask for NEW-ENVIRON vars. ACS sends DEVNAME via NEW-ENVIRON unprompted
on most configs, but we ask explicitly to be safe.
"""
import logging
from dataclasses import dataclass
from ..telnet.codec import TelnetCodec
from ..telnet.options import (
    IAC, WILL, WONT, DO, DONT, SB, SE,
    OPT_BINARY, OPT_EOR, OPT_TTYPE, OPT_NEW_ENVIRON,
    TTYPE_IS, TTYPE_SEND,
    NE_IS, NE_SEND, NE_VAR, NE_VALUE, NE_USERVAR,
)

log = logging.getLogger(__name__)


class NegotiationFailed(Exception):
    pass


@dataclass
class NegotiationResult:
    term_type: str
    devname: str | None
    codec: TelnetCodec


class TN5250Negotiator:
    def __init__(self, sock, timeout: float):
        self.sock = sock
        self.timeout = timeout
        self._cmds: list[tuple[int, int]] = []
        self._subnegs: list[tuple[int, bytes]] = []
        self.codec = TelnetCodec(
            on_command=lambda c, o: self._cmds.append((c, o)),
            on_subneg=lambda o, d: self._subnegs.append((o, d)),
        )

    def _send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def _pump(self) -> None:
        chunk = self.sock.recv(4096)
        if not chunk:
            raise NegotiationFailed("client closed during negotiation")
        self.codec.feed(chunk)

    def negotiate(self) -> NegotiationResult:
        self.sock.settimeout(self.timeout)

        # ── 1. Send our DOs and WILLs ──────────────────────────────
        # We DO: BINARY, EOR, TTYPE, NEW-ENVIRON (asking client to enable)
        # We WILL: BINARY, EOR (offering to enable on our side)
        for opt in (OPT_BINARY, OPT_EOR, OPT_TTYPE, OPT_NEW_ENVIRON):
            self._send(self.codec.send_command(DO, opt))
        for opt in (OPT_BINARY, OPT_EOR):
            self._send(self.codec.send_command(WILL, opt))

        # ── 2. Wait for client's WILLs/DOs ─────────────────────────
        # Required: WILL BINARY, WILL EOR, WILL TTYPE, DO BINARY, DO EOR
        # Optional: WILL NEW-ENVIRON (some clients refuse)
        required = {(WILL, OPT_BINARY), (WILL, OPT_EOR), (WILL, OPT_TTYPE),
                    (DO, OPT_BINARY), (DO, OPT_EOR)}
        new_environ_ok = False
        while required:
            self._pump()
            for c, o in list(self._cmds):
                self._cmds.remove((c, o))
                if (c, o) in required:
                    required.discard((c, o))
                    if c == WILL:
                        self.codec.note_remote_enabled(o)
                    elif c == DO:
                        self.codec.note_local_enabled(o)
                elif (c, o) == (WILL, OPT_NEW_ENVIRON):
                    new_environ_ok = True
                    self.codec.note_remote_enabled(OPT_NEW_ENVIRON)
                elif (c, o) == (WONT, OPT_NEW_ENVIRON):
                    pass  # fine, we'll skip DEVNAME
                elif c == WILL:
                    # Client offers something we don't want
                    self._send(self.codec.send_command(DONT, o))
                elif c == DO:
                    self._send(self.codec.send_command(WONT, o))

        # ── 3. NEW-ENVIRON: ask for DEVNAME ────────────────────────
        devname = None
        if new_environ_ok:
            # SB NEW-ENVIRON SEND USERVAR "DEVNAME" SE
            self._send(self.codec.send_subneg(OPT_NEW_ENVIRON,
                bytes([NE_SEND, NE_USERVAR]) + b"DEVNAME"))
            sb = self._wait_for_subneg(OPT_NEW_ENVIRON)
            devname = self._parse_new_environ(sb)
            log.info("client DEVNAME=%s", devname)

        # ── 4. TERMINAL-TYPE: ask, get answer ──────────────────────
        self._send(self.codec.send_subneg(OPT_TTYPE, bytes([TTYPE_SEND])))
        sb = self._wait_for_subneg(OPT_TTYPE)
        if not sb or sb[0] != TTYPE_IS:
            raise NegotiationFailed(f"expected TTYPE IS, got {sb!r}")
        term_type = sb[1:].decode("ascii")
        log.info("client terminal type: %s", term_type)

        return NegotiationResult(term_type=term_type, devname=devname,
                                 codec=self.codec)

    def _wait_for_subneg(self, want_opt: int) -> bytes:
        while True:
            for opt, data in self._subnegs:
                if opt == want_opt:
                    self._subnegs.remove((opt, data))
                    return data
            self._pump()

    @staticmethod
    def _parse_new_environ(sb: bytes) -> str | None:
        """Parse SB NEW-ENVIRON IS data. Returns DEVNAME value if present.

        Format: IS (USERVAR name VALUE val)*
        Names/values are not length-prefixed — they end at the next type byte.
        """
        if not sb or sb[0] != NE_IS:
            return None
        i = 1
        n = len(sb)
        current_name = None
        while i < n:
            t = sb[i]
            i += 1
            if t in (NE_VAR, NE_USERVAR):
                # Read name until next type byte
                start = i
                while i < n and sb[i] not in (NE_VAR, NE_USERVAR, NE_VALUE):
                    i += 1
                current_name = sb[start:i].decode("ascii", errors="replace")
            elif t == NE_VALUE:
                start = i
                while i < n and sb[i] not in (NE_VAR, NE_USERVAR, NE_VALUE):
                    i += 1
                val = sb[start:i].decode("ascii", errors="replace")
                if current_name == "DEVNAME":
                    return val
        return None
