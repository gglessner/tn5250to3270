import logging
import socket
import threading
from .config import Config
from .session import Session

log = logging.getLogger(__name__)


def serve(config: Config) -> None:
    """Accept loop. Each connection gets a thread running Session.run()."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((config.listen_host, config.listen_port))
    srv.listen(64)
    log.info("listening on %s:%d → %s:%d (TLS=%s)",
             config.listen_host, config.listen_port,
             config.upstream_host, config.upstream_port, config.tls_enabled)
    try:
        while True:
            client, addr = srv.accept()
            log.info("connection from %s:%d", addr[0], addr[1])
            t = threading.Thread(
                target=Session(client, config).run,
                name=f"session-{addr[0]}:{addr[1]}",
                daemon=True,
            )
            t.start()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        srv.close()
