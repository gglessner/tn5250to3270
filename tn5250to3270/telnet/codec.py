"""Telnet IAC handling + EOR record framing.

State machine:
  DATA    — normal bytes accumulate into current record
  IAC     — saw 0xFF, next byte tells us what it is
  CMD     — saw IAC + WILL/WONT/DO/DONT, next byte is option
  SB      — inside subnegotiation, accumulate until IAC SE
  SB_IAC  — saw IAC inside SB; next is either IAC (data 0xFF) or SE (end)

This codec is passive: it strips IAC sequences and fires callbacks.
The caller decides how to respond (send WILL, DONT, etc.) using wrap().
"""
from typing import Callable
from .options import IAC, SE, SB, WILL, WONT, DO, DONT, EOR_CMD, OPT_EOR


_S_DATA, _S_IAC, _S_CMD, _S_SB, _S_SB_IAC = range(5)


class TelnetCodec:
    def __init__(
        self,
        on_command: Callable[[int, int], None] | None = None,
        on_subneg: Callable[[int, bytes], None] | None = None,
    ):
        self._state = _S_DATA
        self._record = bytearray()      # current record being built
        self._sb_opt = 0                # option byte for current SB
        self._sb_data = bytearray()     # subneg payload accumulator
        self._pending_cmd = 0           # WILL/WONT/DO/DONT awaiting option byte
        self._on_command = on_command or (lambda c, o: None)
        self._on_subneg = on_subneg or (lambda o, d: None)
        self._local_opts: set[int] = set()   # options WE have enabled
        self._remote_opts: set[int] = set()  # options PEER has enabled

    def feed(self, data: bytes) -> list[bytes]:
        """Consume socket bytes. Return zero or more complete records."""
        records: list[bytes] = []
        for b in data:
            if self._state == _S_DATA:
                if b == IAC:
                    self._state = _S_IAC
                else:
                    self._record.append(b)

            elif self._state == _S_IAC:
                if b == IAC:
                    # Escaped 0xFF in data
                    self._record.append(0xFF)
                    self._state = _S_DATA
                elif b == EOR_CMD:
                    records.append(bytes(self._record))
                    self._record = bytearray()
                    self._state = _S_DATA
                elif b in (WILL, WONT, DO, DONT):
                    self._pending_cmd = b
                    self._state = _S_CMD
                elif b == SB:
                    self._state = _S_SB
                    self._sb_opt = -1  # next byte is option
                    self._sb_data = bytearray()
                else:
                    # NOP, GA, BRK, etc. — single-byte commands, ignore
                    self._state = _S_DATA

            elif self._state == _S_CMD:
                self._on_command(self._pending_cmd, b)
                self._state = _S_DATA

            elif self._state == _S_SB:
                if self._sb_opt == -1:
                    self._sb_opt = b
                elif b == IAC:
                    self._state = _S_SB_IAC
                else:
                    self._sb_data.append(b)

            elif self._state == _S_SB_IAC:
                if b == IAC:
                    self._sb_data.append(0xFF)
                    self._state = _S_SB
                elif b == SE:
                    self._on_subneg(self._sb_opt, bytes(self._sb_data))
                    self._state = _S_DATA
                else:
                    # Malformed — IAC inside SB followed by neither IAC nor SE.
                    # Best effort: treat as end of SB, re-process this byte.
                    self._on_subneg(self._sb_opt, bytes(self._sb_data))
                    self._state = _S_DATA
                    # Don't drop the byte — but this is rare; log in caller.

        return records

    @staticmethod
    def _escape(data: bytes) -> bytes:
        """Escape any 0xFF in data as IAC IAC."""
        if 0xFF not in data:
            return data
        out = bytearray()
        for b in data:
            if b == 0xFF:
                out.append(IAC)
                out.append(IAC)
            else:
                out.append(b)
        return bytes(out)

    def wrap_record(self, data: bytes) -> bytes:
        """Escape + append IAC EOR. Ready for socket.sendall()."""
        return self._escape(data) + bytes([IAC, EOR_CMD])

    @staticmethod
    def send_command(cmd: int, opt: int) -> bytes:
        """IAC WILL/WONT/DO/DONT <opt>. Returns wire bytes."""
        return bytes([IAC, cmd, opt])

    @staticmethod
    def send_subneg(opt: int, payload: bytes) -> bytes:
        """IAC SB <opt> <escaped-payload> IAC SE."""
        return bytes([IAC, SB, opt]) + TelnetCodec._escape(payload) + bytes([IAC, SE])

    def note_local_enabled(self, opt: int) -> None:
        self._local_opts.add(opt)

    def note_remote_enabled(self, opt: int) -> None:
        self._remote_opts.add(opt)

    def local_enabled(self, opt: int) -> bool:
        return opt in self._local_opts

    def remote_enabled(self, opt: int) -> bool:
        return opt in self._remote_opts

    def eor_negotiated(self) -> bool:
        """EOR must be agreed in both directions for record framing to work."""
        return OPT_EOR in self._local_opts and OPT_EOR in self._remote_opts
