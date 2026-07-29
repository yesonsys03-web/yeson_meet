from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from apps.server.db import session as session_mod
from apps.server.db.models import AppUser, VideoJob, VideoSegment
from apps.server.domain.video_captions import job_tasks as jt
from apps.server.domain.video_captions import pipeline as pl
from apps.server.domain.video_captions import translate as tl
from apps.server.domain.video_captions.ffmpeg import FfmpegError
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
    monkeypatch.setattr(pl, "extract_audio", lambda f, s, d, proc_key=None: Path(d).write_bytes(b"a"))
    monkeypatch.setattr(pl, "ensure_preview", lambda f, s, d, proc_key=None: s)
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


async def test_run_video_job_serialized_by_semaphore(
        monkeypatch, db_session, admin_user, tmp_path):
    """여러 영상 작업을 동시에 띄워도 서버는 한 번에 하나씩만 처리해야 한다
    (다중/폴더 배치 순차 보장 + 동시 whisper 전사 자원 경합 방지)."""
    import time as _time

    srcA = tmp_path / "a.mp4"; srcA.write_bytes(b"v")
    srcB = tmp_path / "b.mp4"; srcB.write_bytes(b"v")
    jobA = await _make_job(db_session, admin_user, media_path=str(srcA))
    jobB = await _make_job(db_session, admin_user, media_path=str(srcB))

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "ensure_preview", lambda f, s, d, proc_key=None: s)
    monkeypatch.setattr(pl, "extract_audio", lambda f, s, d, proc_key=None: Path(d).write_bytes(b"a"))

    concur = {"cur": 0, "max": 0}

    def fake_transcribe(p, m, cb=None):
        concur["cur"] += 1
        concur["max"] = max(concur["max"], concur["cur"])
        _time.sleep(0.2)  # hold the stage so an overlap is observable if not serial
        concur["cur"] -= 1
        return [SubSegment(1, 0, 1000, "Hi")]

    monkeypatch.setattr(pl, "transcribe_audio", fake_transcribe)

    async def fake_translate(segs, provider, **kw):
        return [SubSegment(1, 0, 1000, "안녕")]

    monkeypatch.setattr(pl, "translate_segments", fake_translate)

    await asyncio.gather(
        pl.run_video_job(jobA.external_id), pl.run_video_job(jobB.external_id))

    assert concur["max"] == 1  # never two transcriptions running at once


async def test_cancel_job_task_releases_semaphore(
        monkeypatch, db_session, admin_user, tmp_path):
    """실행 중인 작업을 삭제(취소)하면 그 파이프라인 태스크가 즉시 멈추고 세마포어를
    반납해야 한다. 안 그러면 좀비 태스크가 세마포어를 쥔 채 다른 작업을 무한 대기
    시키고 NoResultFound/FileNotFound 에러를 반복한다(실사용 버그)."""
    src = tmp_path / "a.mp4"; src.write_bytes(b"v")
    job = await _make_job(db_session, admin_user, media_path=str(src))
    ext = job.external_id

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "ensure_preview", lambda f, s, d, proc_key=None: s)
    monkeypatch.setattr(pl, "extract_audio", lambda f, s, d, proc_key=None: Path(d).write_bytes(b"a"))
    monkeypatch.setattr(pl, "transcribe_audio",
                        lambda p, m, cb=None: [SubSegment(1, 0, 1000, "Hi")])

    reached = asyncio.Event()
    blocking = asyncio.Event()

    async def fake_translate(segs, provider, **kw):
        reached.set()
        await blocking.wait()  # hold the semaphore (cancellable await) until cancelled
        return [SubSegment(1, 0, 1000, "안녕")]

    monkeypatch.setattr(pl, "translate_segments", fake_translate)

    pl.start_job_task(ext, pl.run_video_job(ext))
    await asyncio.wait_for(reached.wait(), timeout=5)
    assert pl._JOB_SEMAPHORE.locked()  # job is mid-flight holding the semaphore

    assert pl.cancel_job_task(ext) is True
    await asyncio.sleep(0.1)  # let CancelledError propagate through the finally
    assert not pl._JOB_SEMAPHORE.locked()  # semaphore released → queue can proceed
    assert pl.cancel_job_task(ext) is False  # nothing left to cancel


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

    def fake_burn(ffmpeg, s, srt, dst, style, progress_cb=None, proc_key=None, use_gpu=None):
        burned["style"] = style
        Path(dst).write_bytes(b"out")

    monkeypatch.setattr(pl, "burn_subtitles", fake_burn)

    await pl.run_burn_job(external_id, "top", 20, 24)

    assert burned["style"] == "Alignment=6,MarginV=20,Fontsize=24,PrimaryColour=&H00FFFFFF"
    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.id == job_id))).scalar_one()
    assert loaded.status == "done"
    assert loaded.burned_path and Path(loaded.burned_path).exists()


async def test_run_burn_job_serialized_by_semaphore(
        monkeypatch, db_session, admin_user, tmp_path):
    """굽기도 한 번에 하나씩만 돌아야 한다. '선택 굽기 (N개)'는 클라이언트가
    burn POST를 연달아 쏘고 엔드포인트는 즉시 반환하므로, 직렬화가 없으면
    ffmpeg 인코딩 N개가 동시에 돌아 CPU/GPU를 포화시킨다."""
    import time as _time

    jobs = []
    for name in ("a.mp4", "b.mp4"):
        src = tmp_path / name
        src.write_bytes(b"v")
        job = await _make_job(db_session, admin_user,
                              media_path=str(src), status="review")
        db_session.add(VideoSegment(job_id=job.id, seq=1, start_ms=0,
                                    end_ms=1000, text_en="Hi", text_ko="안녕"))
        jobs.append(job)
    await db_session.commit()

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    concur = {"cur": 0, "max": 0}

    def fake_burn(ffmpeg, s, srt, dst, style, progress_cb=None, proc_key=None,
                  use_gpu=None):
        concur["cur"] += 1
        concur["max"] = max(concur["max"], concur["cur"])
        _time.sleep(0.2)  # hold the stage so an overlap is observable if not serial
        concur["cur"] -= 1
        Path(dst).write_bytes(b"out")

    monkeypatch.setattr(pl, "burn_subtitles", fake_burn)

    await asyncio.gather(
        pl.run_burn_job(jobs[0].external_id, "top", 20, 24),
        pl.run_burn_job(jobs[1].external_id, "top", 20, 24))

    assert concur["max"] == 1  # never two burns running at once


async def test_cancel_burn_task_releases_burn_semaphore(
        monkeypatch, db_session, admin_user, tmp_path):
    """굽는 중 취소하면 굽기 세마포어가 즉시 반납되어 다음 굽기가 진행돼야 한다
    (전사 세마포어의 test_cancel_job_task_releases_semaphore와 동일한 보장)."""
    import threading

    src = tmp_path / "a.mp4"; src.write_bytes(b"v")
    job = await _make_job(db_session, admin_user,
                          media_path=str(src), status="review")
    db_session.add(VideoSegment(job_id=job.id, seq=1, start_ms=0,
                                end_ms=1000, text_en="Hi", text_ko="안녕"))
    await db_session.commit()
    ext = job.external_id

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    reached = threading.Event()
    blocking = threading.Event()

    def fake_burn(ffmpeg, s, srt, dst, style, progress_cb=None, proc_key=None,
                  use_gpu=None):
        reached.set()
        blocking.wait(5)  # hold the burn in its worker thread until released

    monkeypatch.setattr(pl, "burn_subtitles", fake_burn)

    pl.start_job_task(ext, pl.run_burn_job(ext, "top", 20, 24))
    await asyncio.to_thread(reached.wait, 5)
    assert pl._BURN_SEMAPHORE.locked()  # burn is mid-flight holding the semaphore

    assert pl.cancel_job_task(ext) is True
    await asyncio.sleep(0.1)  # let CancelledError propagate through the finally
    assert not pl._BURN_SEMAPHORE.locked()  # released even though thread lingers
    blocking.set()  # let the detached worker thread exit


