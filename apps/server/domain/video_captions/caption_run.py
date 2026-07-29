"""자막 파이프라인 러너 — 인제스트→추출→전사→번역→검수 대기(run_video_job).

pipeline.py에서 분리(2026-07-29 리팩토링, 동작 변경 0). 취소·세대·직렬화 규약은
job_tasks가 소유한다.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete

from apps.server.db.models import VideoSegment
from apps.server.db.session import AsyncSessionLocal
from .ffmpeg import ensure_preview, extract_audio, locate_ffmpeg, wav_duration_seconds
from .ingest import download_youtube
from .job_store import job_dir
from .job_tasks import (
    _JOB_SEMAPHORE, _bump_generation, _current_generation, _load_job,
    _set_progress, _set_status, _try_set_error,
)
from .transcribe import StaleRunCancelled, transcribe_audio
from .translate import maybe_aclose_translator, translate_segments
from .translate_cli import create_translator

# 로거 이름은 분리 전과 동일하게 유지한다(job_tasks 참조).
logger = logging.getLogger("yeson.video.pipeline")


async def run_video_job(external_id: UUID) -> None:
    # 전역 세마포어로 직렬화 — 획득 전까지 job.status는 'queued'로 남아 UI에
    # '대기 중'으로 표시된다(생성 시 큐잉된 상태 그대로). acquire는 try 밖에서
    # 하고 finally에서 반드시 release한다.
    await _JOB_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    try:
        async with AsyncSessionLocal() as db:
            job = await _load_job(db, external_id)
            source_type, source_ref = job.source_type, job.source_ref
            media_path = job.media_path
            model_name = job.whisper_model
            translate_provider = job.translate_provider
            translate_cli_model = job.translate_cli_model
            job_id = job.id

        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError(
                "ffmpeg를 찾을 수 없습니다. 서버에 ffmpeg 설치 또는 번들이 필요합니다.")

        workdir = job_dir(external_id)
        workdir.mkdir(parents=True, exist_ok=True)

        if source_type == "youtube":
            await _set_status(external_id, "ingesting")
            src, title = await asyncio.to_thread(download_youtube, source_ref, workdir, ffmpeg)
            await _set_status(external_id, "ingesting", media_path=str(src), title=title)
            media_path = str(src)
        if not media_path or not Path(media_path).exists():
            raise RuntimeError("원본 영상 파일이 없습니다.")

        await _set_status(external_id, "extracting")
        src = Path(media_path)
        preview = await asyncio.to_thread(
            ensure_preview, ffmpeg, src, workdir / "preview.mp4",
            proc_key=str(external_id))
        audio = workdir / "audio.wav"
        await asyncio.to_thread(extract_audio, ffmpeg, src, audio,
                                proc_key=str(external_id))
        await _set_status(external_id, "extracting",
                          preview_path=str(preview), audio_path=str(audio))

        await _set_status(external_id, "transcribing")
        loop = asyncio.get_running_loop()
        last_pct = {"v": -1}

        def on_transcribe_progress(frac: float) -> None:
            # 워커 스레드에서 직접 호출된다(asyncio 취소가 닿지 않음). 그 사이
            # 취소·재생성으로 세대가 올랐으면 즉시 예외를 던져 스레드가 남은
            # 전사를 마저 태우지 않고 빠져나가게 한다.
            if generation != _current_generation(external_id):
                raise StaleRunCancelled(external_id)
            pct = max(0, min(100, int(frac * 100)))
            if pct != last_pct["v"]:
                last_pct["v"] = pct
                asyncio.run_coroutine_threadsafe(
                    _set_progress(external_id, pct, generation), loop)

        try:
            en_segments = await asyncio.to_thread(
                transcribe_audio, audio, model_name, on_transcribe_progress)
        except StaleRunCancelled:
            logger.info(
                "video job %s: stale run (generation %d) abandoned during transcription",
                external_id, generation)
            return
        if not en_segments:
            raise RuntimeError("전사 결과가 비어 있습니다 (음성이 감지되지 않음).")

        # audio.wav의 유일한 소비자는 전사다. 끝났으니 굽기 진행률 분모로 쓸
        # 길이만 duration_ms로 보존하고 wav는 즉시 삭제해 디스크를 회수한다.
        duration_ms: int | None = None
        try:
            duration_ms = int(wav_duration_seconds(audio) * 1000)
        except Exception:  # noqa: BLE001 — 길이 계산 실패는 세그먼트 최대 end_ms로 폴백
            logger.exception("failed to read audio duration for job %s", external_id)
        if not duration_ms:
            duration_ms = max((s.end_ms for s in en_segments), default=0) or None
        try:
            audio.unlink(missing_ok=True)
        except OSError:
            logger.exception("failed to delete audio wav for job %s", external_id)

        await _set_status(external_id, "translating")

        async def on_translate_progress(frac: float) -> None:
            await _set_progress(external_id, max(0, min(100, int(frac * 100))), generation)

        translator = create_translator(
            provider=translate_provider, cli_model=translate_cli_model)
        try:
            ko_segments = await translate_segments(
                en_segments, translator, progress_cb=on_translate_progress)
        finally:
            await maybe_aclose_translator(translator)

        async with AsyncSessionLocal() as db:
            await db.execute(delete(VideoSegment).where(VideoSegment.job_id == job_id))
            for en, ko in zip(en_segments, ko_segments):
                db.add(VideoSegment(job_id=job_id, seq=en.seq, start_ms=en.start_ms,
                                    end_ms=en.end_ms, text_en=en.text, text_ko=ko.text))
            await db.commit()

        await _set_status(external_id, "review",
                          duration_ms=duration_ms, audio_path=None)
        logger.info("video job %s ready for review (%d segments)",
                    external_id, len(en_segments))
    except Exception as exc:  # noqa: BLE001 — 파이프라인 최종 방어선
        if generation != _current_generation(external_id):
            # 취소·재생성으로 세대가 올라간 뒤(예: kill_active로 죽은 추출 ffmpeg)
            # 발생한 예외 — 이미 다른 상태(cancelled 등)로 정리됐으므로 error로
            # 덮어쓰지 않는다.
            logger.info(
                "video job %s: stale run (generation %d) failed after cancel — ignoring",
                external_id, generation)
            return
        logger.exception("video job %s failed", external_id)
        await _try_set_error(external_id, str(exc)[:1000])
    finally:
        _JOB_SEMAPHORE.release()
