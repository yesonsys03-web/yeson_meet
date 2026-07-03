from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from apps.server.api.v1 import video_jobs as api_vj
from apps.server.db.models import VideoJob, VideoSegment


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    # 파이프라인 자동 시작 차단 — API 계층만 검증
    monkeypatch.setattr(api_vj, "_start_pipeline", lambda external_id: None)
    yield


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _install_model(name: str = "small"):
    from apps.server.domain.video_captions.whisper_models import model_dir
    d = model_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.bin").write_bytes(b"x")


async def test_create_youtube_job(client, admin_token, db_session):
    await _install_model()
    resp = await client.post("/api/v1/video-jobs", headers=_auth(admin_token),
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "small"})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    row = (await db_session.execute(
        select(VideoJob).where(VideoJob.external_id == job_id))).scalar_one()
    assert row.source_type == "youtube"
    assert row.status == "queued"


async def test_create_job_rejects_missing_model(client, admin_token):
    resp = await client.post("/api/v1/video-jobs", headers=_auth(admin_token),
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "medium"})
    assert resp.status_code == 409
    assert "다운로드" in resp.json()["detail"]


async def test_upload_job_saves_file(client, admin_token, db_session, tmp_path):
    await _install_model()
    resp = await client.post(
        "/api/v1/video-jobs/upload", headers=_auth(admin_token),
        data={"whisper_model": "small", "title": "클립"},
        files={"file": ("clip.mp4", b"fake-video-bytes", "video/mp4")},
    )
    assert resp.status_code == 201
    row = (await db_session.execute(select(VideoJob).where(
        VideoJob.external_id == resp.json()["job_id"]))).scalar_one()
    assert row.media_path and Path(row.media_path).read_bytes() == b"fake-video-bytes"
    assert row.title == "클립"


async def test_detail_includes_segments_and_patch_edits(client, admin_token,
                                                        db_session, admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.flush()
    job_id = job.id
    db_session.add(VideoSegment(job_id=job_id, seq=1, start_ms=0, end_ms=900,
                                text_en="Hi", text_ko="안녕"))
    await db_session.commit()

    detail = await client.get(f"/api/v1/video-jobs/{job.external_id}",
                              headers=_auth(admin_token))
    assert detail.status_code == 200
    assert detail.json()["segments"][0]["text_ko"] == "안녕"

    patched = await client.patch(
        f"/api/v1/video-jobs/{job.external_id}/segments",
        headers=_auth(admin_token),
        json={"edits": [{"seq": 1, "text_ko": "안녕하세요!"}]})
    assert patched.status_code == 200
    db_session.expire_all()
    seg = (await db_session.execute(select(VideoSegment).where(
        VideoSegment.job_id == job_id))).scalar_one()
    assert seg.text_ko == "안녕하세요!"


async def test_burn_requires_review_status(client, admin_token, db_session,
                                           admin_user, monkeypatch):
    started = {}
    monkeypatch.setattr(api_vj, "_start_burn",
                        lambda eid, p, m, f: started.setdefault("eid", eid))
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="transcribing")
    db_session.add(job)
    await db_session.commit()

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/burn",
                             headers=_auth(admin_token),
                             json={"position": "bottom", "margin_v": 40,
                                   "font_size": 18})
    assert resp.status_code == 409

    job.status = "review"
    await db_session.commit()
    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/burn",
                             headers=_auth(admin_token),
                             json={"position": "bottom", "margin_v": 40,
                                   "font_size": 18})
    assert resp.status_code == 202
    assert started["eid"] == job.external_id


async def test_media_is_capability_url_no_auth(client, db_session, admin_user,
                                               tmp_path):
    media = tmp_path / "preview.mp4"
    media.write_bytes(b"stream-me")
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4", whisper_model="small",
                   status="review", preview_path=str(media))
    db_session.add(job)
    await db_session.commit()

    resp = await client.get(f"/api/v1/video-jobs/{job.external_id}/media")
    assert resp.status_code == 200
    assert resp.content == b"stream-me"


async def test_srt_download(client, admin_token, db_session, admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.flush()
    db_session.add(VideoSegment(job_id=job.id, seq=1, start_ms=0, end_ms=1000,
                                text_en="Hi", text_ko="안녕"))
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/video-jobs/{job.external_id}/download?kind=srt",
        headers=_auth(admin_token))
    assert resp.status_code == 200
    assert "안녕" in resp.text
    assert "00:00:00,000 --> 00:00:01,000" in resp.text
