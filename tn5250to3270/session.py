"""Session orchestrator. The only module that knows about both protocols.

Lifecycle:
  1. _do_negotiation()  — single-threaded, sequential. Establishes both sides.
  2. _spawn_readers()   — start two threads.
  3. Threads run until either socket closes.
  4. _shutdown()        — close peer, join threads.
"""
import logging
import socket
import ssl
import threading
from .config import Config
from .geometry import UnknownTerminalType
from .screen.model import VirtualScreen
from .screen import ops
from .tn5250.negotiator import TN5250Negotiator, NegotiationResult as R5250
from .tn5250 import emitter as e5250, parser as p5250
from .tn5250.gds import unpack_gds
from .tn5250.constants import AID_MAP_5250_TO_3270
from .tn3270.negotiator import TN3270Negotiator, NegotiationResult as R3270
from .tn3270 import parser as p3270, emitter as e3270
from .tn3270.tn3270e import (
    unpack_header, pack_header, build_positive_response, EHeader,
    DT_3270_DATA, DT_BIND_IMAGE, DT_SCS_DATA, DT_REQUEST, RQ_NONE,
)
from .tn3270.constants import AID_PA1
from .tn3270.query_reply import is_read_partition_query, build_query_reply

log = logging.getLogger(__name__)


class Session:
    def __init__(self, client_sock: socket.socket, config: Config):
        self.client_sock = client_sock
        self.config = config
        self.host_sock: socket.socket | None = None
        self.screen: VirtualScreen | None = None
        self.lock = threading.Lock()
        # Set during negotiation:
        self.codec_5250 = None  # TelnetCodec, client side
        self.codec_3270 = None  # TelnetCodec, host side
        self.e_mode: bool = False
        self.lu_name: str | None = None
        self.mode14: bool = False  # 14-bit addressing if screen > 4096
        self._functions: set[str] = set()  # TN3270E functions granted (for SysReq)
        # 3270 AID register: holds the last operator AID until the next
        # host write resets it. RM replies use THIS, not AID_NO. Per
        # GA23-0059, AID_NO is only for RM with no preceding operator
        # action — MVS rejects 0x60 after Enter as "Invalid attention".
        from .tn3270.constants import AID_NO
        self._aid_register: int = AID_NO
        # Track whether the HOST's screen is unformatted (no SF orders).
        # We synthesize a 5250 field for typeability, but the 3270 inbound
        # to the host must use unformatted format (no SBA — just raw data
        # after cursor). Wire trace confirms: SBA on unformatted → ABEND.
        self._host_unformatted: bool = False
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ── Public entry point ──────────────────────────────────────────

    def run(self) -> None:
        """Run negotiation, then reader threads. Blocks until both close.

        Catches all exceptions — listener spawns this in a thread and
        doesn't want it to crash."""
        try:
            self._do_negotiation()
            self._spawn_readers()
            for t in self._threads:
                t.join()
        except UnknownTerminalType as e:
            log.error("[%s] %s", self._peer(), e)
        except Exception as e:
            log.exception("[%s] session crashed: %s", self._peer(), e)
        finally:
            self._shutdown()

    def _spawn_readers(self) -> None:
        t1 = threading.Thread(target=self._host_reader_loop,
                              name=f"host-reader-{self._peer()}", daemon=True)
        t2 = threading.Thread(target=self._client_reader_loop,
                              name=f"client-reader-{self._peer()}", daemon=True)
        self._threads = [t1, t2]
        t1.start()
        t2.start()

    def _shutdown(self) -> None:
        """Close both sockets. Idempotent — guarded by _stop event so
        both reader threads' finally blocks can call it safely."""
        if self._stop.is_set():
            return
        self._stop.set()
        for s in (self.client_sock, self.host_sock):
            if s is not None:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    s.close()
                except OSError:
                    pass

    # ── Phase 1: negotiation ────────────────────────────────────────

    def _do_negotiation(self) -> None:
        # 1. TN5250 negotiation with client
        r5 = self._negotiate_5250()
        self.codec_5250 = r5.codec
        log.info("[%s] 5250 client: term=%s devname=%s",
                 self._peer(), r5.term_type, r5.devname)

        # 2. Geometry match (raises UnknownTerminalType if no match —
        #    BEFORE we dial upstream)
        geo = self.config.geometry.match(r5.term_type)
        self.screen = VirtualScreen(rows=geo.rows, cols=geo.cols)
        self.mode14 = (geo.rows * geo.cols) > 4096
        log.info("[%s] geometry: %dx%d → %s, mode14=%s",
                 self._peer(), geo.rows, geo.cols, geo.tn3270_type, self.mode14)

        # 3. Connect upstream
        self.host_sock = self._connect_upstream()

        # 4. TN3270 negotiation with host (pass DEVNAME through as LU name)
        r3 = self._negotiate_3270(self.host_sock, geo.tn3270_type, r5.devname)
        self.codec_3270 = r3.codec
        self.e_mode = r3.e_mode
        self.lu_name = r3.lu_name
        self._functions = r3.functions
        log.info("[%s] 3270 host: e_mode=%s lu=%s functions=%s",
                 self._peer(), r3.e_mode, r3.lu_name, r3.functions)

        # 5. Reset socket timeouts — negotiators set short timeouts that
        #    must NOT carry into reader threads, or recv() raises after
        #    10s of idle and kills the session.
        self.client_sock.settimeout(None)
        self.host_sock.settimeout(None)

        # 6. Process any records that arrived DURING negotiation. Hosts
        #    often send the first screen in the same segment as their final
        #    WILL/DO — if we don't drain these here, the screen is lost
        #    (reader loop's recv() won't see bytes already fed to codec).
        if r3.pending_records:
            log.info("[%s] %d record(s) arrived during negotiation, "
                     "processing now", self._peer(), len(r3.pending_records))
            for rec in r3.pending_records:
                self._handle_host_record(rec)

    # ── Pluggable for tests ─────────────────────────────────────────

    def _negotiate_5250(self) -> R5250:
        return TN5250Negotiator(self.client_sock,
                                timeout=self.config.negotiate_timeout).negotiate()

    def _negotiate_3270(self, sock, device_type: str, lu_name: str | None) -> R3270:
        return TN3270Negotiator(sock, device_type=device_type,
                                lu_name=lu_name,
                                timeout=self.config.negotiate_timeout).negotiate()

    def _connect_upstream(self) -> socket.socket:
        s = socket.create_connection(
            (self.config.upstream_host, self.config.upstream_port),
            timeout=self.config.connect_timeout,
        )
        if self.config.tls_enabled:
            ctx = ssl.create_default_context()
            if not self.config.tls_verify:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            if self.config.tls_ca_bundle:
                ctx.load_verify_locations(self.config.tls_ca_bundle)
            s = ctx.wrap_socket(s, server_hostname=self.config.upstream_host)
        s.settimeout(None)  # blocking for runtime
        return s

    def _peer(self) -> str:
        try:
            return f"{self.client_sock.getpeername()[0]}"
        except Exception:
            return "?"

    # ── Phase 2: host→client reader ─────────────────────────────────

    def _host_reader_loop(self) -> None:
        """Thread: read from host, parse 3270, update screen, emit 5250."""
        try:
            while not self._stop.is_set():
                chunk = self.host_sock.recv(8192)
                if not chunk:
                    log.info("[%s] host closed connection", self._peer())
                    break
                records = self.codec_3270.feed(chunk)
                for rec in records:
                    self._handle_host_record(rec)
        except OSError as e:
            log.info("[%s] host socket error: %s", self._peer(), e)
        finally:
            self._shutdown()

    def _handle_host_record(self, record: bytes) -> None:
        # ── TN3270E: strip header, handle non-data types ───────────
        if self.e_mode:
            hdr = unpack_header(record)
            payload = record[5:]
            if hdr.data_type == DT_BIND_IMAGE:
                log.debug("[%s] BIND-IMAGE received (ignored)", self._peer())
                return
            if hdr.data_type == DT_SCS_DATA:
                log.warning("[%s] SCS printer data dropped: %d bytes",
                            self._peer(), len(payload))
                return
            if hdr.data_type != DT_3270_DATA:
                log.warning("[%s] unhandled TN3270E data-type 0x%02x",
                            self._peer(), hdr.data_type)
                return
            need_response = hdr.request_flag != RQ_NONE
            seq = hdr.seq
        else:
            payload = record
            need_response = False
            seq = 0

        # ── Parse 3270 stream ───────────────────────────────────────
        result = p3270.parse(payload)
        log.debug("[%s] host record: cmd=0x%02x ops=%d read=%s wsf=%s "
                  "first=%s", self._peer(), result.command, len(result.ops),
                  result.is_read, result.is_wsf, payload[:8].hex())
        for w in result.warnings:
            log.warning("[%s] 3270 parse: %s", self._peer(), w)

        # ── Intercept: WSF Query → answer ourselves ─────────────────
        if result.is_wsf:
            if is_read_partition_query(result.wsf_data):
                log.info("[%s] answering Read Partition Query", self._peer())
                reply = build_query_reply(self.screen.rows, self.screen.cols,
                                          color=True)
                self._send_to_host(reply)
            else:
                log.warning("[%s] dropped unhandled WSF: %s",
                            self._peer(), result.wsf_data[:16].hex())
            return

        # ── Intercept: Read Buffer / Read Modified ─────────────────
        if result.is_read:
            from .tn3270.constants import CMD_RB, CMD_RB_LOCAL, AID_NO
            cmd = result.command
            is_rb = cmd in (CMD_RB, CMD_RB_LOCAL)
            log.info("[%s] answering Read command 0x%02x (%s)",
                     self._peer(), cmd, "RB" if is_rb else "RM")
            with self.lock:
                if is_rb:
                    # Read Buffer: full dump with SF orders, nulls included
                    reply = e3270.build_read_buffer_reply(
                        self.screen, self.mode14)
                else:
                    # Read Modified / RMA: AID + cursor + data.
                    # Use the AID REGISTER (last operator AID), not
                    # AID_NO. Real 3270 terminals hold the AID until
                    # the next host write resets it. Use unformatted
                    # format if the host's screen has no fields.
                    modified = self.screen.get_modified_fields()
                    reply = e3270.build_inbound(
                        self._aid_register, self.screen.cursor,
                        modified, self.mode14,
                        unformatted=self._host_unformatted)
            self._send_to_host(reply)
            return

        # ── Normal write: update screen, emit 5250 ─────────────────
        # Lock covers ONLY screen mutation + reading screen for emission.
        # sendall() happens OUTSIDE the lock — slow client doesn't stall
        # the host reader's peer thread.
        erased = any(isinstance(o, ops.EraseAll) for o in result.ops)
        wcc = next((o for o in result.ops if isinstance(o, ops.WccFlags)), None)
        reset_mdt = wcc.reset_mdt if wcc else False

        with self.lock:
            for op in result.ops:
                self.screen.apply(op)
            # Host write resets the AID register (real 3270 behavior).
            # Next RM with no intervening operator action gets AID_NO.
            from .tn3270.constants import AID_NO
            self._aid_register = AID_NO
            # ── Unformatted screen handling ────────────────────────
            # Track host's unformatted state SEPARATELY from our screen
            # model. We add a synthetic field for the 5250 side, but the
            # 3270 inbound must follow unformatted rules (no SBA orders).
            # An EW with no DefineField in its ops = unformatted. A W
            # (no erase) inherits the previous state.
            if erased:
                has_field_def = any(isinstance(o, ops.DefineField)
                                    for o in result.ops)
                self._host_unformatted = not has_field_def
            # Synthesize a 5250 input field IN THE MODEL so tn5250j can
            # type and set_field_data finds somewhere to put it.
            if not self.screen.fields and not self.screen.keyboard_locked:
                from .screen.field import FieldAttrs
                self.screen.apply(ops.DefineField(
                    pos=0, attrs=FieldAttrs(protected=False)))
                log.debug("[%s] unformatted+unlocked → synthetic field "
                          "(host_unformatted=%s)",
                          self._peer(), self._host_unformatted)
            wtd = e5250.render_wtd(self.screen, erased=erased,
                                   reset_mdt=reset_mdt)
            # Reset alarm flag (one-shot) — under lock since it's screen state
            self.screen.alarm = False

        self.client_sock.sendall(self.codec_5250.wrap_record(wtd))

        # ── TN3270E response if requested ──────────────────────────
        if need_response:
            # build_positive_response already includes its own E-header
            # (DT_RESPONSE), so bypass _send_to_host which would prepend
            # a DT_3270_DATA header.
            self.host_sock.sendall(
                self.codec_3270.wrap_record(build_positive_response(seq)))

    def _send_to_host(self, data: bytes) -> None:
        """Wrap 3270-DATA payload in E-header (if e_mode) + telnet codec."""
        if self.e_mode:
            hdr = pack_header(EHeader(data_type=DT_3270_DATA, request_flag=0,
                                       response_flag=0, seq=0))
            data = hdr + data
        self.host_sock.sendall(self.codec_3270.wrap_record(data))

    # ── Phase 2: client→host reader ─────────────────────────────────

    def _client_reader_loop(self) -> None:
        """Thread: read from client, parse 5250, update screen, emit 3270."""
        try:
            while not self._stop.is_set():
                chunk = self.client_sock.recv(8192)
                if not chunk:
                    log.info("[%s] client closed connection", self._peer())
                    break
                records = self.codec_5250.feed(chunk)
                for rec in records:
                    self._handle_client_record(rec)
        except OSError as e:
            log.info("[%s] client socket error: %s", self._peer(), e)
        finally:
            self._shutdown()

    def _handle_client_record(self, record: bytes) -> None:
        # ── ATN/SysReq: GDS atn flag, NOT an AID byte (spec §8) ────
        # Check this BEFORE parse_inbound — ATN records may have no
        # cursor/AID payload, parse_inbound would raise.
        try:
            h, _ = unpack_gds(record)
        except ValueError as e:
            log.warning("[%s] malformed GDS: %s — record: %s",
                        self._peer(), e, record[:32].hex())
            return
        if h.atn or h.srq:
            # ATN (Attention) and SRQ (System Request, e.g. Esc key in
            # tn5250j) both route to the same place: TN3270E SYSREQ if
            # available, else PA1. The 5250 distinction doesn't survive
            # translation — 3270 only has one "interrupt" channel.
            log.info("[%s] %s flag → SysReq/PA1",
                     self._peer(), "ATN" if h.atn else "SRQ")
            self._handle_atn()
            return
        if h.trq or h.hlp:
            # Test Request / Help flag — no clean 3270 equivalent. Drop.
            log.warning("[%s] dropped %s flag (no 3270 mapping)",
                        self._peer(), "TRQ" if h.trq else "HLP")
            return

        # ── Parse normal inbound ───────────────────────────────────
        try:
            r = p5250.parse_inbound(record)
        except ValueError as e:
            log.warning("[%s] 5250 parse error: %s — record: %s",
                        self._peer(), e, record[:32].hex())
            return

        # ── Translate AID ──────────────────────────────────────────
        aid_3270 = AID_MAP_5250_TO_3270.get(r.aid)
        if aid_3270 is None:
            log.warning("[%s] dropped unmappable 5250 AID 0x%02x",
                        self._peer(), r.aid)
            return

        # ── Apply client input to screen, build 3270 inbound ───────
        # Lock covers ONLY screen mutation + reading modified fields.
        with self.lock:
            # 5250 row/col are 1-based; convert to 0-based linear
            for row, col, data in r.fields:
                pos = self.screen.from_rowcol(row - 1, col - 1)
                self.screen.set_field_data(pos, data)
            self.screen.cursor = self.screen.from_rowcol(
                r.cursor_row - 1, r.cursor_col - 1)
            modified = self.screen.get_modified_fields()
            cursor = self.screen.cursor
            # Lock keyboard — host will unlock on next WTD
            self.screen.keyboard_locked = True

        # Latch the AID register — RM replies will re-send this AID
        # until the next host write resets it.
        self._aid_register = aid_3270
        inbound = e3270.build_inbound(aid_3270, cursor, modified,
                                       self.mode14,
                                       unformatted=self._host_unformatted)
        log.debug("[%s] → host: aid=0x%02x cursor=%d unfmt=%s data=%s",
                  self._peer(), aid_3270, cursor, self._host_unformatted,
                  inbound.hex())
        self._send_to_host(inbound)

    def _handle_atn(self) -> None:
        """Spec §8: 5250 ATN flag → SysReq routing.

        TN3270E with SYSREQ function: send REQUEST data-type record.
        Otherwise (basic TN3270 or no SYSREQ granted): send PA1.
        """
        log.info("[%s] ATN pressed", self._peer())
        if self.e_mode and "SYSREQ" in self._functions:
            # TN3270E REQUEST record. RFC 2355 §3.5.1: DT_REQUEST,
            # zero seq, zero-length data. Has its own header so bypass
            # _send_to_host (which would add DT_3270_DATA on top).
            req = pack_header(EHeader(data_type=DT_REQUEST, request_flag=0,
                                       response_flag=0, seq=0))
            self.host_sock.sendall(self.codec_3270.wrap_record(req))
        else:
            # Fall back to PA1. PA1 is a SHORT_AID — sends AID + cursor
            # only, no field data. Read cursor under lock for consistency.
            with self.lock:
                cursor = self.screen.cursor
            inbound = e3270.build_inbound(AID_PA1, cursor, [], self.mode14)
            self._send_to_host(inbound)