@pytest.mark.parametrize("enabled", [True, False])
async def test_run_burn_job_always_burns_on_cpu(
        monkeypatch, db_session, admin_user, tmp_path, enabled):
    """굽기는 GPU 토글과 무관하게 항상 CPU(libx264)여야 한다 — RTX 2080 실측
    (2026-07-10)에서 NVENC p5보다 x264 veryfast가 더 빨랐다. 병목이 CPU쪽
    디코드+libass 자막 렌더링이라 GPU 인코더 이득이 없고 복사 오버헤드만 붙는다.
    GPU 토글은 전사 전용."""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"v")
    job = await _make_job(db_session, admin_user, media_path=str(src), status="review")
    job_id, external_id = job.id, job.external_id
    db_session.add(VideoSegment(job_id=job_id, seq=1, start_ms=0, end_ms=1000,
                                text_en="Hello", text_ko="안녕하세요"))
    await db_session.commit()

    monkeypatch.setattr(pl.gpu_pack, "is_enabled", lambda: enabled)
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")

    seen_use_gpu = {}

    def fake_burn(ffmpeg, s, srt, dst, style, progress_cb=None, proc_key=None, use_gpu=None):
        seen_use_gpu["v"] = use_gpu
        Path(dst).write_bytes(b"out")

    monkeypatch.setattr(pl, "burn_subtitles", fake_burn)

    await pl.run_burn_job(external_id, "top", 20, 24)

    assert seen_use_gpu["v"] is False  # GPU 토글 값과 무관하게 항상 CPU


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
    monkeypatch.setattr(pl, "ensure_preview", lambda f, s, d, proc_key=None: s)

    def fake_extract(f, s, d, proc_key=None):
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

    async def fake_set_progress(eid, pct, generation):
        progress_pcts.append(pct)

    monkeypatch.setattr(pl, "_set_progress", fake_set_progress)
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")

    def fake_burn(ffmpeg, s, srt, dst, style, progress_cb=None, proc_key=None, use_gpu=None):
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


async def test_stale_generation_progress_write_is_dropped(
        db_session, admin_user, tmp_path):
    """유령 스레드가 지연 스케줄한 진행률 쓰기는, 그 사이 세대가 올라갔으면
    (취소·재생성) 조용히 버려져야 한다 — 새 실행/대기 상태를 덮어쓰지 않도록."""
    job = await _make_job(db_session, admin_user, status="transcribing")
    job.progress = 10
    await db_session.commit()
    job_id, ext = job.id, job.external_id

    stale_generation = pl._current_generation(ext)  # 0 (아직 아무 run도 없음)
    pl._bump_generation(ext)  # 새 run이 시작된 것처럼 세대를 올림 → stale_generation은 이제 낡음

    await pl._set_progress(ext, 77, stale_generation)

    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.id == job_id))).scalar_one()
    assert loaded.progress == 10  # 77로 덮어쓰이지 않음

    await pl._set_progress(ext, 55, pl._current_generation(ext))
    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.id == job_id))).scalar_one()
    assert loaded.progress == 55  # 현재 세대의 쓰기는 정상 반영


async def test_status_transition_to_queued_resets_progress(db_session, admin_user):
    """대기 전환 시 진행률 리셋 — 재생성 직후 목록에 옛 77%가 잠깐이라도 남지
    않도록 _PROGRESS에 queued:0을 명시한다."""
    job = await _make_job(db_session, admin_user, status="transcribing")
    job.progress = 77
    await db_session.commit()
    job_id, ext = job.id, job.external_id

    await pl._set_status(ext, "queued")

    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.id == job_id))).scalar_one()
    assert loaded.progress == 0


async def test_cancel_job_task_bumps_generation(db_session, admin_user, tmp_path):
    """cancel_job_task는 실행 중인 태스크가 없어도(이미 끝났거나 존재하지 않아도)
    세대를 올려야 한다 — 취소 시점 이후 도착하는 유령 진행률 쓰기를 무효화."""
    job = await _make_job(db_session, admin_user, status="transcribing")
    ext = job.external_id

    before = pl._current_generation(ext)
    pl.cancel_job_task(ext)  # no running task registered — still bumps
    after = pl._current_generation(ext)

    assert after == before + 1


async def test_cancel_job_task_kills_active_ffmpeg_proc(db_session, admin_user, monkeypatch):
    """취소 시 다음 진행률 라인을 기다리지 않고 활성 ffmpeg 프로세스를 즉시
    kill해야 한다 — task.cancel()은 워커 스레드에 닿지 않기 때문."""
    job = await _make_job(db_session, admin_user, status="burning")
    ext = job.external_id
    killed: list[str] = []
    # cancel_job_task는 job_tasks로 옮겨졌다 — 패치도 그 모듈에(파사드 패치는 안 닿는다).
    monkeypatch.setattr(jt, "kill_active", lambda key: killed.append(key) or True)

    pl.cancel_job_task(ext)

    assert killed == [str(ext)]


async def test_run_video_job_extract_killed_stays_cancelled_not_error(
        monkeypatch, db_session, admin_user, tmp_path):
    """추출(extract_audio) 도중 취소되면(kill_active로 ffmpeg가 비정상 종료) error가
    아니라 cancelled 상태를 유지해야 한다."""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"v")
    job = await _make_job(db_session, admin_user, media_path=str(src))
    job_id, ext = job.id, job.external_id

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "ensure_preview", lambda f, s, d, proc_key=None: s)

    loop = asyncio.get_running_loop()

    async def _cancel_mid_extract():
        # 사용자의 취소를 재현: 상태 cancelled 마킹 + 세대 상승(cancel_job_task와 동일)
        async with pl.AsyncSessionLocal() as db:
            j = await pl._load_job(db, ext)
            j.status = "cancelled"
            j.progress = 0
            await db.commit()
        pl._bump_generation(ext)

    def fake_extract(f, s, d, proc_key=None):
        asyncio.run_coroutine_threadsafe(_cancel_mid_extract(), loop).result(timeout=10)
        # kill_active로 죽은 ffmpeg는 이렇게 FfmpegError로 표면화된다
        raise FfmpegError("ffmpeg failed (code=-9): killed")

    monkeypatch.setattr(pl, "extract_audio", fake_extract)

    await pl.run_video_job(ext)

    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.id == job_id))).scalar_one()
    assert loaded.status == "cancelled"  # error로 덮어쓰지 않음
    assert loaded.error is None


async def test_run_burn_job_killed_ffmpeg_error_stays_cancelled(
        monkeypatch, db_session, admin_user, tmp_path):
    """굽기 도중 kill_active로 ffmpeg가 죽어 FfmpegError(StaleRunCancelled 아님)로
    표면화돼도, 이미 cancelled로 마킹된 상태를 error로 덮어쓰지 않는다."""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"v")
    job = await _make_job(db_session, admin_user, media_path=str(src), status="review")
    job.duration_ms = 4000
    await db_session.commit()
    job_id, ext = job.id, job.external_id
    db_session.add(VideoSegment(job_id=job_id, seq=1, start_ms=0, end_ms=1000,
                                text_en="Hello", text_ko="안녕"))
    await db_session.commit()

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    loop = asyncio.get_running_loop()

    async def _cancel_mid_burn():
        async with pl.AsyncSessionLocal() as db:
            j = await pl._load_job(db, ext)
            j.status = "cancelled"
            j.progress = 0
            await db.commit()
        pl._bump_generation(ext)

    def fake_burn(ffmpeg, s, srt, dst, style, progress_cb=None, proc_key=None, use_gpu=None):
        asyncio.run_coroutine_threadsafe(_cancel_mid_burn(), loop).result(timeout=10)
        raise FfmpegError("ffmpeg failed (code=-9): killed")

    monkeypatch.setattr(pl, "burn_subtitles", fake_burn)

    await pl.run_burn_job(ext, "bottom", 40, 18)

    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.id == job_id))).scalar_one()
    assert loaded.status == "cancelled"  # error로 덮어쓰지 않음
    assert loaded.burned_path is None


async def test_maybe_aclose_translator_calls_when_present():
    """pipeline이 쓰는 옵셔널 aclose 정리 규약을 검증 (QwenMlxTranslator처럼
    서브프로세스 워커를 쓰는 번역기는 잡 종료 시 정리된다)."""

    class WithAclose:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    t1 = WithAclose()
    await tl.maybe_aclose_translator(t1)
    assert t1.closed is True


async def test_maybe_aclose_translator_noop_when_absent():
    """aclose가 없는 번역기(gemini/CLI/apple)는 예외 없이 무시된다."""

    class WithoutAclose:
        pass

    await tl.maybe_aclose_translator(WithoutAclose())


