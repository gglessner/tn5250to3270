"""TN3270/TN3270E telnet negotiation. RFC 1576 + RFC 2355.

Sequence (TN3270E):
  -> WILL TN3270E
  <- DO TN3270E             (or WONT/DONT -> fall back)
  -> SB TN3270E DEVICE-TYPE REQUEST <type> [CONNECT <lu>] SE
  <- SB TN3270E DEVICE-TYPE IS <type> CONNECT <lu> SE
  -> SB TN3270E FUNCTIONS REQUEST <list> SE
  <- SB TN3270E FUNCTIONS IS <list> SE

Fallback (basic TN3270):
  <-> DO/WILL BINARY, DO/WILL EOR
  <-  DO TERMINAL-TYPE
  ->  WILL TERMINAL-TYPE
  <-  SB TERMINAL-TYPE SEND SE
  ->  SB TERMINAL-TYPE IS <type> SE
"""
import logging
from dataclasses import dataclass, field
from ..telnet.codec import TelnetCodec
from ..telnet.options import (
    IAC, WILL, WONT, DO, DONT, SB, SE,
    OPT_BINARY, OPT_EOR, OPT_TTYPE, OPT_TN3270E,
    TTYPE_IS, TTYPE_SEND,
)

log = logging.getLogger(__name__)

# TN3270E subnegotiation codes (RFC 2355 sec 3)
E_ASSOCIATE   = 0x00
E_CONNECT     = 0x01
E_DEVICE_TYPE = 0x02
E_FUNCTIONS   = 0x03
E_IS          = 0x04
E_REASON      = 0x05
E_REJECT      = 0x06
E_REQUEST     = 0x07
E_SEND        = 0x08

# Function codes
FN_BIND_IMAGE   = 0x00
FN_DATA_STREAM  = 0x01
FN_RESPONSES    = 0x02
FN_SCS_CTL      = 0x03
FN_SYSREQ       = 0x04

_FN_NAMES = {
    FN_BIND_IMAGE: "BIND-IMAGE", FN_DATA_STREAM: "DATA-STREAM-CTL",
    FN_RESPONSES: "RESPONSES", FN_SCS_CTL: "SCS-CTL-CODES", FN_SYSREQ: "SYSREQ",
}


class NegotiationFailed(Exception):
    pass


@dataclass
class NegotiationResult:
    e_mode: bool
    device_type: str
    lu_name: str | None = None
    functions: set[str] = field(default_factory=set)
    codec: TelnetCodec | None = None
    pending_records: list[bytes] = field(default_factory=list)
    """Records that arrived DURING negotiation (host sent screen in same
    TCP segment as final WILL/DO). Session must process these BEFORE
    entering its recv loop, or the first screen is lost."""


