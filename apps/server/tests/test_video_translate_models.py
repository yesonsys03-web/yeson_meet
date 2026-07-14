from __future__ import annotations

import httpx
import pytest

from apps.server.api.v1 import translate_models as router
from apps.server.domain.video_captions import translate_models as tm
from apps.server.domain.video_captions import translate_ollama as to


@pytest.fixture(autouse=True)
def _reset_state():
    tm._downloading.clear()
    tm._progress.clear()
    to._avail_cache["at"] = -1e9
    to._avail_cache["models"] = frozenset()
    yield
    tm._downloading.clear()
    tm._progress.clear()


class FakeResp:
    def __init__(self, payload=None, status_code=200, lines=None):
        self._payload = payload or {}
        self.status_code = status_code
        self._lines = lines or []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]

    def json(self):
        return self._payload

    def iter_lines(self):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── runtime() ───────────────────────────────────────────────────────────────
def test_runtime_silicon(monkeypatch):
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: True)
    assert tm.runtime() == "mlx"


def test_runtime_other(monkeypatch):
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: False)
    assert tm.runtime() == "ollama"


# ── list_models ─────────────────────────────────────────────────────────────
def test_list_models_ollama(monkeypatch):
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: False)
    monkeypatch.setattr(to, "ollama_running", lambda: True)
    monkeypatch.setattr(to, "ollama_installed", lambda: True)
    monkeypatch.setattr(to, "qwen_ollama_available", lambda tag: tag == "qwen3.5:9b")
    out = tm.list_models()
    assert out["runtime"] == "ollama"
    assert out["ollama_running"] is True
    by = {m["name"]: m for m in out["models"]}
    assert by["qwen"]["downloaded"] is True
    assert by["qwen_lite"]["downloaded"] is False
    assert by["qwen"]["approx_bytes"] == 6_600_000_000
    assert all(m["downloadable"] for m in out["models"])
    assert "MLX" not in by["qwen"]["label"]


def test_list_models_ollama_down_blocks_download(monkeypatch):
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: False)
    monkeypatch.setattr(to, "ollama_running", lambda: False)
    monkeypatch.setattr(to, "ollama_installed", lambda: False)
    monkeypatch.setattr(to, "qwen_ollama_available", lambda tag: False)
    out = tm.list_models()
    assert out["ollama_running"] is False
    assert all(not m["downloadable"] for m in out["models"])


def test_list_models_mlx(monkeypatch):
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: True)
    monkeypatch.setattr(tm, "mlx_model_installed", lambda repo: "9B-4bit" in repo)
    out = tm.list_models()
    assert out["runtime"] == "mlx"
    by = {m["name"]: m for m in out["models"]}
    assert by["qwen"]["downloaded"] is True       # Qwen3.5-9B-4bit
    assert by["qwen"]["approx_bytes"] == 5_000_000_000
    assert all(m["downloadable"] for m in out["models"])  # mlx always downloadable


# ── download_model ──────────────────────────────────────────────────────────
def test_download_ollama_calls_pull(monkeypatch):
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: False)
    called = {}
    monkeypatch.setattr(to, "qwen_ollama_model", lambda name: "qwen3.5:9b")
    monkeypatch.setattr(to, "pull_model", lambda tag, on_progress=None: called.setdefault("tag", tag))
    tm.download_model("qwen")
    assert called["tag"] == "qwen3.5:9b"
    assert tm._downloading.get("qwen") is False  # cleared in finally


def test_download_mlx_calls_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: True)
    monkeypatch.setattr(tm, "mlx_model_dir", lambda repo: tmp_path / repo.replace("/", "--"))
    called = {}
    monkeypatch.setattr(tm, "_snapshot_download", lambda repo, d: called.setdefault("repo", repo))
    tm.download_model("qwen_lite")
    assert called["repo"] == "mlx-community/Qwen3.5-4B-4bit"
    assert tm._downloading.get("qwen_lite") is False


def test_download_duplicate_is_noop(monkeypatch):
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: False)
    tm._downloading["qwen"] = True
    hit = {"n": 0}
    monkeypatch.setattr(to, "pull_model", lambda *a, **k: hit.__setitem__("n", hit["n"] + 1))
    tm.download_model("qwen")
    assert hit["n"] == 0  # skipped because already downloading


