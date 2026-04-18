import pathlib
from dataclasses import dataclass, field

import yaml

from .geometry import GeometryMap, GeometryEntry


@dataclass(slots=True)
class Config:
    listen_host: str
    listen_port: int
    upstream_host: str
    upstream_port: int
    tls_enabled: bool = False
    tls_verify: bool = True
    tls_ca_bundle: str | None = None
    connect_timeout: float = 10.0
    negotiate_timeout: float = 10.0
    codepage: str = "cp037"
    geometry: GeometryMap = field(default_factory=GeometryMap.default)
    log_level: str = "INFO"
    log_format: str = "text"
    unmappable_log_level: str = "WARNING"


def load_config(path: pathlib.Path | str) -> Config:
    raw = yaml.safe_load(pathlib.Path(path).read_text())
    listen = raw["listen"]
    upstream = raw["upstream"]
    tls = upstream.get("tls", {})
    ebcdic = raw.get("ebcdic", {})
    logging_cfg = raw.get("logging", {})

    geo_raw = raw.get("geometry")
    if geo_raw:
        geo = GeometryMap({
            k: GeometryEntry(v[0], int(v[1]), int(v[2]))
            for k, v in geo_raw.items()
        })
    else:
        geo = GeometryMap.default()

    return Config(
        listen_host=listen["host"],
        listen_port=int(listen["port"]),
        upstream_host=upstream["host"],
        upstream_port=int(upstream["port"]),
        tls_enabled=bool(tls.get("enabled", False)),
        tls_verify=bool(tls.get("verify", True)),
        tls_ca_bundle=tls.get("ca_bundle"),
        connect_timeout=float(upstream.get("connect_timeout", 10.0)),
        negotiate_timeout=float(upstream.get("negotiate_timeout", 10.0)),
        codepage=ebcdic.get("codepage", "cp037"),
        geometry=geo,
        log_level=logging_cfg.get("level", "INFO"),
        log_format=logging_cfg.get("format", "text"),
        unmappable_log_level=logging_cfg.get("unmappable_level", "WARNING"),
    )
