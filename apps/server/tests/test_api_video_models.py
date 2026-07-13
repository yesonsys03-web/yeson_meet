from __future__ import annotations

from pathlib import Path

import pytest

from apps.server.domain.video_captions import gpu_pack as gp
from apps.server.domain.video_captions import whisper_models as wm
from apps.server.api.v1 import video_models as api_vm


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    yield


async def test_list_models(client, monkeypatch):
    # Apple 미가용 기기(인텔/윈도우/구버전 macOS)에서도 목록엔 항상 노출된다.
    monkeypatch.setattr(api_vm, "apple_stt_available", lambda: False)
    resp = await client.get("/api/v1/video-models")
    assert resp.status_code == 200
    names = [m["name"] for m in resp.json()["models"]]
    assert names == ["apple", "tiny", "base", "small", "medium", "large-v3"]


async def test_apple_model_shown_but_unavailable_on_non_silicon(client, monkeypatch):
    monkeypatch.setattr(api_vm, "apple_stt_available", lambda: False)
    resp = await client.get("/api/v1/video-models")
    apple = next(m for m in resp.json()["models"] if m["name"] == "apple")
    assert apple["builtin"] is True
    assert apple["available"] is False
    assert apple["downloaded"] is False  # 드롭다운이 비활성 처리


async def test_apple_model_available_on_silicon(client, monkeypatch):
    monkeypatch.setattr(api_vm, "apple_stt_available", lambda: True)
    resp = await client.get("/api/v1/video-models")
    apple = next(m for m in resp.json()["models"] if m["name"] == "apple")
    assert apple["available"] is True
    assert apple["downloaded"] is True


async def test_download_starts_background_thread(client, monkeypatch):
    started = {}
    monkeypatch.setattr(api_vm, "_spawn_download", lambda name: started.setdefault("name", name))
    resp = await client.post("/api/v1/video-models/small/download")
    assert resp.status_code == 202
    assert started["name"] == "small"


async def test_download_unknown_model_404(client):
    resp = await client.post("/api/v1/video-models/giant/download")
    assert resp.status_code == 404


async def test_delete_model(client, tmp_path):
    d = wm.model_dir("tiny")
    d.mkdir(parents=True)
    (d / "model.bin").write_bytes(b"x")
    resp = await client.delete("/api/v1/video-models/tiny")
    assert resp.status_code == 204
    assert not d.exists()


async def test_no_auth_required(client):
    resp = await client.get("/api/v1/video-models")
    assert resp.status_code == 200


async def test_delete_model_downloading_conflict(client, monkeypatch):
    wm._downloading["tiny"] = True
    try:
        resp = await client.delete("/api/v1/video-models/tiny")
    finally:
        wm._downloading["tiny"] = False
    assert resp.status_code == 409


def _install_fake_gpu_pack() -> None:
    d = gp.bin_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "cublas64_12.dll").write_bytes(b"x")
    (d / "cudnn_ops64_9.dll").write_bytes(b"x")


async def test_gpu_status_shape(client, monkeypatch):
    monkeypatch.setattr(gp, "gpu_name", lambda: None)
    resp = await client.get("/api/v1/video-models/gpu")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("supported", "installed", "enabled", "cuda_available",
                "cuda_ok", "cuda_reason", "downloading", "approx_bytes"):
        assert key in body


async def test_gpu_pack_unsupported_platform_409(client, monkeypatch):
    monkeypatch.setattr(gp, "is_supported", lambda: False)
    resp = await client.post("/api/v1/video-models/gpu/pack")
    assert resp.status_code == 409


async def test_gpu_pack_download_starts_thread(client, monkeypatch):
    monkeypatch.setattr(gp, "is_supported", lambda: True)
    started = {}
    monkeypatch.setattr(api_vm, "_spawn_gpu_pack_download",
                        lambda: started.setdefault("hit", True))
    resp = await client.post("/api/v1/video-models/gpu/pack")
    assert resp.status_code == 202
    assert resp.json()["status"] == "started"
    assert started["hit"] is True


async def test_gpu_pack_already_installed(client, monkeypatch):
    monkeypatch.setattr(gp, "is_supported", lambda: True)
    _install_fake_gpu_pack()
    resp = await client.post("/api/v1/video-models/gpu/pack")
    assert resp.status_code == 202
    assert resp.json()["status"] == "already_installed"


async def test_gpu_enable_without_pack_409(client):
    resp = await client.post("/api/v1/video-models/gpu/enable",
                             json={"enabled": True})
    assert resp.status_code == 409


async def test_gpu_enable_disable_roundtrip(client):
    _install_fake_gpu_pack()
    resp = await client.post("/api/v1/video-models/gpu/enable",
                             json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    resp = await client.post("/api/v1/video-models/gpu/enable",
                             json={"enabled": False})
    assert resp.json()["enabled"] is False