def test_download_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        tm.download_model("nope")


# ── delete_model ────────────────────────────────────────────────────────────
def test_delete_ollama(monkeypatch):
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: False)
    monkeypatch.setattr(to, "qwen_ollama_model", lambda name: "qwen3.5:9b")
    seen = {}
    monkeypatch.setattr(to, "delete_model", lambda tag: seen.setdefault("tag", tag))
    tm.delete_model("qwen")
    assert seen["tag"] == "qwen3.5:9b"


def test_delete_while_downloading_raises(monkeypatch):
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: False)
    tm._downloading["qwen"] = True
    with pytest.raises(RuntimeError):
        tm.delete_model("qwen")


# ── translate_ollama helpers ────────────────────────────────────────────────
def test_ollama_running_true(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp(status_code=200))
    assert to.ollama_running() is True


def test_ollama_running_false_on_error(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "get", boom)
    assert to.ollama_running() is False


def test_ollama_installed_via_which(monkeypatch):
    monkeypatch.setattr(to, "ollama_running", lambda: False)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/local/bin/ollama")
    assert to.ollama_installed() is True


def test_pull_model_reports_progress(monkeypatch):
    lines = [
        '{"status":"pulling","total":100,"completed":50}',
        '{"status":"pulling","total":100,"completed":100}',
        '{"status":"success"}',
    ]
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: FakeResp(lines=lines))
    pct = []
    to.pull_model("qwen3.5:9b", on_progress=pct.append)
    assert pct == [50, 99]  # 100% clamps to 99 (done signaled separately)


def test_pull_model_http_error_raises(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "stream", boom)
    with pytest.raises(RuntimeError):
        to.pull_model("qwen3.5:9b")


def test_delete_model_calls_api(monkeypatch):
    seen = {}

    def fake_request(method, url, json, timeout):
        seen["method"] = method
        seen["model"] = json["model"]
        return FakeResp(status_code=200)

    monkeypatch.setattr(httpx, "request", fake_request)
    to.delete_model("qwen3.5:9b")
    assert seen["method"] == "DELETE"
    assert seen["model"] == "qwen3.5:9b"


# ── router handlers ─────────────────────────────────────────────────────────
async def test_router_list(monkeypatch):
    monkeypatch.setattr(tm, "list_models", lambda: {"models": [], "runtime": "ollama"})
    assert await router.list_translate_models() == {"models": [], "runtime": "ollama"}


async def test_router_download_unknown_404():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await router.download_translate_model("nope")
    assert ei.value.status_code == 404


async def test_router_download_already(monkeypatch):
    monkeypatch.setattr(tm, "is_installed", lambda name: True)
    out = await router.download_translate_model("qwen")
    assert out["status"] == "already_downloaded"


async def test_router_download_ollama_not_running_409(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(tm, "is_installed", lambda name: False)
    monkeypatch.setattr(tm, "runtime", lambda: "ollama")
    monkeypatch.setattr(to, "ollama_running", lambda: False)
    with pytest.raises(HTTPException) as ei:
        await router.download_translate_model("qwen")
    assert ei.value.status_code == 409


async def test_router_download_starts(monkeypatch):
    monkeypatch.setattr(tm, "is_installed", lambda name: False)
    monkeypatch.setattr(tm, "runtime", lambda: "ollama")
    monkeypatch.setattr(to, "ollama_running", lambda: True)
    spawned = {}
    monkeypatch.setattr(router, "_spawn_download", lambda name: spawned.setdefault("name", name))
    out = await router.download_translate_model("qwen_hifi")
    assert out["status"] == "started"
    assert spawned["name"] == "qwen_hifi"


async def test_router_delete_conflict(monkeypatch):
    from fastapi import HTTPException
    def boom(name):
        raise RuntimeError("다운로드 중")
    monkeypatch.setattr(tm, "delete_model", boom)
    with pytest.raises(HTTPException) as ei:
        await router.delete_translate_model("qwen")
    assert ei.value.status_code == 409