async def test_run_burn_job_stale_generation_stops_and_keeps_cancelled(
        monkeypatch, db_session, admin_user, tmp_path):
    """굽기 도중 취소되면(상태 cancelled + 세대 상승) 진행 콜백이 StaleRunCancelled를
    던져 조기 종료하고, 이미 cancelled로 마킹된 상태를 error로 덮어쓰지 않는다."""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"v")
    job = await _make_job(db_session, admin_user, media_path=str(src), status="review")
    job.duration_ms = 4000
    await db_session.commit()
    job_id, ext = job.id, job.external_id
    db_session.add(VideoSegment(job_id=job_id, seq=1, start_ms=0, end_ms=1000,
                                text_en="Hello", text_ko="안녕"))
    await db_session.commit()

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    loop = asyncio.get_running_loop()

    async def _cancel_mid_burn():
        # 사용자의 취소를 재현: 상태 cancelled 마킹 + 세대 상승(엔드포인트와 동일)
        async with pl.AsyncSessionLocal() as db:
            j = await pl._load_job(db, ext)
            j.status = "cancelled"
            j.progress = 0
            await db.commit()
        pl._bump_generation(ext)

    def fake_burn(ffmpeg, s, srt, dst, style, progress_cb=None, proc_key=None, use_gpu=None):
        assert progress_cb is not None
        asyncio.run_coroutine_threadsafe(_cancel_mid_burn(), loop).result(timeout=10)
        progress_cb(2.0)  # stale 세대 감지 → StaleRunCancelled를 기대
        raise AssertionError("progress_cb must raise StaleRunCancelled on stale run")

    monkeypatch.setattr(pl, "burn_subtitles", fake_burn)

    await pl.run_burn_job(ext, "bottom", 40, 18)

    db_session.expire_all()
    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.id == job_id))).scalar_one()
    assert loaded.status == "cancelled"  # error로 덮어쓰지 않음
    assert loaded.burned_path is None    # done 처리도 되지 않음


# ── 지문 컷 감지 스캔 (run_scene_scan_fingerprint) ───────────────────────────

def test_build_fingerprint_segments_groups_both_modes():
    """지문 런 + 규칙 → 양 모드 세그먼트(순수) — 씬은 씬 토큰까지, 시퀀스는
    seq 토큰만으로 그룹. 경계는 런 경계 그대로(흡수·정렬·정밀화 없음)."""
    runs = [
        {"start_ms": 0, "end_ms": 100, "text": "HH0307_010_0010_AC_v01"},
        {"start_ms": 100, "end_ms": 250, "text": "HH0307_010_0010_AC_v01"},
        {"start_ms": 250, "end_ms": 500, "text": "HH0307_010_0020_AC_v01"},
        {"start_ms": 500, "end_ms": 900, "text": "HH0307_020_0010_AC_v01"},
    ]
    rule = {"delimiters": ["_", "-"], "seq_tokens": [1], "scene_tokens": [2]}
    out = pl.build_fingerprint_segments(runs, rule)
    assert [(s["label"], s["start_ms"], s["end_ms"])
            for s in out["segments_scene"]] == [
        ("HH0307_010_0010", 0, 250), ("HH0307_010_0020", 250, 500),
        ("HH0307_020_0010", 500, 900)]
    assert [(s["label"], s["start_ms"], s["end_ms"])
            for s in out["segments_sequence"]] == [
        ("HH0307_010", 0, 500), ("HH0307_020", 500, 900)]


async def test_run_scene_scan_fingerprint_happy_path(monkeypatch, tmp_path):
    """추출→지문 diff(진짜 계산)→컷→런 중간 OCR→scenes.json 저장 전 구간.

    페이크 추출이 프레임 10에서 패턴이 바뀌는 PNG 20장을 만들고, 지문 diff가
    실제로 그 컷 하나를 찾는지, 런 경계가 frame_boundary_ms 규약으로 기록되는지,
    method/runs/frames(호환 샘플)/total_ms/video_fps가 저장되는지 확인한다."""
    from PIL import Image

    from apps.server.domain.video_captions.fingerprint import frame_boundary_ms

    eid = uuid4()
    workdir = pl.job_dir(eid)
    workdir.mkdir(parents=True)
    (workdir / "burned.mp4").write_bytes(b"v")

    def fake_extract_fp(ffmpeg, src, out_dir, region, scale_w=160,
                        proc_key=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(20):
            im = Image.new("L", (32, 8), 255)
            for x in (range(0, 20) if i < 10 else range(12, 32)):
                for y in range(8):
                    im.putpixel((x, y), 0)
            im.save(out_dir / f"f_{i + 1:06d}.png")

    def fake_thumbs(ffmpeg, src, out_dir, interval_s, proc_key=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "thumb_00001.jpg").write_bytes(b"j")

    def fake_extract_frame(ffmpeg, src, t_ms, dst, proc_key=None, region=None):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(str(t_ms))  # 판독 페이크가 어느 프레임인지 알 수 있게

    # 배치 추출 페이크 — 실제 구현과 같은 프레임번호→경로 계약으로, 파일에
    # 그 프레임의 중앙 시각을 적어 판독 페이크가 라벨을 결정할 수 있게 한다.
    def fake_extract_at(ffmpeg, src, frame_indices, out_dir, region,
                        proc_key=None, workers=1):
        out_dir.mkdir(parents=True, exist_ok=True)
        out = {}
        for k, n in enumerate(sorted(set(frame_indices)), 1):
            p = out_dir / f"at_{k:05d}.png"
            p.write_text(str(frame_boundary_ms(n, 24.0)))
            out[n] = p
        return out

    cut_ms = frame_boundary_ms(10, 24.0)

    def fake_read(path, delimiters, min_tokens=2, top_frac=0.35):
        t_ms = int(Path(path).read_text())
        return ("HH0307_010_0010_AC_v01" if t_ms < cut_ms
                else "HH0307_010_0020_AC_v01")

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "video_fps", lambda f, s: 24.0)
    monkeypatch.setattr(pl, "build_scan_source",
                        lambda ffmpeg, src, dst, region, proc_key=None:
                        dst.write_bytes(b"s"))
    monkeypatch.setattr(pl, "extract_fingerprint_frames", fake_extract_fp)
    monkeypatch.setattr(pl, "extract_thumbnails", fake_thumbs)
    monkeypatch.setattr(pl, "extract_frames_at", fake_extract_at)
    monkeypatch.setattr(pl, "extract_frame", fake_extract_frame)
    monkeypatch.setattr(pl, "read_slate_line", fake_read)

    await pl.run_scene_scan_fingerprint(eid)

    data = pl.load_scenes(eid)
    assert data["scanning"] is False and data.get("error") is None
    assert data["method"] == "fingerprint"
    assert data["video_fps"] == 24.0
    assert data["total_ms"] == frame_boundary_ms(20, 24.0)
    assert data["runs"] == [
        {"start_ms": frame_boundary_ms(0, 24.0), "end_ms": cut_ms,
         "text": "HH0307_010_0010_AC_v01", "cut_diff": 0},
        {"start_ms": cut_ms, "end_ms": frame_boundary_ms(20, 24.0),
         "text": "HH0307_010_0020_AC_v01", "cut_diff": 192},
    ]
    # 토큰 선택 UI 호환 샘플 — 런 시작 시각·텍스트.
    assert data["frames"] == [
        {"t_ms": r["start_ms"], "text": r["text"]} for r in data["runs"]]
    assert data["thumb_count"] == 1
    # 지문 프레임 임시 디렉토리는 정리된다(크기가 크다).
    assert not (workdir / "scene_fp_frames").exists()


async def test_run_scene_scan_fingerprint_failure_writes_error(
        monkeypatch, tmp_path):
    """진짜 실패(추출 예외)는 error를 기록해 프론트 폴링이 멈추게 한다 —
    method도 남겨 방식 선택이 유지되게."""
    eid = uuid4()
    workdir = pl.job_dir(eid)
    workdir.mkdir(parents=True)
    (workdir / "burned.mp4").write_bytes(b"v")
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "video_fps", lambda f, s: 24.0)

    def boom(*a, **kw):
        raise FfmpegError("x")

    monkeypatch.setattr(pl, "build_scan_source",
                        lambda ffmpeg, src, dst, region, proc_key=None:
                        dst.write_bytes(b"s"))
    monkeypatch.setattr(pl, "extract_fingerprint_frames", boom)
    await pl.run_scene_scan_fingerprint(eid)
    data = pl.load_scenes(eid)
    assert data["scanning"] is False
    assert data["error"]
    assert data["method"] == "fingerprint"


def test_build_fingerprint_segments_canonicalizes_and_absorbs():
    """지문 오독 대응 배선: ①구분자 유실 오독은 canonical화로 같은 키가 되어
    병합되고 ②교정 불가 오독(문자 오독)은 클러스터 흡수(≤5s)로 사라진다 —
    실기(HZBN307)에서 씬 806→481·시퀀스 322→19를 만든 조합."""
    runs = [
        {"start_ms": 0, "end_ms": 1000, "text": "HH0307_010_0010_AC_v01"},
        # 구분자 유실 오독 — canonical화가 고쳐 위와 같은 키로 병합돼야 한다.
        {"start_ms": 1000, "end_ms": 1200, "text": "HH0307010_0010_AC_v01"},
        # 문자 오독(Z) — 교정 불가지만 같은 키 사이 짧은 클러스터라 흡수된다.
        {"start_ms": 1200, "end_ms": 1300, "text": "HH030Z_010_0010_AC_v01"},
        {"start_ms": 1300, "end_ms": 2000, "text": "HH0307_010_0010_AC_v01"},
        {"start_ms": 2000, "end_ms": 3000, "text": "HH0307_010_0020_AC_v01"},
    ]
    rule = {"delimiters": ["_", "-"], "seq_tokens": [1], "scene_tokens": [2]}
    out = pl.build_fingerprint_segments(runs, rule)
    assert [(s["label"], s["start_ms"], s["end_ms"])
            for s in out["segments_scene"]] == [
        ("HH0307_010_0010", 0, 2000), ("HH0307_010_0020", 2000, 3000)]
    assert [(s["label"], s["start_ms"], s["end_ms"])
            for s in out["segments_sequence"]] == [("HH0307_010", 0, 3000)]


