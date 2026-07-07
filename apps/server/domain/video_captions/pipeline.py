"""Video caption job orchestration.

Long-running per-job work runs as an asyncio task with its OWN
``AsyncSessionLocal()`` (the request session is closed by then) — same rule as
the report FTS background task. CPU-bound stages go through asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select, update

from apps.server.db.models import VideoJob, VideoSegment
from apps.server.db.session import AsyncSessionLocal
from .ffmpeg import (
    burn_subtitles, ensure_preview, extract_audio, locate_ffmpeg,
    wav_duration_seconds,
)
from .ingest import download_youtube
from .srt import SubSegment, build_force_style, segments_to_srt
from .transcribe import transcribe_audio
from .translate import translate_segments
from .translate_cli import create_translator

logger = logging.getLogger("yeson.video.pipeline")

# 계측되는 단계(전사/번역/굽기)는 단계 진입 시 0에서 시작해 단계 내부에서
# 0→100%로 채워진다 — UI 라벨이 단계명을 보여주므로 절대 % 의미는 없다.
_PROGRESS = {"ingesting": 5, "extracting": 15, "transcribing": 0,
             "translating": 0, "review": 100, "burning": 0, "done": 100}


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

# 자막 메이커가 무한정 쌓이지 않도록 유지할 최근 작업 수 (개수 상한 정책).
RETENTION_KEEP = 10

# strong refs so fire-and-forget tasks are not garbage-collected mid-flight
_tasks: set[asyncio.Task] = set()


def start_task(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def video_jobs_root() -> Path:
    root = os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")
    return Path(root) / "video_jobs"


def job_dir(external_id: UUID | str) -> Path:
    return video_jobs_root() / str(external_id)


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


async def _set_progress(external_id: UUID, pct: int) -> None:
    """단계 내부 진행률(0~100)만 갱신 — status/error는 건드리지 않는다.

    진행률 갱신 실패가 파이프라인 자체를 죽이면 안 되므로 예외는 로그만.
    """
    try:
        async with AsyncSessionLocal() as db:
            job = await _load_job(db, external_id)
            job.progress = pct
            await db.commit()
    except Exception:  # noqa: BLE001 — 진행률은 부가 정보, 실패해도 파이프라인은 계속
        logger.exception("failed to update progress for job %s", external_id)


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
            ensure_preview, ffmpeg, src, workdir / "preview.mp4")
        audio = workdir / "audio.wav"
        await asyncio.to_thread(extract_audio, ffmpeg, src, audio)
        await _set_status(external_id, "extracting",
                          preview_path=str(preview), audio_path=str(audio))

        await _set_status(external_id, "transcribing")
        loop = asyncio.get_running_loop()
        last_pct = {"v": -1}

        def on_transcribe_progress(frac: float) -> None:
            pct = max(0, min(100, int(frac * 100)))
            if pct != last_pct["v"]:
                last_pct["v"] = pct
                asyncio.run_coroutine_threadsafe(_set_progress(external_id, pct), loop)

        en_segments = await asyncio.to_thread(
            transcribe_audio, audio, model_name, on_transcribe_progress)
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
            await _set_progress(external_id, max(0, min(100, int(frac * 100))))

        translator = create_translator(
            provider=translate_provider, cli_model=translate_cli_model)
        ko_segments = await translate_segments(
            en_segments, translator, progress_cb=on_translate_progress)

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


async def _prune_pre_delete_hook(candidate_ids: list[int]) -> None:
    """프루닝의 SELECT와 DELETE 사이 지점 (기본 no-op). 테스트가 여기서 상태
    전이(review→burning)를 주입해 DELETE 시점의 상태 재확인 가드를 검증한다."""
    return None


async def prune_old_video_jobs(keep: int = RETENTION_KEEP) -> int:
    """가장 최근 ``keep``개만 남기고 오래된 영상 작업을 삭제한다 (작업 폴더 + DB 행).

    자막 메이커 작업은 원본/preview/burned mp4를 작업 폴더에 쌓으므로 정리하지
    않으면 무한정 누적된다. 서버 시작 시와 새 작업 생성 직후 호출해 개수를
    상한으로 유지한다. 진행 중(in-flight) 작업은 아무리 오래돼도 절대 지우지
    않는다 — 실행 중인 굽기/전사의 입력 파일을 없애면 안 되기 때문.
    """
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(VideoJob.id, VideoJob.status).order_by(
                    VideoJob.created_at.desc(), VideoJob.id.desc())
            )).all()
            candidate_ids = [r.id for r in rows[keep:]
                             if r.status not in _INFLIGHT_STATUSES]
            if not candidate_ids:
                return 0
            await _prune_pre_delete_hook(candidate_ids)
            # 삭제 시점에 상태를 원자적으로 재확인한다. SELECT와 DELETE 사이에
            # review→burning으로 전이한 작업(동시에 굽기가 시작된 경우)은 지우지
            # 않는다 — 그 폴더/행을 지우면 진행 중인 run_burn_job이 깨진다. Core
            # 벌크 삭제라 동시 프루닝 두 개가 겹쳐도 StaleDataError가 나지 않고,
            # 실제로 삭제된 행만 RETURNING으로 받아 그 폴더만 정리한다.
            deleted = (await db.execute(
                delete(VideoJob)
                .where(VideoJob.id.in_(candidate_ids),
                       VideoJob.status.not_in(_INFLIGHT_STATUSES))
                .returning(VideoJob.external_id)
            )).all()
            await db.commit()
        for row in deleted:
            shutil.rmtree(job_dir(row.external_id), ignore_errors=True)
        if deleted:
            logger.info("retention: pruned %d old video job(s) (keep=%d)",
                        len(deleted), keep)
        return len(deleted)
    except Exception:  # noqa: BLE001 — fire-and-forget 태스크로도 호출되므로 삼키고 로그
        logger.exception("video-job retention prune failed")
        return 0


async def prune_old_video_jobs_at_startup() -> int:
    """서버 시작 시 리텐션 프루닝 — in-flight 스윕과 동일한 '다른 인스턴스가
    서빙 중' 가드로 보호한다.

    이중 기동된 비소유 프로세스(uvicorn lifespan이 포트 바인딩보다 먼저 도는)가
    살아있는 인스턴스의 작업 폴더/DB 행을 지운 뒤 'address already in use'로
    죽는 것을 막는다. 런타임 작업 생성 시 호출되는 prune_old_video_jobs()는
    자기 자신이 이미 포트를 점유하고 있어 이 가드를 쓰면 항상 스킵되므로,
    가드는 startup 경로에만 둔다.
    """
    if _another_instance_is_serving():
        logger.warning(
            "startup retention prune skipped: another instance is already serving")
        return 0
    return await prune_old_video_jobs()


async def run_burn_job(external_id: UUID, position: str, margin_v: int,
                       font_size: int, color: str = "#FFFFFF") -> None:
    try:
        await _set_status(external_id, "burning")
        async with AsyncSessionLocal() as db:
            job = await _load_job(db, external_id)
            media_path = job.media_path
            audio_path = job.audio_path
            duration_ms = job.duration_ms
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

        # 진행률 분모: 전사 단계가 저장한 duration_ms를 우선 사용. audio.wav는 이미
        # 삭제됐으므로, wav 폴백은 duration_ms 이전에 만들어진 옛 작업에만 해당한다.
        duration: float | None = (duration_ms / 1000) if duration_ms else None
        if not duration and audio_path and Path(audio_path).exists():
            try:
                duration = wav_duration_seconds(Path(audio_path))
            except Exception:  # noqa: BLE001 — 진행률 분모 실패는 진행바만 포기
                logger.exception("failed to read audio duration for job %s", external_id)
        if not duration:
            duration = max((s.end_ms for s in segments), default=0) / 1000 or None

        workdir = job_dir(external_id)
        workdir.mkdir(parents=True, exist_ok=True)
        srt_path = workdir / "subs.srt"
        srt_path.write_text(segments_to_srt(segments), encoding="utf-8")
        burned = workdir / "burned.mp4"
        style = build_force_style(position, margin_v, font_size, color)

        progress_cb = None
        if duration:
            loop = asyncio.get_running_loop()
            last_pct = {"v": -1}

            def on_burn_progress(seconds: float, _duration: float = duration) -> None:
                pct = max(0, min(100, int(seconds / _duration * 100)))
                if pct != last_pct["v"]:
                    last_pct["v"] = pct
                    asyncio.run_coroutine_threadsafe(_set_progress(external_id, pct), loop)

            progress_cb = on_burn_progress

        await asyncio.to_thread(
            burn_subtitles, ffmpeg, Path(media_path), srt_path, burned, style, progress_cb)
        await _set_status(external_id, "done", burned_path=str(burned))
    except Exception as exc:  # noqa: BLE001
        logger.exception("burn job %s failed", external_id)
        await _try_set_error(external_id, str(exc)[:1000])
