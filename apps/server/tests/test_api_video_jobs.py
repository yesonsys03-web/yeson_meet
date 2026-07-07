from __future__ import annotations

import os
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
    # 리텐션 프루닝도 차단 — 프루닝 로직은 test_video_pipeline에서 직접 검증한다.
    monkeypatch.setattr(api_vj, "_prune_old_jobs", lambda: None)
    yield


async def _install_model(name: str = "small"):
    from apps.server.domain.video_captions.whisper_models import model_dir
    d = model_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.bin").write_bytes(b"x")


async def test_create_youtube_job(client, admin_user, db_session):
    await _install_model()
    resp = await client.post("/api/v1/video-jobs",
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "small"})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    row = (await db_session.execute(
        select(VideoJob).where(VideoJob.external_id == job_id))).scalar_one()
    assert row.source_type == "youtube"
    assert row.status == "queued"


async def test_create_job_rejects_missing_model(client):
    resp = await client.post("/api/v1/video-jobs",
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "medium"})
    assert resp.status_code == 409
    assert "다운로드" in resp.json()["detail"]


async def test_upload_job_saves_file(client, admin_user, db_session, tmp_path):
    await _install_model()
    resp = await client.post(
        "/api/v1/video-jobs/upload",
        data={"whisper_model": "small", "title": "클립"},
        files={"file": ("clip.mp4", b"fake-video-bytes", "video/mp4")},
    )
    assert resp.status_code == 201
    row = (await db_session.execute(select(VideoJob).where(
        VideoJob.external_id == resp.json()["job_id"]))).scalar_one()
    assert row.media_path and Path(row.media_path).read_bytes() == b"fake-video-bytes"
    assert row.title == "클립"


async def test_detail_includes_segments_and_patch_edits(client, db_session, admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.flush()
    job_id = job.id
    db_session.add(VideoSegment(job_id=job_id, seq=1, start_ms=0, end_ms=900,
                                text_en="Hi", text_ko="안녕"))
    await db_session.commit()

    detail = await client.get(f"/api/v1/video-jobs/{job.external_id}")
    assert detail.status_code == 200
    assert detail.json()["segments"][0]["text_ko"] == "안녕"

    patched = await client.patch(
        f"/api/v1/video-jobs/{job.external_id}/segments",
        json={"edits": [{"seq": 1, "text_ko": "안녕하세요!"}]})
    assert patched.status_code == 200
    db_session.expire_all()
    seg = (await db_session.execute(select(VideoSegment).where(
        VideoSegment.job_id == job_id))).scalar_one()
    assert seg.text_ko == "안녕하세요!"


async def test_burn_requires_review_status(client, db_session,
                                           admin_user, monkeypatch):
    started = {}
    monkeypatch.setattr(api_vj, "_start_burn",
                        lambda eid, p, m, f, c: started.setdefault("eid", eid))
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="transcribing")
    db_session.add(job)
    await db_session.commit()

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/burn",
                             json={"position": "bottom", "margin_v": 40,
                                   "font_size": 18})
    assert resp.status_code == 409

    job.status = "review"
    await db_session.commit()
    job_id = job.id
    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/burn",
                             json={"position": "bottom", "margin_v": 40,
                                   "font_size": 18})
    assert resp.status_code == 202
    assert started["eid"] == job.external_id
    db_session.expire_all()
    burning = (await db_session.execute(select(VideoJob).where(
        VideoJob.id == job_id))).scalar_one()
    assert burning.status == "burning"
    assert burning.progress == 0


async def test_burn_forwards_color_to_start_burn(client, db_session,
                                                 admin_user, monkeypatch):
    started = {}
    monkeypatch.setattr(
        api_vj, "_start_burn",
        lambda eid, p, m, f, c: started.update(eid=eid, color=c))
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.commit()

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/burn",
                             json={"position": "bottom", "margin_v": 40,
                                   "font_size": 18, "color": "#FF0000"})
    assert resp.status_code == 202
    assert started["eid"] == job.external_id
    assert started["color"] == "#FF0000"


async def test_burn_rejects_invalid_color(client, db_session, admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.commit()

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/burn",
                             json={"position": "bottom", "margin_v": 40,
                                   "font_size": 18, "color": "red"})
    assert resp.status_code == 422


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