# ── 디졸브 경계 OCR 정렬 (_align_cut) ────────────────────────────────────────
# 지문 컷=픽셀 전환 지점은 디졸브에서 슬레이트 '가독' 전환과 어긋난다(실기:
# 130→140 컷이 6프레임 지각 — 끝 6프레임이 다음 시퀀스로 읽힘, 030→040은
# 1프레임 조기). 컷을 '다음 슬레이트가 읽히는 첫 프레임'으로 옮긴다.

def _mk_read(mapping):
    return lambda f: mapping.get(f, "")


PREV = "HH0307_130_0330_AC_v01"
NEXT = "HH0307_140_0010_AC_v01"


def test_align_cut_keeps_exact_boundary():
    read = _mk_read({9: PREV, 10: NEXT})
    assert pl._align_cut(read, 10, PREV, NEXT, lo=0, hi=20, delimiters=["_", "-"]) == 10


def test_align_cut_moves_left_when_next_leaks_before():
    # 컷 지각: d-1..d-6이 이미 다음 슬레이트로 읽힘 → 첫 다음-프레임(4)으로.
    read = _mk_read({3: PREV, **{f: NEXT for f in range(4, 11)}})
    assert pl._align_cut(read, 10, PREV, NEXT, lo=0, hi=20, delimiters=["_", "-"]) == 4


def test_align_cut_moves_right_when_prev_lingers():
    # 컷 조기: d·d+1이 아직 이전 슬레이트 → 다음이 읽히는 첫 프레임(12)으로.
    read = _mk_read({9: PREV, 10: PREV, 11: PREV, 12: NEXT})
    assert pl._align_cut(read, 10, PREV, NEXT, lo=0, hi=20, delimiters=["_", "-"]) == 12


def test_align_cut_conservative_on_unreadable():
    # 전환 프레임이 흐릿해 판독불가면 판단 근거가 없다 — 원래 컷 유지.
    read = _mk_read({})
    assert pl._align_cut(read, 10, PREV, NEXT, lo=0, hi=20, delimiters=["_", "-"]) == 10


def test_align_cut_stops_left_walk_at_unreadable():
    # 왼쪽 걷기 중 판독불가를 만나면 마지막으로 확인된 다음-프레임에서 멈춘다.
    read = _mk_read({6: NEXT, 7: NEXT, 8: NEXT, 9: NEXT, 10: NEXT})  # 5는 ""
    assert pl._align_cut(read, 10, PREV, NEXT, lo=0, hi=20, delimiters=["_", "-"]) == 6


def test_align_cut_tolerates_fused_misreads():
    # 오독(구분자 유실)도 squash 접두 일치로 같은 쪽으로 분류돼야 한다.
    read = _mk_read({9: "HH0307130_0330AC", 10: "HH03071400010_AC"})
    assert pl._align_cut(read, 10, PREV, NEXT, lo=0, hi=20, delimiters=["_", "-"]) == 10


def test_align_cut_respects_bounds():
    # 이전 런 시작(lo)·다음 런 끝(hi)을 넘어가지 않는다.
    read = _mk_read({f: NEXT for f in range(0, 11)})
    out = pl._align_cut(read, 10, PREV, NEXT, lo=8, hi=20, delimiters=["_", "-"])
    assert out == 9  # lo=8 → 8은 이전 런 시작이라 침범 금지, 9까지가 한계


def test_align_cut_walks_right_even_if_before_unreadable():
    # 직전 프레임이 판독불가(디졸브)여도 컷 프레임이 '이전'으로 읽히면 오른쪽
    # 걷기 — before까지 요구하던 가드가 ±1프레임 잔존을 남겼다(실기 4건).
    read = _mk_read({10: PREV, 11: NEXT})  # 9는 "" (판독불가)
    assert pl._align_cut(read, 10, PREV, NEXT, lo=0, hi=20, delimiters=["_", "-"]) == 11


# ── 지문 유사도 경계 정렬 (_fp_align) ────────────────────────────────────────
# 디졸브의 판독불가 페이드 프레임은 OCR로는 귀속 불가 — 프레임 지문이 이전/다음
# 런 대표 지문 중 어느 쪽에 가까운지의 '플립 지점'이 사람 눈의 경계와 일치한다
# (실기 030_0190→0200: 페이드 2프레임 dist 4823 vs 8044로 이전, 다음 첫 프레임
# 7951 vs 127로 다음 — 경계가 페이드 뒤로 옮겨져야 머리 혼입이 사라진다).

def _mk_fp_env(flip_at):
    import numpy as np
    ref_prev = np.zeros((4, 8), dtype=np.uint8)
    ref_next = np.full((4, 8), 9, dtype=np.uint8)

    def fp_at(f):
        # flip_at 전에는 이전 지문에 가깝게(살짝 노이즈), 이후는 다음 지문에.
        base = ref_prev if f < flip_at else ref_next
        out = base.copy()
        out[0, 0] = 5  # 약간의 노이즈
        return out
    return fp_at, ref_prev, ref_next


def test_fp_align_moves_boundary_to_similarity_flip():
    fp_at, rp, rn = _mk_fp_env(flip_at=13)
    assert pl._fp_align(fp_at, 10, rp, rn, lo=0, hi=30) == 13


def test_fp_align_keeps_exact_boundary():
    fp_at, rp, rn = _mk_fp_env(flip_at=10)
    assert pl._fp_align(fp_at, 10, rp, rn, lo=0, hi=30) == 10


def test_fp_align_moves_left_when_next_starts_earlier():
    fp_at, rp, rn = _mk_fp_env(flip_at=7)
    assert pl._fp_align(fp_at, 10, rp, rn, lo=0, hi=30) == 7


def test_fp_align_none_when_no_flip_in_window():
    import numpy as np
    rp = np.zeros((4, 8), dtype=np.uint8)
    rn = np.full((4, 8), 9, dtype=np.uint8)
    fp_at = lambda f: rp.copy()  # 창 전체가 이전 쪽 — 플립 없음 → 유지(None)
    assert pl._fp_align(fp_at, 10, rp, rn, lo=0, hi=30) is None


def test_fp_align_respects_bounds():
    fp_at, rp, rn = _mk_fp_env(flip_at=2)
    # lo=5 → 5 이전으로는 못 간다(이전 런 침범 금지) — 창 시작부터 next면 lo+1.
    assert pl._fp_align(fp_at, 10, rp, rn, lo=5, hi=30) == 6


# ── fp 이동 OCR 캡 (_clamp_fp_move) ──────────────────────────────────────────
# 유사도 정렬은 판독불가 페이드에는 옳지만, 새 슬레이트가 옛 그림 위에 일찍
# 떠오르는 반대 극성 디졸브에서는 OCR로 이미 '다음'이 읽히는 프레임까지 이전
# 쪽으로 밀어버린다(실기 090_0180 꼬리에 0190 등장). 읽히는 프레임의 소속은
# OCR이 권위 — fp 이동을 OCR 가독성으로 캡한다.

def test_clamp_blocks_right_move_at_readable_next():
    sides = {11: None, 12: "next", 13: None}
    assert pl._clamp_fp_move(lambda f: sides.get(f), 11, 14) == 12


def test_clamp_allows_right_move_over_unreadable():
    assert pl._clamp_fp_move(lambda f: None, 11, 14) == 14


def test_clamp_blocks_left_move_at_readable_prev():
    sides = {8: None, 9: "prev"}
    assert pl._clamp_fp_move(lambda f: sides.get(f), 10, 8) == 10


def test_clamp_allows_left_move_over_unreadable():
    assert pl._clamp_fp_move(lambda f: None, 10, 8) == 8


def test_clamp_noop_when_equal():
    assert pl._clamp_fp_move(lambda f: "next", 10, 10) == 10


