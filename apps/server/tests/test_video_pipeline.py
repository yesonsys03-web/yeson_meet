from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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
    # audio.wav는 전사 후 유일 소비자가 사라지므로 즉시 삭제되고, 굽기 진행률
    # 분모로 쓰던 길이는 duration_ms로 DB에 남는다 (b"a" 가짜 wav는 wave 파싱
    # 실패 → 세그먼트 최대 end_ms 폴백).
    assert loaded.audio_path is None
    assert loaded.duration_ms == 1000
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


async def _make_dated_job(db_session, admin_user, created_at, *, status="done"):
    job = VideoJob(
        external_id=uuid4(), owner_user_id=admin_user.id, title="t",
        source_type="upload", source_ref="clip.mp4", whisper_model="small",
        status=status, created_at=created_at)
    db_session.add(job)
    await db_session.commit()
    d = pl.job_dir(job.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x")
    return job


async def test_prune_keeps_most_recent_n(db_session, admin_user):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    jobs = [await _make_dated_job(db_session, admin_user, base + timedelta(minutes=i))
            for i in range(12)]  # index 0 = oldest, 11 = newest
    ext = [j.external_id for j in jobs]  # capture before expiry/deletion

    removed = await pl.prune_old_video_jobs(keep=10)

    assert removed == 2
    db_session.expire_all()
    surviving = {r.external_id for r in (await db_session.execute(
        select(VideoJob))).scalars()}
    # 10 newest remain; 2 oldest gone from DB and disk
    assert ext[0] not in surviving
    assert ext[1] not in surviving
    assert not pl.job_dir(ext[0]).exists()
    assert not pl.job_dir(ext[1]).exists()
    for e in ext[2:]:
        assert e in surviving
        assert pl.job_dir(e).exists()


async def test_prune_never_deletes_inflight_job(db_session, admin_user):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # oldest job is still burning — must survive even though it is beyond keep=10
    burning = await _make_dated_job(db_session, admin_user, base, status="burning")
    older_done = await _make_dated_job(
        db_session, admin_user, base + timedelta(minutes=1), status="done")
    recent = [await _make_dated_job(
        db_session, admin_user, base + timedelta(minutes=2 + i))
        for i in range(10)]
    burning_ext = burning.external_id
    older_done_ext = older_done.external_id
    recent_ext = [j.external_id for j in recent]

    removed = await pl.prune_old_video_jobs(keep=10)

    assert removed == 1  # only older_done pruned; burning protected
    db_session.expire_all()
    surviving = {r.external_id for r in (await db_session.execute(
        select(VideoJob))).scalars()}
    assert burning_ext in surviving
    assert pl.job_dir(burning_ext).exists()
    assert older_done_ext not in surviving
    assert all(e in surviving for e in recent_ext)


async def test_prune_at_startup_skipped_when_another_instance_serving(
        db_session, admin_user, monkeypatch):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(12):
        await _make_dated_job(db_session, admin_user, base + timedelta(minutes=i))
    monkeypatch.setattr(pl, "_another_instance_is_serving", lambda: True)

    removed = await pl.prune_old_video_jobs_at_startup()

    assert removed == 0  # non-owning double-start must not prune the live instance
    db_session.expire_all()
    remaining = (await db_session.execute(
        select(VideoJob))).scalars().all()
    assert len(remaining) == 12


async def test_prune_reasserts_status_at_delete_time(
        monkeypatch, db_session, admin_user):
    """SELECT와 DELETE 사이에 review→burning으로 전이한 작업은 지우면 안 된다.

    스냅샷에서는 review(정당한 프루닝 후보)였다가, DELETE 직전에 굽기가
    시작(POST /burn)돼 burning이 된 작업. 상태 재확인 가드가 이를 살려야 한다.
    """
    from sqlalchemy import update as sa_update

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    review_job = await _make_dated_job(db_session, admin_user, base, status="review")
    review_ext, review_pk = review_job.external_id, review_job.id
    for i in range(10):
        await _make_dated_job(db_session, admin_user, base + timedelta(minutes=1 + i))

    async def flip_to_burning(candidate_ids):
        # the exact TOCTOU window: snapshot already taken, delete not yet issued
        assert review_pk in candidate_ids
        async with pl.AsyncSessionLocal() as db:
            await db.execute(sa_update(VideoJob)
                             .where(VideoJob.id == review_pk)
                             .values(status="burning"))
            await db.commit()

    monkeypatch.setattr(pl, "_prune_pre_delete_hook", flip_to_burning)

    removed = await pl.prune_old_video_jobs(keep=10)

    assert removed == 0  # the now-burning job is spared by the delete-time guard
    db_session.expire_all()
    surviving = {r.external_id for r in (await db_session.execute(
        select(VideoJob))).scalars()}
    assert review_ext in surviving
    assert pl.job_dir(review_ext).exists()


async def test_run_video_job_deletes_audio_after_transcribe(
        monkeypatch, db_session, admin_user, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"v")
    job = await _make_job(db_session, admin_user, media_path=str(src))
    external_id = job.external_id
    audio_seen = {}

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "ensure_preview", lambda f, s, d: s)

    def fake_extract(f, s, d):
        Path(d).write_bytes(b"a")

    def fake_transcribe(p, m, cb=None):
        # the wav must still exist while transcription consumes it
        audio_seen["exists_during"] = Path(p).exists()
        return [SubSegment(1, 0, 2500, "Hello")]

    monkeypatch.setattr(pl, "extract_audio", fake_extract)
    monkeypatch.setattr(pl, "transcribe_audio", fake_transcribe)

    async def fake_translate(segs, provider, **kw):
        return [SubSegment(1, 0, 2500, "안녕")]

    monkeypatch.setattr(pl, "translate_segments", fake_translate)

    await pl.run_video_job(external_id)

    assert audio_seen["exists_during"] is True
    assert not (pl.job_dir(external_id) / "audio.wav").exists()
    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.external_id == external_id))).scalar_one()
    assert loaded.audio_path is None
    assert loaded.duration_ms == 2500


async def test_run_burn_job_uses_stored_duration_without_wav(
        monkeypatch, db_session, admin_user, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"v")
    job = await _make_job(db_session, admin_user, media_path=str(src), status="review")
    job.duration_ms = 4000
    await db_session.commit()
    job_id, external_id = job.id, job.external_id
    db_session.add(VideoSegment(job_id=job_id, seq=1, start_ms=0, end_ms=1000,
                                text_en="Hello", text_ko="안녕"))
    await db_session.commit()

    progress_pcts: list[int] = []

    async def fake_set_progress(eid, pct):
        progress_pcts.append(pct)

    monkeypatch.setattr(pl, "_set_progress", fake_set_progress)
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")

    def fake_burn(ffmpeg, s, srt, dst, style, progress_cb=None):
        # duration must come from duration_ms (4.0s), NOT the segment max (1.0s),
        # so a progress callback at 2s maps to 50% (would be 100%-capped otherwise).
        assert progress_cb is not None
        progress_cb(2.0)
        Path(dst).write_bytes(b"out")

    monkeypatch.setattr(pl, "burn_subtitles", fake_burn)

    await pl.run_burn_job(external_id, "bottom", 40, 18)
    await asyncio.sleep(0)  # let the thread-scheduled progress callback drain

    assert 50 in progress_pcts
    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.id == job_id))).scalar_one()
    assert loaded.status == "done"
