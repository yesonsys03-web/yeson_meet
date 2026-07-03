from __future__ import annotations

from pathlib import Path

import pytest

from apps.server.domain.video_captions import whisper_models as wm
from apps.server.api.v1 import video_models as api_vm


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    yield


async def test_list_models(client, admin_token):
    resp = await client.get("/api/v1/video-models",
                            headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    names = [m["name"] for m in resp.json()["models"]]
    assert names == ["tiny", "base", "small", "medium", "large-v3"]


async def test_download_starts_background_thread(client, admin_token, monkeypatch):
    started = {}
    monkeypatch.setattr(api_vm, "_spawn_download", lambda name: started.setdefault("name", name))
    resp = await client.post("/api/v1/video-models/small/download",
                             headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 202
    assert started["name"] == "small"


async def test_download_unknown_model_404(client, admin_token):
    resp = await client.post("/api/v1/video-models/giant/download",
                             headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 404


async def test_delete_model(client, admin_token, tmp_path):
    d = wm.model_dir("tiny")
    d.mkdir(parents=True)
    (d / "model.bin").write_bytes(b"x")
    resp = await client.delete("/api/v1/video-models/tiny",
                               headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 204
    assert not d.exists()


async def test_requires_auth(client):
    resp = await client.get("/api/v1/video-models")
    assert resp.status_code in (401, 403)


async def test_delete_model_downloading_conflict(client, admin_token, monkeypatch):
    wm._downloading["tiny"] = True
    try:
        resp = await client.delete("/api/v1/video-models/tiny",
                                   headers={"Authorization": f"Bearer {admin_token}"})
    finally:
        wm._downloading["tiny"] = False
    assert resp.status_code == 409