def test_text_side_is_case_insensitive():
    # OCR이 v01을 V01로 읽는 순간 대소문자 구분 비교가 '어느 쪽도 아님'을
    # 만들어 OCR 권위가 무력화됐다(실기 090_0180 꼬리 2프레임 잔존).
    assert pl._text_side("HH0307_090_0190_AC_V01",
                         "HH0307_090_0180_AC_v01",
                         "HH0307_090_0190_AC_v01", ["_", "-", "/"]) == "next"
    assert pl._text_side("HH03070900180AC-V01",
                         "HH0307_090_0180_AC_v01",
                         "HH0307_090_0190_AC_v01", ["_", "-", "/"]) == "prev"


# ── 판독불가 블록 프레임 단위 귀속 (_resolve_unreadable_blocks) ──────────────
# 실기 HH0304(2026-07-23): 서로 다른 라벨 사이의 ''(판독불가) 블록이 통째로
# 앞 씬에 붙어 시퀀스 3·씬 48클립에 꼬리/머리 혼입. 텍스트 근거가 전혀 없을 때
# 컷 세기 비율(runs_to_segments)은 애매 밴드(1.1~2.4배)에서 판정 불가 —
# 스캔 단계에서 블록 프레임을 지문 근접+OCR로 직접 귀속한다.

def _A(v):
    import numpy as np
    return np.full(4, v, dtype="uint8")


_DELIMS = ["_", "-", "/"]
_LBL_A = "HH0307_010_0010_AC_v01"
_LBL_B = "HH0307_010_0020_AC_v01"


def _resolve(runs_f, texts, picks, fp_map, reads):
    return pl._resolve_unreadable_blocks(
        runs_f, texts, picks, _DELIMS,
        fp_at=lambda f: fp_map[f], read_frame=lambda f: reads.get(f, ""))


def test_resolve_block_wholly_next_by_fp():
    # 블록 전 프레임이 다음 런 지문에 가까움 + OCR 침묵 → 경계=블록 시작.
    fp = {f: _A(0) for f in range(10)} | {f: _A(1) for f in range(10, 24)}
    runs, texts = _resolve([(0, 10), (10, 14), (14, 24)],
                           [_LBL_A, "", _LBL_B], [5, 11, 19], fp, {})
    assert runs == [(0, 10), (10, 24)]
    assert texts == [_LBL_A, _LBL_B]


def test_resolve_block_internal_flip_splits():
    # 블록 안에서 지문이 prev→next로 뒤집힘 → 그 프레임이 경계.
    fp = ({f: _A(0) for f in range(12)} | {f: _A(1) for f in range(12, 24)})
    runs, texts = _resolve([(0, 10), (10, 14), (14, 24)],
                           [_LBL_A, "", _LBL_B], [5, 11, 19], fp, {})
    assert runs == [(0, 12), (12, 24)]
    assert texts == [_LBL_A, _LBL_B]


def test_resolve_ocr_overrides_fp():
    # 지문은 블록 전체를 next라 하지만 첫 프레임이 prev로 '읽히면' OCR이 이긴다.
    fp = {f: _A(0) for f in range(10)} | {f: _A(1) for f in range(10, 24)}
    runs, texts = _resolve([(0, 10), (10, 14), (14, 24)],
                           [_LBL_A, "", _LBL_B], [5, 11, 19], fp,
                           {10: _LBL_A})
    assert runs == [(0, 11), (11, 24)]
    assert texts == [_LBL_A, _LBL_B]


def test_resolve_ocr_next_pulls_boundary():
    # 지문이 전부 prev여도 블록 프레임이 next로 읽히면 그 프레임부터 next.
    fp = {f: _A(0) for f in range(24)}
    runs, texts = _resolve([(0, 10), (10, 14), (14, 24)],
                           [_LBL_A, "", _LBL_B], [5, 11, 19], fp,
                           {13: _LBL_B})
    assert runs == [(0, 13), (13, 24)]
    assert texts == [_LBL_A, _LBL_B]


def test_resolve_conflicting_ocr_keeps_block():
    # OCR 제약이 모순(앞프레임 next, 뒷프레임 prev)이면 보수적으로 무변경.
    fp = {f: _A(0) for f in range(24)}
    runs, texts = _resolve([(0, 10), (10, 14), (14, 24)],
                           [_LBL_A, "", _LBL_B], [5, 11, 19], fp,
                           {10: _LBL_B, 13: _LBL_A})
    assert runs == [(0, 10), (10, 14), (14, 24)]
    assert texts == [_LBL_A, "", _LBL_B]


def test_resolve_same_label_flanks_untouched():
    # 같은 씬 안의 판독불가 런은 기존 흡수(연속 병합)가 담당 — 건드리지 않는다.
    fp = {f: _A(0) for f in range(24)}
    runs, texts = _resolve([(0, 10), (10, 14), (14, 24)],
                           [_LBL_A, "", _LBL_A], [5, 11, 19], fp, {})
    assert runs == [(0, 10), (10, 14), (14, 24)]
    assert texts == [_LBL_A, "", _LBL_A]


def test_resolve_edge_blocks_untouched():
    # 선두/꼬리 블록(한쪽 이웃 없음)은 기존 규칙(선두 드롭·꼬리 앞씬)에 맡긴다.
    fp = {f: _A(0) for f in range(20)}
    runs, texts = _resolve([(0, 6), (6, 14), (14, 20)],
                           ["", _LBL_A, ""], [3, 9, 17], fp, {})
    assert runs == [(0, 6), (6, 14), (14, 20)]
    assert texts == ["", _LBL_A, ""]


def test_resolve_multi_run_block_and_canonical_flanks():
    # 연속 '' 런 여러 개=한 블록. 플랭크 라벨이 구분자 오독으로만 다르면 같은
    # 씬으로 보고 무변경(canonical 비교).
    fp = {f: _A(0) for f in range(30)}
    runs, texts = _resolve(
        [(0, 10), (10, 12), (12, 14), (14, 30)],
        ["HH0307 010 0010 AC v01", "", "", _LBL_A], [5, 11, 13, 20], fp, {})
    assert runs == [(0, 10), (10, 12), (12, 14), (14, 30)]
    # 다른 라벨이면 하나의 블록으로 귀속된다.
    fp2 = {f: _A(0) for f in range(12)} | {f: _A(1) for f in range(12, 30)}
    runs2, texts2 = _resolve(
        [(0, 10), (10, 12), (12, 14), (14, 30)],
        [_LBL_A, "", "", _LBL_B], [5, 11, 13, 20], fp2, {})
    assert runs2 == [(0, 12), (12, 30)]
    assert texts2 == [_LBL_A, _LBL_B]


@pytest.mark.anyio
async def test_run_scene_scan_fingerprint_resolves_unreadable_block(
        monkeypatch, tmp_path):
    """스캔 전 구간에서 판독불가 블록 귀속이 동작하는지 — 프레임 10에서 다음
    씬(내용)이 시작되지만 14 전까지 OCR이 못 읽는 상황(실기 030→040 패턴).
    수정 전엔 [10,14)가 앞 씬 런에 남아 경계가 14로 늦었다 — 이제 지문
    근접(10부터 다음 런 지문과 동일)이 경계를 10으로 되돌린다."""
    from PIL import Image

    from apps.server.domain.video_captions.fingerprint import frame_boundary_ms

    eid = uuid4()
    workdir = pl.job_dir(eid)
    workdir.mkdir(parents=True)
    (workdir / "burned.mp4").write_bytes(b"v")

    def _img(pattern: int) -> Image.Image:
        im = Image.new("L", (32, 8), 255)
        cols = {0: range(0, 20), 1: range(12, 32), 2: range(14, 32)}[pattern]
        for x in cols:
            for y in range(8):
                im.putpixel((x, y), 0)
        return im

    def fake_extract_fp(ffmpeg, src, out_dir, region, scale_w=160,
                        proc_key=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(30):
            # 0-9=씬A(P0), 10-13=씬B 도입부(P1, 판독불가), 14-29=씬B(P2).
            # P1↔P2 차이(2컬럼=16px)는 임계(15) 위라 14에도 컷이 선다 —
            # 씬B 도입부가 별도 '' 런이 되는 실기 구조 재현.
            p = 0 if i < 10 else (1 if i < 14 else 2)
            _img(p).save(out_dir / f"f_{i + 1:06d}.png")

    def fake_thumbs(ffmpeg, src, out_dir, interval_s, proc_key=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "thumb_00001.jpg").write_bytes(b"j")

    def fake_extract_frame(ffmpeg, src, t_ms, dst, proc_key=None, region=None):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(str(t_ms))

    def fake_extract_at(ffmpeg, src, frame_indices, out_dir, region,
                        proc_key=None, workers=1):
        out_dir.mkdir(parents=True, exist_ok=True)
        out = {}
        for k, n in enumerate(sorted(set(frame_indices)), 1):
            p = out_dir / f"at_{k:05d}.png"
            p.write_text(str(frame_boundary_ms(n, 24.0)))
            out[n] = p
        return out

    b10 = frame_boundary_ms(10, 24.0)
    b14 = frame_boundary_ms(14, 24.0)

    def fake_read(path, delimiters, min_tokens=2, top_frac=0.35):
        t_ms = int(Path(path).read_text())
        if t_ms < b10:
            return "HH0307_010_0010_AC_v01"
        if t_ms < b14:
            return ""  # 도입부 4프레임은 저대비로 판독불가
        return "HH0307_010_0020_AC_v01"

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "video_fps", lambda f, s: 24.0)
    monkeypatch.setattr(pl, "build_scan_source",
                        lambda ffmpeg, src, dst, region, proc_key=None:
                        dst.write_bytes(b"s"))
    monkeypatch.setattr(pl, "extract_fingerprint_frames", fake_extract_fp)
    monkeypatch.setattr(pl, "extract_thumbnails", fake_thumbs)
    monkeypatch.setattr(pl, "extract_frames_at", fake_extract_at)
    monkeypatch.setattr(pl, "extract_frame", fake_extract_frame)
    monkeypatch.setattr(pl, "read_slate_line", fake_read)

    await pl.run_scene_scan_fingerprint(eid)

    data = pl.load_scenes(eid)
    assert data["scanning"] is False and data.get("error") is None
    # '' 도입부 [10,14)가 다음 씬 런에 병합돼 경계가 10(진짜 컷)이어야 한다.
    assert [(r["start_ms"], r["end_ms"], r["text"]) for r in data["runs"]] == [
        (frame_boundary_ms(0, 24.0), b10, "HH0307_010_0010_AC_v01"),
        (b10, frame_boundary_ms(30, 24.0), "HH0307_010_0020_AC_v01"),
    ]