async def test_srt_download(client, db_session, admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.flush()
    db_session.add(VideoSegment(job_id=job.id, seq=1, start_ms=0, end_ms=1000,
                                text_en="Hi", text_ko="안녕"))
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/video-jobs/{job.external_id}/download?kind=srt")
    assert resp.status_code == 200
    assert "안녕" in resp.text
    assert "00:00:00,000 --> 00:00:01,000" in resp.text


async def test_upload_cleans_up_on_failure(client, monkeypatch):
    await _install_model()

    async def boom(upload, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"partial")
        raise RuntimeError("disk full")

    monkeypatch.setattr(api_vj, "save_upload", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        await client.post(
            "/api/v1/video-jobs/upload",
            data={"whisper_model": "small"},
            files={"file": ("clip.mp4", b"x", "video/mp4")},
        )
    jobs_root = Path(os.environ["STORAGE_ROOT"]) / "video_jobs"
    assert not jobs_root.exists() or not any(jobs_root.iterdir())


async def test_no_auth_required_for_list(client, admin_user):
    resp = await client.get("/api/v1/video-jobs")
    assert resp.status_code == 200


async def test_create_youtube_job_saves_translate_provider(client, admin_user, db_session):
    await _install_model()
    resp = await client.post("/api/v1/video-jobs",
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "small",
                                   "translate_provider": "claude"})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    row = (await db_session.execute(
        select(VideoJob).where(VideoJob.external_id == job_id))).scalar_one()
    assert row.translate_provider == "claude"

    listed = await client.get("/api/v1/video-jobs")
    item = next(j for j in listed.json()["items"] if j["job_id"] == job_id)
    assert item["translate_provider"] == "claude"


async def test_translate_engines_endpoint(client, admin_user):
    resp = await client.get("/api/v1/video-jobs/translate-engines")
    assert resp.status_code == 200
    engines = resp.json()["engines"]
    assert len(engines) == 5
    assert {e["value"] for e in engines} == {
        "gemini", "claude", "codex", "agy", "opencode"}
    for engine in engines:
        assert "label" in engine
        assert isinstance(engine["available"], bool)


async def test_translate_engines_route_does_not_shadow_detail_route(client, db_session, admin_user):
    # "translate-engines"가 /{external_id} 동적 라우트보다 먼저 선언돼야 UUID로
    # 오인 파싱되지 않는다 — test_detail_includes_segments_and_patch_edits가
    # 상세 조회 자체는 이미 검증하므로, 여기서는 정적 라우트가 200을 반환하는지만 확인.
    resp = await client.get("/api/v1/video-jobs/translate-engines")
    assert resp.status_code == 200


async def test_list_with_sizes_adds_per_job_bytes(client, db_session, admin_user):
    from apps.server.domain.video_captions.pipeline import job_dir
    ext = uuid4()
    d = job_dir(ext)  # STORAGE_ROOT is tmp_path via the _env fixture
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"y" * 250)
    job = VideoJob(external_id=ext, owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4", whisper_model="small",
                   status="done")
    db_session.add(job)
    await db_session.commit()

    # default: no size_bytes (client hot-poll path stays cheap)
    plain = await client.get("/api/v1/video-jobs")
    assert plain.status_code == 200
    assert "size_bytes" not in plain.json()["items"][0]

    # opt-in: server console asks for per-job folder sizes
    sized = await client.get("/api/v1/video-jobs?with_sizes=true")
    assert sized.status_code == 200
    item = next(j for j in sized.json()["items"] if j["job_id"] == str(ext))
    assert item["size_bytes"] >= 250


async def test_storage_endpoint_reports_usage(client, db_session, admin_user):
    from apps.server.domain.video_captions.pipeline import job_dir
    ext = uuid4()
    d = job_dir(ext)  # STORAGE_ROOT is tmp_path via the _env fixture
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x" * 100)
    job = VideoJob(external_id=ext, owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4", whisper_model="small",
                   status="done")
    db_session.add(job)
    await db_session.commit()

    resp = await client.get("/api/v1/video-jobs/storage")
    assert resp.status_code == 200  # static route not shadowed by /{external_id}
    body = resp.json()
    assert body["total_bytes"] >= 100
    assert body["job_count"] == 1
    assert body["keep"] == 30


async def test_create_job_rejects_invalid_translate_provider(client, admin_user):
    await _install_model()
    resp = await client.post("/api/v1/video-jobs",
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "small",
                                   "translate_provider": "not-a-real-engine"})
    assert resp.status_code == 422
