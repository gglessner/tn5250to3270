import pytest
import textwrap
from tn5250to3270.config import Config, load_config


def test_load_minimal(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
      listen:
        host: 127.0.0.1
        port: 2323
      upstream:
        host: mainframe.local
        port: 23
    """))
    c = load_config(p)
    assert c.listen_host == "127.0.0.1"
    assert c.listen_port == 2323
    assert c.upstream_host == "mainframe.local"
    assert c.upstream_port == 23
    assert c.tls_enabled is False
    assert c.codepage == "cp037"


def test_load_full(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
      listen: {host: 0.0.0.0, port: 9999}
      upstream:
        host: zos.example.com
        port: 992
        tls: {enabled: true, verify: false, ca_bundle: /etc/ca.pem}
        connect_timeout: 5.0
      ebcdic: {codepage: cp500}
      geometry:
        IBM-3477-FC: [IBM-3278-5-E, 27, 132]
      logging: {level: DEBUG}
    """))
    c = load_config(p)
    assert c.tls_enabled is True
    assert c.tls_verify is False
    assert c.tls_ca_bundle == "/etc/ca.pem"
    assert c.codepage == "cp500"
    assert c.geometry.match("IBM-3477-FC").rows == 27
    assert c.log_level == "DEBUG"