def test_align_cut_right_walk_skips_unreadable():
    # 오른쪽 걷기 중 판독불가 1프레임(디졸브 블러)이 걷기를 끊어 컷이 안 옮겨
    # 지던 실기 잔존(090_0060·020_0250 머리 혼입) — 근거 없는 프레임은 건너
    # 뛰고 '다음이 읽히는 첫 프레임'까지 걷는다. 건너뛴 프레임은 이전 쪽 유지.
    read = _mk_read({10: PREV, 12: NEXT})  # 11은 "" (판독불가)
    assert pl._align_cut(read, 10, PREV, NEXT, lo=0, hi=20, delimiters=["_", "-"]) == 12


def test_align_cut_right_walk_returns_cut_when_no_next_found():
    # 걷는 내내 다음이 안 읽히면(전부 판독불가/이전) 원래 컷 유지.
    read = _mk_read({10: PREV})
    assert pl._align_cut(read, 10, PREV, NEXT, lo=0, hi=20, delimiters=["_", "-"]) == 10


def test_align_cut_probe_cap_covers_long_lingering():
    # 이전 슬레이트가 10+프레임 지속되는 느린 전환(실기 020_0250) — 기존 캡 8은
    # 못 닿았다. 걷기는 텍스트 변경 경계에서만 돌므로 캡 확대 비용은 미미.
    read = _mk_read({**{f: PREV for f in range(10, 22)}, 22: NEXT})
    assert pl._align_cut(read, 10, PREV, NEXT, lo=0, hi=40, delimiters=["_", "-"]) == 22


def test_resolve_ocr_walk_pushes_flip_through_readable_prev():
    # 지문이 블러에 속아 플립을 당겨도(실기 130_0160), 플립 이후 프레임들이
    # '이전'으로 읽히면 걷기가 블록 끝까지 민다 → 블록 전체 이전 귀속 유지.
    fp = {f: _A(0) for f in range(10)} | {f: _A(1) for f in range(10, 24)}
    runs, texts = _resolve([(0, 10), (10, 14), (14, 24)],
                           [_LBL_A, "", _LBL_B], [5, 11, 19], fp,
                           {10: _LBL_A, 11: _LBL_A, 12: _LBL_A, 13: _LBL_A})
    assert runs == [(0, 14), (14, 24)]
    assert texts == [_LBL_A, _LBL_B]


def test_pad_region_expands_and_clamps():
    x, y, w, h = pl._pad_region((0.3, 0.3, 0.2, 0.1))
    assert x < 0.3 and y < 0.3 and w > 0.2 and h > 0.1
    assert x + w <= 1.0 and y + h <= 1.0
    # 원점 근처는 0으로 클램프되고 우하단도 1을 넘지 않는다.
    x2, y2, w2, h2 = pl._pad_region((0.0073, 0.0259, 0.3271, 0.2056))
    assert x2 == 0.0 and y2 == 0.0
    assert 0 < w2 <= 1.0 and 0 < h2 <= 1.0


@pytest.mark.anyio
async def test_run_scene_scan_fingerprint_padded_batch_recovers_text(
        monkeypatch, tmp_path):
    """타이트 구역 판독이 전부 실패한 런도 패딩 배치 재판독이 텍스트를 회수해
    씬으로 살아난다(실기 110_0330~0350: 씬 통째가 이 경로로만 복구)."""
    from PIL import Image

    from apps.server.domain.video_captions.fingerprint import frame_boundary_ms

    eid = uuid4()
    workdir = pl.job_dir(eid)
    workdir.mkdir(parents=True)
    (workdir / "burned.mp4").write_bytes(b"v")
    # 사용자 지정 타이트 구역 — 미지정이면 폴백 밴드(w=1.0)라 패딩 구역과
    # 구분이 안 돼 이 테스트가 패딩 경로를 타지 않는다.
    pl.save_scenes(eid, {"ocr_region": {"x": 0.0073, "y": 0.0259,
                                        "w": 0.3271, "h": 0.2056}})

    def fake_extract_fp(ffmpeg, src, out_dir, region, scale_w=160,
                        proc_key=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(20):
            im = Image.new("L", (32, 8), 255)
            for x in (range(0, 20) if i < 10 else range(12, 32)):
                for y in range(8):
                    im.putpixel((x, y), 0)
            im.save(out_dir / f"f_{i + 1:06d}.png")

    def fake_thumbs(ffmpeg, src, out_dir, interval_s, proc_key=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "thumb_00001.jpg").write_bytes(b"j")

    def fake_extract_frame(ffmpeg, src, t_ms, dst, proc_key=None, region=None):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(f"{t_ms}|{region[2]:.4f}" if region else str(t_ms))

    # 배치 추출이 구역 너비를 파일에 남겨, 판독 페이크가 패딩 여부를 안다.
    def fake_extract_at(ffmpeg, src, frame_indices, out_dir, region,
                        proc_key=None, workers=1):
        out_dir.mkdir(parents=True, exist_ok=True)
        out = {}
        for k, n in enumerate(sorted(set(frame_indices)), 1):
            p = out_dir / f"at_{k:05d}.png"
            p.write_text(f"{frame_boundary_ms(n, 24.0)}|{region[2]:.4f}")
            out[n] = p
        return out

    cut_ms = frame_boundary_ms(10, 24.0)
    # 스캔은 중간본(패딩 크롭) 좌표계로 돈다 — 타이트 구역은 상대 폭이 된다.
    region = (0.0073, 0.0259, 0.3271, 0.2056)
    tight_w = pl._relative_region(region, pl._pad_region(region))[2]

    def fake_read(path, delimiters, min_tokens=2, top_frac=0.35):
        raw = Path(path).read_text().split("|")
        t_ms, w = int(raw[0]), float(raw[1]) if len(raw) > 1 else tight_w
        if t_ms < cut_ms:
            return "HH0307_010_0010_AC_v01"
        # 컷 뒤 씬은 저대비 — 패딩(중간본 전체, w=1.0)에서만 읽힌다.
        return "HH0307_010_0020_AC_v01" if w > tight_w + 0.001 else ""

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "video_fps", lambda f, s: 24.0)
    monkeypatch.setattr(pl, "build_scan_source",
                        lambda ffmpeg, src, dst, region, proc_key=None:
                        dst.write_bytes(b"s"))
    monkeypatch.setattr(pl, "extract_fingerprint_frames", fake_extract_fp)
    monkeypatch.setattr(pl, "extract_thumbnails", fake_thumbs)
    monkeypatch.setattr(pl, "extract_frames_at", fake_extract_at)
    monkeypatch.setattr(pl, "extract_frame", fake_extract_frame)
    monkeypatch.setattr(pl, "read_slate_line", fake_read)

    await pl.run_scene_scan_fingerprint(eid)

    data = pl.load_scenes(eid)
    assert data["scanning"] is False and data.get("error") is None
    assert [(r["start_ms"], r["end_ms"], r["text"]) for r in data["runs"]] == [
        (frame_boundary_ms(0, 24.0), cut_ms, "HH0307_010_0010_AC_v01"),
        (cut_ms, frame_boundary_ms(20, 24.0), "HH0307_010_0020_AC_v01"),
    ]


