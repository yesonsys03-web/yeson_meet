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
    monkeypatch.setattr(pl, "kill_active", lambda key: killed.append(key) or True)

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
    실제로 그 컷 하나를 찾는지, 런 경계가 frame_mid_ms 규약으로 기록되는지,
    method/runs/frames(호환 샘플)/total_ms/video_fps가 저장되는지 확인한다."""
    from PIL import Image

    from apps.server.domain.video_captions.fingerprint import frame_mid_ms

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

    cut_ms = frame_mid_ms(10, 24.0)

    def fake_read(path, delimiters, min_tokens=2, top_frac=0.35):
        t_ms = int(Path(path).read_text())
        return ("HH0307_010_0010_AC_v01" if t_ms < cut_ms
                else "HH0307_010_0020_AC_v01")

    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(pl, "video_fps", lambda f, s: 24.0)
    monkeypatch.setattr(pl, "extract_fingerprint_frames", fake_extract_fp)
    monkeypatch.setattr(pl, "extract_thumbnails", fake_thumbs)
    monkeypatch.setattr(pl, "extract_frame", fake_extract_frame)
    monkeypatch.setattr(pl, "read_slate_line", fake_read)

    await pl.run_scene_scan_fingerprint(eid)

    data = pl.load_scenes(eid)
    assert data["scanning"] is False and data.get("error") is None
    assert data["method"] == "fingerprint"
    assert data["video_fps"] == 24.0
    assert data["total_ms"] == frame_mid_ms(20, 24.0)
    assert data["runs"] == [
        {"start_ms": frame_mid_ms(0, 24.0), "end_ms": cut_ms,
         "text": "HH0307_010_0010_AC_v01"},
        {"start_ms": cut_ms, "end_ms": frame_mid_ms(20, 24.0),
         "text": "HH0307_010_0020_AC_v01"},
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

    monkeypatch.setattr(pl, "extract_fingerprint_frames", boom)
    await pl.run_scene_scan_fingerprint(eid)
    data = pl.load_scenes(eid)
    assert data["scanning"] is False
    assert data["error"]
    assert data["method"] == "fingerprint"
