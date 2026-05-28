"""TLS bootstrap: the sidecar trusts the OS cert store (Caddy private CA)."""
from __future__ import annotations


def test_install_os_trust_store_injects(monkeypatch):
    import truststore

    calls = []
    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: calls.append(True))

    from apps.client_sidecar.main import _install_os_trust_store
    _install_os_trust_store()

    assert calls == [True]