async def test_run_scene_scan_fingerprint_reports_progress_during_retry(
        monkeypatch, tmp_path):
    """재시도 단계(패딩 배치·개별 시킹)도 진행률을 갱신해야 한다.

    실기: 타이트 판독이 전멸한 소스(슬레이트 '_'가 공백으로 읽히는 쇼)에서
    카운터가 N/N에 닿은 채 재시도만 몇 분 돌았고, 프론트는 200초 무변화를
    '정체'로 보고 "스캔이 진행되지 않습니다"를 띄웠다 — 서버는 멀쩡했다.
    화면은 '슬레이트 판독 중… 2791/2791'에서 굳어 있었다.
    """
    from PIL import Image

    from apps.server.domain.video_captions.fingerprint import frame_boundary_ms

    eid = uuid4()
    workdir = pl.job_dir(eid)
    workdir.mkdir(parents=True)
    (workdir / "burned.mp4").write_bytes(b"v")
    pl.save_scenes(eid, {"ocr_region": {"x": 0.0073, "y": 0.0259,
                                        "w": 0.3271, "h": 0.2056}})

    def fake_extract_fp(ffmpeg, src, out_dir, region, scale_w=160,
                        proc_key=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(20):
            im = Image.new("L", (32, 8), 255)
            for x in (range(0, 20) if i < 10 else range(12, 32)):
                for y in range(8):
                    im.putpixel((x, y), 0)
            im.save(out_dir / f"f_{i + 1:06d}.png")

    def fake_thumbs(ffmpeg, src, out_dir, interval_s, proc_key=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "thumb_00001.jpg").write_bytes(b"j")

    def fake_extract_at(ffmpeg, src, frame_indices, out_dir, region,
                        proc_key=None, workers=1):
        out_dir.mkdir(parents=True, exist_ok=True)
        out = {}
        for k, n in enumerate(sorted(set(frame_indices)), 1):
            p = out_dir / f"at_{k:05d}.png"
            p.write_text(f"{frame_boundary_ms(n, 24.0)}|{region[2]:.4f}")
            out[n] = p
        return out

    def fake_extract_frame(ffmpeg, src, t_ms, dst, proc_key=None, region=None):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(f"{t_ms}|{region[2]:.4f}" if region else str(t_ms))

    region = (0.0073, 0.0259, 0.3271, 0.2056)
    tight_w = pl._relative_region(region, pl._pad_region(region))[2]
    cut_ms = frame_boundary_ms(10, 24.0)
    # 패딩 재판독 시점에 '마지막으로 기록된 진행률'을 관찰한다.
    seen_at_retry: list[int | None] = []

    def fake_read(path, delimiters, min_tokens=2, top_frac=0.35):
        raw = Path(path).read_text().split("|")
        t_ms, w = int(raw[0]), float(raw[1]) if len(raw) > 1 else tight_w
        if w > tight_w + 0.001:   # 패딩(중간본 전체) 판독 = 재시도 단계
            seen_at_retry.append((pl.load_scenes(eid) or {}).get("ocr_done"))
            return ("HH0307_010_0010_AC_v01" if t_ms < cut_ms
                    else "HH0307_010_0020_AC_v01")
        return ""                 # 타이트 판독은 전멸(실기 재현)

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "video_fps", lambda f, s: 24.0)
    monkeypatch.setattr(pl, "build_scan_source",
                        lambda ffmpeg, src, dst, region, proc_key=None:
                        dst.write_bytes(b"s"))
    monkeypatch.setattr(pl, "extract_fingerprint_frames", fake_extract_fp)
    monkeypatch.setattr(pl, "extract_thumbnails", fake_thumbs)
    monkeypatch.setattr(pl, "extract_frames_at", fake_extract_at)
    monkeypatch.setattr(pl, "extract_frame", fake_extract_frame)
    monkeypatch.setattr(pl, "read_slate_line", fake_read)

    await pl.run_scene_scan_fingerprint(eid)

    data = pl.load_scenes(eid)
    assert data["scanning"] is False and data.get("error") is None
    # 재시도 단계에서 두 런을 읽는 동안 진행률이 멈춰 있으면 안 된다.
    assert len(seen_at_retry) >= 2, seen_at_retry
    assert len(set(seen_at_retry)) > 1, (
        f"재시도 내내 진행률이 {seen_at_retry[0]}에 멈춰 있다 — "
        "프론트가 200초 뒤 '스캔이 진행되지 않습니다'로 포기한다")


async def test_run_scene_scan_fingerprint_reports_extraction_stages(
        monkeypatch, tmp_path):
    """추출·컷감지 구간(카운터가 없는 60초대 구간)도 살아있음을 알려야 한다.

    이 구간은 ocr_done이 0에 머물러 프론트 정체 판정의 여유가 3배뿐이다 —
    조금만 느린 환경이면 멀쩡한 스캔이 '진행되지 않습니다'가 된다. 단계 이름과
    산출물 증가 신호(stage_tick)를 흘려 보낸다.
    """
    from PIL import Image

    eid = uuid4()
    workdir = pl.job_dir(eid)
    workdir.mkdir(parents=True)
    (workdir / "burned.mp4").write_bytes(b"v")

    def fake_extract_fp(ffmpeg, src, out_dir, region, scale_w=160,
                        proc_key=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(20):
            im = Image.new("L", (32, 8), 255)
            for x in (range(0, 20) if i < 10 else range(12, 32)):
                for y in range(8):
                    im.putpixel((x, y), 0)
            im.save(out_dir / f"f_{i + 1:06d}.png")

    def fake_thumbs(ffmpeg, src, out_dir, interval_s, proc_key=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "thumb_00001.jpg").write_bytes(b"j")

    stages: list[str] = []
    orig_save = pl.save_scenes

    def spy_save(external_id, data):
        st = data.get("stage")
        if st and (not stages or stages[-1] != st):
            stages.append(st)
        orig_save(external_id, data)

    monkeypatch.setattr(pl, "save_scenes", spy_save)
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "video_fps", lambda f, s: 24.0)
    monkeypatch.setattr(pl, "build_scan_source",
                        lambda ffmpeg, src, dst, region, proc_key=None:
                        dst.write_bytes(b"s"))
    monkeypatch.setattr(pl, "extract_fingerprint_frames", fake_extract_fp)
    monkeypatch.setattr(pl, "extract_thumbnails", fake_thumbs)
    monkeypatch.setattr(pl, "extract_frames_at",
                        lambda *a, **k: {})   # 판독은 이 테스트의 관심 밖
    monkeypatch.setattr(pl, "extract_frame",
                        lambda ffmpeg, src, t_ms, dst, proc_key=None,
                        region=None: dst.write_text("x"))
    monkeypatch.setattr(pl, "read_slate_line", lambda *a, **k: "")

    await pl.run_scene_scan_fingerprint(eid)

    assert pl.load_scenes(eid).get("error") is None
    # 카운터가 없는 구간의 단계들이 순서대로 보고돼야 한다.
    assert stages[:4] == [pl.STAGE_CROP, pl.STAGE_FRAMES,
                          pl.STAGE_THUMBS, pl.STAGE_CUTS], stages


def test_extract_tick_grows_with_output(tmp_path):
    """살아있음 신호는 '산출물이 실제로 늘었는가'로 만든다 — 단순 시계라면
    진짜로 멎은 ffmpeg도 살아있는 것처럼 보여 정체 감지가 죽는다."""
    src = tmp_path / "s.mp4"
    frames = tmp_path / "frames"
    thumbs = tmp_path / "thumbs"
    assert pl._extract_tick(src, frames, thumbs) == 0
    src.write_bytes(b"x" * 4096)
    first = pl._extract_tick(src, frames, thumbs)
    assert first > 0
    frames.mkdir()
    (frames / "f_000001.png").write_bytes(b"p")
    assert pl._extract_tick(src, frames, thumbs) > first


