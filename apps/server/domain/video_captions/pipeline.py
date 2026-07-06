"""Video caption job orchestration.

Long-running per-job work runs as an asyncio task with its OWN
``AsyncSessionLocal()`` (the request session is closed by then) — same rule as
the report FTS background task. CPU-bound stages go through asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select, update

from apps.server.db.models import VideoJob, VideoSegment
from apps.server.db.session import AsyncSessionLocal
from .ffmpeg import burn_subtitles, ensure_preview, extract_audio, locate_ffmpeg
from .ingest import download_youtube
from .srt import SubSegment, build_force_style, segments_to_srt
from .transcribe import transcribe_audio
from .translate import GeminiFlashTranslator, translate_segments

logger = logging.getLogger("yeson.video.pipeline")

_PROGRESS = {"ingesting": 10, "extracting": 25, "transcribing": 40,
             "translating": 75, "review": 90, "burning": 95, "done": 100}


def _another_instance_is_serving() -> bool:
    """이미 같은 포트를 서빙 중인 인스턴스가 있으면 True.

    uvicorn은 lifespan startup을 소켓 바인딩보다 먼저 실행한다. 이중 기동된
    두 번째 프로세스는 곧 'address already in use'로 죽는데, 그 전에 sweep이
    돌면 살아있는 인스턴스의 진행 중 작업을 오판한다 — 그 경우 sweep을 건너뛴다.
    """
    import socket

    port = int(os.environ.get("PORT", "8000"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

_INFLIGHT_STATUSES = ("queued", "ingesting", "extracting", "transcribing",
                      "translating", "burning")

# strong refs so fire-and-forget tasks are not garbage-collected mid-flight
_tasks: set[asyncio.Task] = set()


def start_task(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def job_dir(external_id: UUID | str) -> Path:
    root = os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")
    return Path(root) / "video_jobs" / str(external_id)


async def _load_job(db, external_id: UUID) -> VideoJob:
    return (await db.execute(
        select(VideoJob).where(VideoJob.external_id == external_id)
    )).scalar_one()


async def _set_status(external_id: UUID, status: str, *, error: str | None = None,
                      **fields) -> None:
    async with AsyncSessionLocal() as db:
        job = await _load_job(db, external_id)
        job.status = status
        job.progress = _PROGRESS.get(status, job.progress)
        job.error = error
        for key, value in fields.items():
            setattr(job, key, value)
        await db.commit()


async def _try_set_error(external_id: UUID, message: str) -> None:
    try:
        await _set_status(external_id, "error", error=message)
    except Exception:  # noqa: BLE001 — 상태 기록 실패는 로그만 남기고 삼킨다 (최종 방어선의 방어선)
        logger.exception("failed to record error status for job %s", external_id)


async def run_video_job(external_id: UUID) -> None:
    try:
        async with AsyncSessionLocal() as db:
            job = await _load_job(db, external_id)
            source_type, source_ref = job.source_type, job.source_ref
            media_path = job.media_path
            model_name = job.whisper_model
            job_id = job.id

        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError(
                "ffmpeg를 찾을 수 없습니다. 서버에 ffmpeg 설치 또는 번들이 필요합니다.")

        workdir = job_dir(external_id)
        workdir.mkdir(parents=True, exist_ok=True)

        if source_type == "youtube":
            await _set_status(external_id, "ingesting")
            src, title = await asyncio.to_thread(download_youtube, source_ref, workdir)
            await _set_status(external_id, "ingesting", media_path=str(src), title=title)
            media_path = str(src)
        if not media_path or not Path(media_path).exists():
            raise RuntimeError("원본 영상 파일이 없습니다.")

        await _set_status(external_id, "extracting")
        src = Path(media_path)
        preview = await asyncio.to_thread(
            ensure_preview, ffmpeg, src, workdir / "preview.mp4")
        audio = workdir / "audio.wav"
        await asyncio.to_thread(extract_audio, ffmpeg, src, audio)
        await _set_status(external_id, "extracting",
                          preview_path=str(preview), audio_path=str(audio))

        await _set_status(external_id, "transcribing")
        en_segments = await asyncio.to_thread(transcribe_audio, audio, model_name)
        if not en_segments:
            raise RuntimeError("전사 결과가 비어 있습니다 (음성이 감지되지 않음).")

        await _set_status(external_id, "translating")
        ko_segments = await translate_segments(en_segments, GeminiFlashTranslator())

        async with AsyncSessionLocal() as db:
            await db.execute(delete(VideoSegment).where(VideoSegment.job_id == job_id))
            for en, ko in zip(en_segments, ko_segments):
                db.add(VideoSegment(job_id=job_id, seq=en.seq, start_ms=en.start_ms,
                                    end_ms=en.end_ms, text_en=en.text, text_ko=ko.text))
            await db.commit()

        await _set_status(external_id, "review")
        logger.info("video job %s ready for review (%d segments)",
                    external_id, len(en_segments))
    except Exception as exc:  # noqa: BLE001 — 파이프라인 최종 방어선
        logger.exception("video job %s failed", external_id)
        await _try_set_error(external_id, str(exc)[:1000])


async def fail_inflight_video_jobs_at_startup() -> None:
    """서버 재시작으로 중단된 작업을 error로 정리 — end_live_sessions_at_startup과 같은 취지.

    영상 자막 파이프라인은 프로세스 메모리상의 asyncio task로 진행되므로,
    서버가 재시작되면 진행 중이던 job은 상태만 남고 다시 이어받을 코드가
    없다 — 영구 좀비로 남아 큐를 막는다. 재시작 직후 in-flight 상태를 모두
    error로 정리해 사용자가 삭제 후 재시도할 수 있게 한다.
    """
    if _another_instance_is_serving():
        logger.warning(
            "startup video-job sweep skipped: another instance is already serving")
        return
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(VideoJob)
            .where(VideoJob.status.in_(_INFLIGHT_STATUSES))
            .values(status="error", error="서버 재시작으로 작업이 중단되었습니다. 삭제 후 다시 시도하세요.")
        )
        await db.commit()
    if result.rowcount:
        logger.info("startup sweep: %d in-flight video job(s) marked error", result.rowcount)


async def run_burn_job(external_id: UUID, position: str, margin_v: int,
                       font_size: int) -> None:
    try:
        await _set_status(external_id, "burning")
        async with AsyncSessionLocal() as db:
            job = await _load_job(db, external_id)
            media_path = job.media_path
            rows = (await db.execute(
                select(VideoSegment).where(VideoSegment.job_id == job.id)
                .order_by(VideoSegment.seq)
            )).scalars().all()
            segments = [SubSegment(r.seq, r.start_ms, r.end_ms, r.text_ko) for r in rows]

        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")
        if not segments:
            raise RuntimeError("구울 자막 세그먼트가 없습니다.")

        workdir = job_dir(external_id)
        workdir.mkdir(parents=True, exist_ok=True)
        srt_path = workdir / "subs.srt"
        srt_path.write_text(segments_to_srt(segments), encoding="utf-8")
        burned = workdir / "burned.mp4"
        style = build_force_style(position, margin_v, font_size)
        await asyncio.to_thread(
            burn_subtitles, ffmpeg, Path(media_path), srt_path, burned, style)
        await _set_status(external_id, "done", burned_path=str(burned))
    except Exception as exc:  # noqa: BLE001
        logger.exception("burn job %s failed", external_id)
        await _try_set_error(external_id, str(exc)[:1000])
