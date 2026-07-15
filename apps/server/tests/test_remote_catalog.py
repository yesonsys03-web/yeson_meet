from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.server.domain.video_captions import remote_catalog as rc


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    yield


def _payload(*models: dict) -> str:
    return json.dumps({"version": 1, "models": list(models)})


VALID = {"name": "large-v3-turbo", "repo_id": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
         "approx_bytes": 1_620_000_000, "label": "고품질·고속"}


def test_fetch_parses_valid_models(monkeypatch):
    monkeypatch.setattr(rc, "_http_get", lambda url: _payload(VALID))
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    out = rc.get_remote_models(force=True)
    assert [m.name for m in out] == ["large-v3-turbo"]
    assert out[0].repo_id == "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    assert out[0].approx_bytes == 1_620_000_000


def test_skips_malformed_entries_keeps_valid(monkeypatch):
    bad = [
        {"name": "no-repo", "approx_bytes": 10, "label": "x"},          # repo_id 없음
        {"name": "bad name!", "repo_id": "a/b", "approx_bytes": 10, "label": "x"},  # name 정규식
        {"name": "neg", "repo_id": "a/b", "approx_bytes": -1, "label": "x"},        # 음수
        {"name": "boolbytes", "repo_id": "a/b", "approx_bytes": True, "label": "x"},  # bool
        "not-a-dict",
    ]
    monkeypatch.setattr(rc, "_http_get", lambda url: _payload(VALID, *bad))
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    out = rc.get_remote_models(force=True)
    assert [m.name for m in out] == ["large-v3-turbo"]


def test_cache_hit_skips_network(monkeypatch):
    calls = {"n": 0}
    def fake_get(url):
        calls["n"] += 1
        return _payload(VALID)
    monkeypatch.setattr(rc, "_http_get", fake_get)
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    rc.get_remote_models(force=True)        # writes cache at t=1000
    assert calls["n"] == 1
    monkeypatch.setattr(rc, "_now", lambda: 1000.0 + 3600)  # < TTL(6h)
    rc.get_remote_models(force=False)       # cache fresh -> no network
    assert calls["n"] == 1


def test_ttl_expiry_refetches(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(rc, "_http_get", lambda url: (calls.__setitem__("n", calls["n"] + 1), _payload(VALID))[1])
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    rc.get_remote_models(force=True)
    monkeypatch.setattr(rc, "_now", lambda: 1000.0 + rc.CACHE_TTL_SECONDS + 1)
    rc.get_remote_models(force=False)
    assert calls["n"] == 2


def test_fetch_failure_falls_back_to_cache(monkeypatch):
    monkeypatch.setattr(rc, "_http_get", lambda url: _payload(VALID))
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    rc.get_remote_models(force=True)  # populate cache
    def boom(url):
        raise RuntimeError("network down")
    monkeypatch.setattr(rc, "_http_get", boom)
    monkeypatch.setattr(rc, "_now", lambda: 1000.0 + rc.CACHE_TTL_SECONDS + 1)  # stale -> tries network
    out = rc.get_remote_models(force=True)
    assert [m.name for m in out] == ["large-v3-turbo"]  # served from cache


def test_no_cache_no_network_returns_empty(monkeypatch):
    def boom(url):
        raise RuntimeError("network down")
    monkeypatch.setattr(rc, "_http_get", boom)
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    assert rc.get_remote_models(force=True) == []


def test_non_https_url_ignored(monkeypatch):
    monkeypatch.setenv(rc.CATALOG_URL_ENV, "http://evil.example/catalog.json")
    called = {"n": 0}
    monkeypatch.setattr(rc, "_http_get", lambda url: called.__setitem__("n", 1) or _payload(VALID))
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    assert rc.get_remote_models(force=True) == []
    assert called["n"] == 0


def test_cached_models_reads_cache_without_network(monkeypatch):
    monkeypatch.setattr(rc, "_http_get", lambda url: _payload(VALID))
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    rc.get_remote_models(force=True)  # populate cache
    calls = {"n": 0}
    monkeypatch.setattr(rc, "_http_get", lambda url: calls.__setitem__("n", 1) or _payload(VALID))
    out = rc.cached_models()
    assert [m.name for m in out] == ["large-v3-turbo"]
    assert calls["n"] == 0  # never hits network


def test_cached_models_empty_when_no_cache(monkeypatch):
    assert rc.cached_models() == []


def test_dot_names_are_rejected(monkeypatch):
    dotted = [
        {"name": ".", "repo_id": "a/b", "approx_bytes": 10, "label": "x"},
        {"name": "..", "repo_id": "a/b", "approx_bytes": 10, "label": "x"},
    ]
    monkeypatch.setattr(rc, "_http_get", lambda url: _payload(VALID, *dotted))
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    out = rc.get_remote_models(force=True)
    assert [m.name for m in out] == ["large-v3-turbo"]  # dotted entries skipped
