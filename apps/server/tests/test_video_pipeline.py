from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from apps.server.db import session as session_mod
from apps.server.db.models import AppUser, VideoJob, VideoSegment
from apps.server.domain.video_captions import pipeline as pl
from apps.server.domain.video_captions.srt import SubSegment


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    yield


@pytest.fixture(autouse=True)
async def _dispose_singleton_engine_pool():
    """pipeline.py deliberately uses the process-wide ``AsyncSessionLocal``
    singleton (own session, separate from the request/``db_session`` one).
    pytest-asyncio gives each test its own event loop, but the singleton's
    connection pool persists across tests — a pooled asyncpg connection
    opened in a previous test's (now-closed) loop cannot be reused in this
    one. Dispose the pool before each test so it opens fresh on first use in
    the current loop; this does not touch what the tests exercise."""
    await session_mod.engine.dispose()
    yield
    await session_mod.engine.dispose()


async def _make_job(db_session, admin_user: AppUser, **kw) -> VideoJob:
    job = VideoJob(
        external_id=uuid4(), owner_user_id=admin_user.id, title="t",
        source_type=kw.get("source_type", "upload"), source_ref="clip.mp4",
        whisper_model="small", status=kw.get("status", "queued"),
        media_path=kw.get("media_path"),
    )
    db_session.add(job)
    await db_session.commit()
    return job


async def test_run_video_job_happy_path(monkeypatch, db_session, admin_user, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"v")
    job = await _make_job(db_session, admin_user, media_path=str(src))
    job_id, external_id = job.id, job.external_id

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "extract_audio", lambda f, s, d: Path(d).write_bytes(b"a"))
    monkeypatch.setattr(pl, "ensure_preview", lambda f, s, d: s)
    monkeypatch.setattr(pl, "transcribe_audio",
                        lambda p, m, cb=None: [SubSegment(1, 0, 1000, "Hello")])

    async def fake_translate(segs, provider, **kw):
        return [SubSegment(1, 0, 1000, "안녕하세요")]

    monkeypatch.setattr(pl, "translate_segments", fake_translate)

    await pl.run_video_job(external_id)

    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.id == job_id))).scalar_one()
    assert loaded.status == "review"
    assert loaded.audio_path is not None
    segs = (await db_session.execute(
        select(VideoSegment).where(VideoSegment.job_id == job_id))).scalars().all()
    assert [(s.text_en, s.text_ko) for s in segs] == [("Hello", "안녕하세요")]


async def test_run_video_job_records_error(monkeypatch, db_session, admin_user, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"v")
    job = await _make_job(db_session, admin_user, media_path=str(src))
    job_id, external_id = job.id, job.external_id
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: None)

    await pl.run_video_job(external_id)

    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.id == job_id))).scalar_one()
    assert loaded.status == "error"
    assert "ffmpeg" in (loaded.error or "")


async def test_run_burn_job(monkeypatch, db_session, admin_user, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"v")
    job = await _make_job(db_session, admin_user, media_path=str(src), status="review")
    job_id, external_id = job.id, job.external_id
    db_session.add(VideoSegment(job_id=job_id, seq=1, start_ms=0, end_ms=1000,
                                text_en="Hello", text_ko="안녕하세요"))
    await db_session.commit()

    burned = {}
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")

    def fake_burn(ffmpeg, s, srt, dst, style, progress_cb=None):
        burned["style"] = style
        Path(dst).write_bytes(b"out")

    monkeypatch.setattr(pl, "burn_subtitles", fake_burn)

    await pl.run_burn_job(external_id, "top", 20, 24)

    assert burned["style"] == "Alignment=8,MarginV=20,Fontsize=24,PrimaryColour=&H00FFFFFF"
    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.id == job_id))).scalar_one()
    assert loaded.status == "done"
    assert loaded.burned_path and Path(loaded.burned_path).exists()


async def test_run_video_job_survives_missing_job(db_session):
    # 존재하지 않는 job이어도 예외가 새어나가면 안 된다 (에러 기록 실패는 삼킴+로그)
    await pl.run_video_job(uuid4())


async def test_run_burn_job_survives_missing_job(db_session):
    await pl.run_burn_job(uuid4(), "bottom", 40, 18)


async def test_startup_sweep_fails_inflight_jobs(db_session, admin_user, monkeypatch):
    monkeypatch.setattr(pl, "_another_instance_is_serving", lambda: False)
    inflight = await _make_job(db_session, admin_user, status="transcribing")
    inflight_id, inflight_external_id = inflight.id, inflight.external_id
    done = await _make_job(db_session, admin_user, status="done")
    done_id, done_external_id = done.id, done.external_id

    await pl.fail_inflight_video_jobs_at_startup()

    db_session.expire_all()
    rows = {r.external_id: r for r in (await db_session.execute(
        select(VideoJob).where(VideoJob.id.in_([inflight_id, done_id])))).scalars()}
    assert rows[inflight_external_id].status == "error"
    assert "재시작" in rows[inflight_external_id].error
    assert rows[done_external_id].status == "done"


async def test_startup_sweep_skipped_when_another_instance_serving(
        db_session, admin_user, monkeypatch):
    job = await _make_job(db_session, admin_user, status="transcribing")
    external_id = job.external_id
    monkeypatch.setattr(pl, "_another_instance_is_serving", lambda: True)
    await pl.fail_inflight_video_jobs_at_startup()
    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.external_id == external_id))).scalar_one()
    assert loaded.status == "transcribing"
