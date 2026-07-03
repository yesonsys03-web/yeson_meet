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

from sqlalchemy import delete, select

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