class TN3270Negotiator:
    def __init__(self, sock, device_type: str, lu_name: str | None, timeout: float):
        self.sock = sock
        self.device_type = device_type
        self.lu_name = lu_name
        self.timeout = timeout
        self._cmds: list[tuple[int, int]] = []
        self._subnegs: list[tuple[int, bytes]] = []
        self._pending_records: list[bytes] = []
        self.codec = TelnetCodec(
            on_command=lambda c, o: self._cmds.append((c, o)),
            on_subneg=lambda o, d: self._subnegs.append((o, d)),
        )

    def _send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def _pump(self) -> None:
        """Read once from socket, feed codec. Raises on EOF.

        CRITICAL: codec.feed() returns complete records (between EOR marks).
        If the host sends its first screen in the same segment as the final
        negotiation bytes, those records appear here. We MUST keep them —
        the session's reader loop won't see them (recv() returns new bytes,
        not what's already been fed)."""
        chunk = self.sock.recv(4096)
        if not chunk:
            raise NegotiationFailed("connection closed during negotiation")
        self._pending_records.extend(self.codec.feed(chunk))

    def _wait_for_cmd(self, want_cmd: int, want_opt: int) -> bool:
        """Pump until we see (want_cmd, want_opt). Returns True if seen,
        False if a refusal (WONT/DONT) for the same option arrives.

        Hosts vary in which negative they send: a strict telnet reply to
        WILL X is DONT X, but many hosts send WONT X. Accept either.
        """
        while True:
            for c, o in self._cmds:
                if o == want_opt:
                    if c == want_cmd:
                        self._cmds.remove((c, o))
                        return True
                    if c in (WONT, DONT):
                        self._cmds.remove((c, o))
                        return False
            self._pump()

    def _wait_for_subneg(self, want_opt: int) -> bytes:
        while True:
            for opt, data in self._subnegs:
                if opt == want_opt:
                    self._subnegs.remove((opt, data))
                    return data
            self._pump()

    # -- Public ------------------------------------------------------

    def negotiate(self) -> NegotiationResult:
        self.sock.settimeout(self.timeout)
        # Try TN3270E first
        self._send(self.codec.send_command(WILL, OPT_TN3270E))
        if self._wait_for_cmd(DO, OPT_TN3270E):
            log.info("host accepted TN3270E")
            self.codec.note_local_enabled(OPT_TN3270E)
            self.codec.note_remote_enabled(OPT_TN3270E)
            return self._negotiate_e()
        else:
            log.info("host refused TN3270E, falling back to basic TN3270")
            return self._negotiate_basic()

    def _negotiate_e(self) -> NegotiationResult:
        # DEVICE-TYPE REQUEST
        req = bytearray([E_DEVICE_TYPE, E_REQUEST])
        req += self.device_type.encode("ascii")
        if self.lu_name:
            req.append(E_CONNECT)
            req += self.lu_name.encode("ascii")
        self._send(self.codec.send_subneg(OPT_TN3270E, bytes(req)))

        # Wait for DEVICE-TYPE IS
        sb = self._wait_for_subneg(OPT_TN3270E)
        if len(sb) < 2 or sb[0] != E_DEVICE_TYPE or sb[1] != E_IS:
            if len(sb) >= 2 and sb[1] == E_REJECT:
                raise NegotiationFailed(f"host rejected device type: {sb[2:]!r}")
            raise NegotiationFailed(f"unexpected DEVICE-TYPE response: {sb!r}")
        # Parse: [E_DEVICE_TYPE, E_IS, <type>..., E_CONNECT, <lu>...]
        rest = sb[2:]
        if E_CONNECT in rest:
            idx = rest.index(E_CONNECT)
            assigned_type = rest[:idx].decode("ascii")
            lu = rest[idx + 1:].decode("ascii")
        else:
            assigned_type = rest.decode("ascii")
            lu = None
        log.info("host assigned device-type=%s lu=%s", assigned_type, lu)

        # FUNCTIONS REQUEST -- ask for everything we can use
        want = [FN_BIND_IMAGE, FN_RESPONSES, FN_SYSREQ]
        self._send(self.codec.send_subneg(OPT_TN3270E,
            bytes([E_FUNCTIONS, E_REQUEST]) + bytes(want)))

        # Wait for FUNCTIONS IS
        sb = self._wait_for_subneg(OPT_TN3270E)
        if len(sb) < 2 or sb[0] != E_FUNCTIONS or sb[1] != E_IS:
            raise NegotiationFailed(f"unexpected FUNCTIONS response: {sb!r}")
        granted = {_FN_NAMES.get(b, f"FN_{b:02x}") for b in sb[2:]}
        log.info("host granted functions: %s", granted)

        # Mark BINARY+EOR as implicitly enabled (TN3270E implies them)
        for opt in (OPT_BINARY, OPT_EOR):
            self.codec.note_local_enabled(opt)
            self.codec.note_remote_enabled(opt)

        return NegotiationResult(e_mode=True, device_type=assigned_type,
                                 lu_name=lu, functions=granted,
                                 codec=self.codec,
                                 pending_records=self._pending_records)

    def _negotiate_basic(self) -> NegotiationResult:
        """Basic TN3270 (RFC 1576): BINARY + EOR + TTYPE.

        Real hosts vary wildly in ordering. Hercules-style hosts refuse
        BINARY/EOR until they've seen a 3270 terminal type, then re-offer.
        Others send everything in one burst. The robust approach: process
        whatever arrives in whatever order, settle TTYPE first, then push
        BINARY/EOR through after.
        """
        # We may already have DO TTYPE in self._cmds (host sent it
        # alongside its TN3270E refusal). Acknowledge.
        self._send(self.codec.send_command(WILL, OPT_TTYPE))

        def drain_cmds():
            """Process every buffered command. Accept BINARY/EOR/TTYPE,
            refuse anything else, discard DONT/WONT (host may re-offer)."""
            for c, o in list(self._cmds):
                self._cmds.remove((c, o))
                if c == DO and o in (OPT_BINARY, OPT_EOR, OPT_TTYPE):
                    self.codec.note_local_enabled(o)
                elif c == WILL and o in (OPT_BINARY, OPT_EOR):
                    self.codec.note_remote_enabled(o)
                elif c == DO:
                    self._send(self.codec.send_command(WONT, o))
                elif c == WILL:
                    self._send(self.codec.send_command(DONT, o))
                # DONT/WONT: discard. Don't reply (RFC 854: never loop),
                # don't treat as permanent — host may re-offer post-TTYPE.

        # ── Stage 1: get TTYPE settled ─────────────────────────────
        ttype_sent = False
        while not ttype_sent:
            drain_cmds()
            for opt, data in list(self._subnegs):
                if opt == OPT_TTYPE and data and data[0] == TTYPE_SEND:
                    self._subnegs.remove((opt, data))
                    self._send(self.codec.send_subneg(
                        OPT_TTYPE,
                        bytes([TTYPE_IS]) + self.device_type.encode("ascii")
                    ))
                    ttype_sent = True
                    log.debug("sent TTYPE IS %s", self.device_type)
                    break
            if not ttype_sent:
                self._pump()

        # ── Stage 2: push BINARY/EOR through (host should accept now) ──
        # Send WILL/DO for whatever isn't already enabled. Hosts that
        # already accepted will ignore (RFC: don't re-confirm). Hosts that
        # refused earlier will now accept.
        for opt in (OPT_BINARY, OPT_EOR):
            if not self.codec.local_enabled(opt):
                self._send(self.codec.send_command(WILL, opt))
            if not self.codec.remote_enabled(opt):
                self._send(self.codec.send_command(DO, opt))

        # Collect responses. Stop when EOR is confirmed both ways (that's
        # what record framing needs) OR when the codec already has a
        # complete record buffered (host sent the screen — we're done).
        while not self.codec.eor_negotiated():
            drain_cmds()
            if self.codec.eor_negotiated():
                break
            self._pump()
            # If a record arrived, host clearly thinks we're negotiated —
            # treat EOR as implicitly on (it sent IAC EOR, so it works).
            if self.codec._record or any(
                opt == OPT_TTYPE for opt, _ in self._subnegs
            ):
                # Either data is arriving, or host wants TTYPE again
                # (some hosts re-probe). Either way, proceed.
                break

        if not self.codec.eor_negotiated():
            log.warning("EOR not formally negotiated — proceeding anyway, "
                        "host may use it implicitly")

        return NegotiationResult(e_mode=False, device_type=self.device_type,
                                 codec=self.codec,
                                 pending_records=self._pending_records)