async def test_clear_stale_scan_flags_at_startup(monkeypatch):
    """서버 재시작으로 죽은 진행 플래그를 시작 시 내린다.

    DB 스윕(fail_inflight_video_jobs_at_startup)은 job 상태만 본다. 씬 분할의
    'scanning'은 작업 폴더 JSON에 있고 그 플래그를 내리는 건 작업 자신뿐이라
    (완료·취소·실패), 스캔 도중 서버가 재시작되면 뒤에 도는 작업이 없는데도
    영원히 '실행중'으로 남았다 — 사용자는 취소를 눌러야만 빠져나올 수 있었다.
    """
    monkeypatch.setattr(pl, "_another_instance_is_serving", lambda: False)
    eid = uuid4()
    pl.job_dir(eid).mkdir(parents=True)
    region = {"x": 0.749, "y": 0.9, "w": 0.1823, "h": 0.087}
    pl.save_scenes(eid, {"scanning": True, "method": "fingerprint",
                         "interval_ms": 2000, "ocr_region": region,
                         "ocr_done": 2791, "total_frames": 2791, "frames": []})
    pl.save_refine_status(eid, {"refining": True, "done": 3})
    pl.save_boundary_status(eid, {"checking": True})
    pl.save_export_status(eid, {"exporting": True})

    await pl.clear_stale_scan_flags_at_startup()

    d = pl.load_scenes(eid)
    assert d["scanning"] is False
    assert d["error"], "왜 멈췄는지 사용자에게 보여야 한다"
    # 사용자 설정(구역·방식)은 작업 산출물이 아니므로 보존한다.
    assert d["ocr_region"] == region and d["method"] == "fingerprint"
    assert pl.load_refine_status(eid)["refining"] is False
    assert pl.load_boundary_status(eid)["checking"] is False
    assert pl.load_export_status(eid)["exporting"] is False


async def test_clear_stale_scan_flags_skips_when_another_instance_serves(
        monkeypatch):
    """이중 기동된 비소유 프로세스가 살아있는 인스턴스의 스캔을 죽이면 안 된다
    — DB 스윕과 같은 가드."""
    monkeypatch.setattr(pl, "_another_instance_is_serving", lambda: True)
    eid = uuid4()
    pl.job_dir(eid).mkdir(parents=True)
    pl.save_scenes(eid, {"scanning": True, "frames": []})
    await pl.clear_stale_scan_flags_at_startup()
    assert pl.load_scenes(eid)["scanning"] is True


async def test_clear_stale_scan_flags_leaves_finished_scans(monkeypatch):
    """끝난 스캔의 결과는 건드리지 않는다(에러를 새로 심지 않는다)."""
    monkeypatch.setattr(pl, "_another_instance_is_serving", lambda: False)
    eid = uuid4()
    pl.job_dir(eid).mkdir(parents=True)
    done = {"scanning": False, "frames": [{"t_ms": 0, "text": "A_001"}]}
    pl.save_scenes(eid, done)
    await pl.clear_stale_scan_flags_at_startup()
    assert pl.load_scenes(eid) == done


def test_relative_region_maps_tight_into_padded():
    tight = (0.0073, 0.0259, 0.3271, 0.2056)
    pad = pl._pad_region(tight)
    rel = pl._relative_region(tight, pad)
    # 패딩 원점이 0으로 클램프된 경우 타이트 구역의 절대 오프셋이 그대로 남는다.
    assert 0.0 <= rel[0] and 0.0 <= rel[1]
    assert rel[0] + rel[2] <= 1.0 and rel[1] + rel[3] <= 1.0
    # 상대 폭 = 타이트 폭 / 패딩 폭.
    assert abs(rel[2] - tight[2] / pad[2]) < 1e-9
    # 퇴화 outer는 전체 프레임으로 폴백.
    assert pl._relative_region(tight, (0, 0, 0, 0)) == (0.0, 0.0, 1.0, 1.0)


def test_align_cut_left_walk_starts_over_unreadable_cut_minus_one():
    # 실기 040_0200: 슬레이트만 바뀌는 무컷 전환에서 컷 직전 프레임 판독이
    # 깜박이면 왼쪽 걷기가 시작조차 안 돼 꼬리 혼입 ~22프레임이 남았다.
    # 컷 프레임이 '다음'으로 읽히면 더 왼쪽을 살펴, '다음'으로 확인된 가장
    # 깊은 프레임까지 이동한다(판독불가는 건너뛰되 이동 근거 아님).
    read = _mk_read({13: NEXT, 11: NEXT, 10: PREV})  # 12는 "" (판독불가)
    assert pl._align_cut(read, 13, PREV, NEXT, lo=0, hi=20, delimiters=["_", "-"]) == 11


def test_align_cut_left_walk_no_confirmed_next_keeps_cut():
    # 컷 왼쪽이 전부 판독불가면 이동 근거가 없다 — 컷 유지.
    read = _mk_read({13: NEXT})
    assert pl._align_cut(read, 13, PREV, NEXT, lo=0, hi=20, delimiters=["_", "-"]) == 13


def test_align_cut_left_walk_skips_unreadable_mid_walk():
    # before가 '다음'으로 시작한 걷기도 중간 판독불가를 건너뛰고 더 깊은
    # 확인-다음 프레임까지 간다(기존: 판독불가에서 확장 중단 → 잔존 혼입).
    read = _mk_read({9: NEXT, 7: NEXT, 6: PREV})  # 8은 ""
    assert pl._align_cut(read, 10, PREV, NEXT, lo=0, hi=20, delimiters=["_", "-"]) == 7


async def test_scene_export_flags_error_when_file_not_written(monkeypatch):
    """ffmpeg가 코드 0으로 끝났는데 실제 출력 파일이 없으면(권한·경로·AV 격리 등)
    조용히 '완료'로 넘어가지 않고 error로 표면화해야 한다 — 실기 Windows의
    '카운트만 오르고 파일이 안 생김'을 진단 가능하게(컷 직후 파일 존재 검증)."""
    ext = uuid4()
    pl.save_scenes(ext, {"segments_scene": [
        {"label": "0010", "start_ms": 0, "end_ms": 1000},
        {"label": "0020", "start_ms": 1000, "end_ms": 2000},
    ]})
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "video_fps", lambda *a, **k: 24.0)
    # cut_segment가 '성공'하지만 파일을 만들지 않는 상황을 재현(no-op).
    monkeypatch.setattr(pl, "cut_segment", lambda *a, **k: None)

    written = await pl.run_scene_export(ext, "scene")

    assert written == []  # 파일이 없으니 성공 목록은 비어야 한다
    st = pl.load_export_status(ext)
    assert st is not None and st.get("exporting") is False
    assert st.get("error")  # 조용한 성공이 아니라 에러로 표면화됨


async def test_scene_export_writes_files_and_completes(monkeypatch):
    """정상 경로: cut_segment가 파일을 만들면 검증을 통과하고 완료(error 없음)."""
    ext = uuid4()
    pl.save_scenes(ext, {"segments_scene": [
        {"label": "0010", "start_ms": 0, "end_ms": 1000},
    ]})
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "video_fps", lambda *a, **k: 24.0)

    def _fake_cut(_ffmpeg, _src, dst, *a, **k):
        Path(dst).write_bytes(b"\x00\x01")  # 실제 파일 생성 시늉

    monkeypatch.setattr(pl, "cut_segment", _fake_cut)

    written = await pl.run_scene_export(ext, "scene")

    assert len(written) == 1 and Path(written[0]).exists()
    st = pl.load_export_status(ext)
    assert st and st.get("exporting") is False and not st.get("error")
    assert st.get("done") == 1


async def test_scene_export_partial_keeps_full_list_dedupe_names(monkeypatch, tmp_path):
    """부분 익스포트(indices)는 고른 세그먼트만 굽고, 파일명은 '전체' 목록 dedupe와
    같아야 한다 — 선택분만으로 dedupe하면 중복 라벨의 접미사가 달라져(0010_02 → 0010)
    전체 익스포트가 만든 파일을 갱신하지 못하고 유령 파일이 생긴다."""
    ext = uuid4()
    pl.save_scenes(ext, {"segments_scene": [
        {"label": "0010", "start_ms": 0, "end_ms": 1000},
        {"label": "0010", "start_ms": 1000, "end_ms": 2000},  # 비단조 — 같은 라벨 재등장
        {"label": "0020", "start_ms": 2000, "end_ms": 3000},
    ]})
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "video_fps", lambda *a, **k: 24.0)

    def _fake_cut(_ffmpeg, _src, dst, *a, **k):
        Path(dst).write_bytes(b"\x00\x01")

    monkeypatch.setattr(pl, "cut_segment", _fake_cut)

    written = await pl.run_scene_export(ext, "scene", str(tmp_path), [1, 2])

    assert sorted(Path(p).name for p in written) == ["0010_02.mp4", "0020.mp4"]
    # 고르지 않은 인덱스 0("0010.mp4")은 건드리지 않는다.
    assert sorted(p.name for p in tmp_path.glob("*.mp4")) == ["0010_02.mp4", "0020.mp4"]
    st = pl.load_export_status(ext)
    assert st and st.get("total") == 2 and st.get("done") == 2 and not st.get("error")
