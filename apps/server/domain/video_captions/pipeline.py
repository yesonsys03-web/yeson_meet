"""Video caption job orchestration.

Long-running per-job work runs as an asyncio task with its OWN
``AsyncSessionLocal()`` (the request session is closed by then) — same rule as
the report FTS background task. CPU-bound stages go through asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select, update

from apps.server.db.models import VideoJob, VideoSegment
from apps.server.db.session import AsyncSessionLocal
from . import gpu_pack
from .ffmpeg import (
    burn_subtitles, cut_segment, ensure_preview, extract_audio, extract_frame,
    extract_fingerprint_frames, extract_frames, extract_frames_at,
    extract_thumbnails, kill_active, locate_ffmpeg, video_fps,
    wav_duration_seconds,
)
from .fingerprint import (
    FADE_WINDOW, detect_cuts_with_fades, diff_series, frame_boundary_ms,
    frame_runs, load_fingerprint, stable_frame,
)
from .ingest import download_youtube
from .scene_split import (
    FrameSample, SceneRun, SlateRule, build_label, canonicalize_texts,
    compute_boundaries, dedupe_labels, hold_keys, label_matches,
    runs_to_segments, tokenize,
)
from .slate_ocr import read_slate_line
from .srt import SubSegment, build_force_style, segments_to_srt
from .transcribe import StaleRunCancelled, transcribe_audio
from .translate import maybe_aclose_translator, translate_segments
from .translate_cli import create_translator

logger = logging.getLogger("yeson.video.pipeline")

# 계측되는 단계(전사/번역/굽기)는 단계 진입 시 0에서 시작해 단계 내부에서
# 0→100%로 채워진다 — UI 라벨이 단계명을 보여주므로 절대 % 의미는 없다.
# queued: 0 — 재생성(rebuild) 직후 목록에 옛 진행률(예: 77%)이 잠깐이라도
# 남지 않도록 대기 전환 시 명시적으로 리셋한다.
_PROGRESS = {"queued": 0, "ingesting": 5, "extracting": 15, "transcribing": 0,
             "translating": 0, "review": 100, "burning": 0, "done": 100}

# StaleRunCancelled(취소·재생성 감지용 예외)는 pipeline↔transcribe 순환 임포트를
# 피해 transcribe.py에 정의돼 있고, 전사·굽기 진행 콜백이 공용으로 던진다.


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
RETENTION_KEEP = 30

# strong refs so fire-and-forget tasks are not garbage-collected mid-flight
_tasks: set[asyncio.Task] = set()

# 영상 작업 직렬화: 다중 파일/폴더 배치를 한꺼번에 올려도 서버는 한 번에 하나씩만
# 처리한다. whisper 전사는 CPU 집약적이라 동시에 여러 개 돌리면 서로 경합해 모두
# 느려진다 — 세마포어(1)로 순차 처리해 배치 순서를 보장하고 자원 경합을 없앤다.
_JOB_SEMAPHORE = asyncio.Semaphore(1)

# 굽기 직렬화: '선택 굽기 (N개)'는 클라이언트가 burn POST를 연달아 쏘고 엔드포인트는
# 즉시 반환하므로, 세마포어가 없으면 ffmpeg 인코딩 N개가 동시에 돌아 CPU/GPU를
# 포화시킨다. 전사 세마포어와 분리한 이유: 공유하면 재생성/재굽기 1건이 긴 배치
# 전사 뒤에 줄을 서는 UX 퇴행이 생긴다 — 동시 상한은 "전사 1 + 굽기 1"로 고정된다.
_BURN_SEMAPHORE = asyncio.Semaphore(1)


def start_task(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# 작업(external_id)별 파이프라인 태스크 레지스트리. 작업을 삭제할 때 실행 중인
# 태스크를 취소해 세마포어를 즉시 반납하고, 지워진 행/파일에 대한 NoResultFound·
# FileNotFound 에러를 반복하는 좀비 태스크를 없앤다.
_job_tasks: dict[str, asyncio.Task] = {}

# 작업(external_id)별 "실행 세대" — DB 스키마 변경 없이 메모리에만 둔다. task.cancel()
# 은 워커 스레드(전사/굽기)에는 닿지 않으므로, 취소·재생성 후에도 유령 스레드가 지연
# 스케줄한 진행률 쓰기가 도착할 수 있다. 그 쓰기가 캡처한 세대가 현재 세대와 다르면
# (run 시작·취소·재생성마다 세대가 오른다) 스테일로 간주해 버린다.
_job_generation: dict[str, int] = {}


def _bump_generation(external_id: UUID | str) -> int:
    key = str(external_id)
    gen = _job_generation.get(key, 0) + 1
    _job_generation[key] = gen
    return gen


def _current_generation(external_id: UUID | str) -> int:
    return _job_generation.get(str(external_id), 0)


def start_job_task(external_id: UUID, coro) -> None:
    """external_id로 추적되는 파이프라인 태스크를 시작한다(취소 가능하도록)."""
    key = str(external_id)
    task = asyncio.create_task(coro)
    _tasks.add(task)
    _job_tasks[key] = task

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if _job_tasks.get(key) is t:
            _job_tasks.pop(key, None)

    task.add_done_callback(_done)


def cancel_job_task(external_id: UUID) -> bool:
    """실행 중인 작업 파이프라인 태스크가 있으면 취소한다(삭제 시 호출).

    run_video_job/run_burn_job은 finally에서 세마포어를 반납하므로 취소 시 즉시
    반납된다(대기 중인 다음 작업이 진행). 이미 끝났거나 없으면 False.

    태스크 존재 여부와 무관하게 세대를 올린다 — 이미 끝난(그러나 워커 스레드가
    아직 진행률을 지연 스케줄 중인) 실행의 유령 쓰기도 무효화해야 하기 때문.

    세대를 올린 직후 활성 ffmpeg 프로세스(추출·굽기)를 즉시 kill한다 — task.cancel()
    은 워커 스레드에 닿지 않고, 다음 진행률 라인이 올 때까지(수 초) 기다리지 않기
    위함. run_video_job/run_burn_job은 이 kill로 발생하는 예외를 세대 확인 후
    조용히 삼켜 'cancelled' 상태를 'error'로 덮어쓰지 않는다.
    """
    _bump_generation(external_id)
    kill_active(str(external_id))
    task = _job_tasks.get(str(external_id))
    if task is not None and not task.done():
        task.cancel()
        return True
    return False


def video_jobs_root() -> Path:
    root = os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")
    return Path(root) / "video_jobs"


def job_dir(external_id: UUID | str) -> Path:
    return video_jobs_root() / str(external_id)


def scenes_json_path(external_id: UUID | str) -> Path:
    return job_dir(external_id) / "scenes.json"


def save_scenes(external_id: UUID | str, data: dict) -> None:
    path = scenes_json_path(external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_scenes(external_id: UUID | str) -> dict | None:
    path = scenes_json_path(external_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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


async def _set_progress(external_id: UUID, pct: int, generation: int) -> None:
    """단계 내부 진행률(0~100)만 갱신 — status/error는 건드리지 않는다.

    generation은 호출자(진행률 콜백)가 자기 run 시작 시점에 캡처해 넘긴 값.
    현재 세대와 다르면(그 사이 취소·재생성이 있었음) 유령 쓰기이므로 조용히
    버린다. 진행률 갱신 실패가 파이프라인 자체를 죽이면 안 되므로 예외는 로그만.
    """
    if generation != _current_generation(external_id):
        return
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
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
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
                # 워커 스레드에서 직접 호출된다. 취소·재생성으로 세대가 올랐으면
                # 예외를 던져 ffmpeg을 조기 종료시킨다(_burn_once가 kill 처리).
                if generation != _current_generation(external_id):
                    raise StaleRunCancelled(external_id)
                pct = max(0, min(100, int(seconds / _duration * 100)))
                if pct != last_pct["v"]:
                    last_pct["v"] = pct
                    asyncio.run_coroutine_threadsafe(
                        _set_progress(external_id, pct, generation), loop)

            progress_cb = on_burn_progress

        # 굽기는 GPU 토글과 무관하게 항상 CPU(libx264) — RTX 2080 실측(2026-07-10)
        # 에서 NVENC(p5, GPU 100% 포화)보다 x264 veryfast가 더 빨랐다. 병목이
        # CPU쪽 디코드+libass 자막 렌더링이라 GPU 인코더 이득이 없고 프레임 복사
        # 오버헤드만 붙는다. GPU 토글은 전사(CUDA) 전용. (이전엔 토글을 공유해
        # "전사 GPU + 굽기 CPU" 최적 조합을 선택할 수 없었다.)
        use_gpu = False
        try:
            await asyncio.to_thread(
                burn_subtitles, ffmpeg, Path(media_path), srt_path, burned, style,
                progress_cb, proc_key=str(external_id), use_gpu=use_gpu)
        except StaleRunCancelled:
            # 취소된 실행 — 이미 cancelled로 마킹된 상태를 error로 덮어쓰지 않는다.
            logger.info("burn job %s: stale run (generation %d) cancelled early",
                        external_id, generation)
            return
        await _set_status(external_id, "done", burned_path=str(burned))
    except Exception as exc:  # noqa: BLE001
        if generation != _current_generation(external_id):
            # kill_active로 죽은 ffmpeg가 FfmpegError로 표면화된 경우 등 — 세대가
            # 이미 넘어갔으면(취소·재생성) 이미 다른 상태로 정리됐으므로 error로
            # 덮어쓰지 않는다.
            logger.info(
                "burn job %s: stale run (generation %d) failed after cancel — ignoring",
                external_id, generation)
            return
        logger.exception("burn job %s failed", external_id)
        await _try_set_error(external_id, str(exc)[:1000])
    finally:
        _BURN_SEMAPHORE.release()


def build_scene_data(samples: list[FrameSample], rule_dict: dict,
                     total_ms: int, min_ms: int = 2000) -> dict:
    """프레임 샘플 + 규칙 → scenes.json 본문(양 모드 경계 포함). 순수 함수."""
    rule = SlateRule(
        delimiters=rule_dict.get("delimiters", ["_", " ", "-"]),
        seq_tokens=rule_dict["seq_tokens"],
        scene_tokens=rule_dict.get("scene_tokens", []),
    )
    # 샘플 간격 — 경계 중앙정렬·1샘플 흡수 판정에 쓴다(스캔은 균일 간격).
    interval_ms = (samples[1].t_ms - samples[0].t_ms) if len(samples) >= 2 else 2000
    scene_keyed = hold_keys(samples, rule, "scene")
    seq_keyed = hold_keys(samples, rule, "sequence")
    # 씬 모드: 경계 중앙정렬만(짧은 진짜 컷이 있을 수 있어 1샘플 흡수는 안 함).
    seg_scene = compute_boundaries(scene_keyed, total_ms, min_ms,
                                   interval_ms=interval_ms)
    # 시퀀스 모드: 중앙정렬 + 내부 1샘플 고립 흡수(오독 제거 — 시퀀스는 1샘플일 리 없음).
    seg_seq = compute_boundaries(seq_keyed, total_ms, min_ms,
                                 interval_ms=interval_ms, absorb_single=True)
    return {
        "rule": rule_dict,
        "frames": [{"t_ms": s.t_ms, "text": s.text} for s in samples],
        "segments_scene": [
            {"label": s.label, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in seg_scene],
        "segments_sequence": [
            {"label": s.label, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in seg_seq],
    }


# 슬레이트 스캔 기본 규칙 — 사용자가 규칙 지정 전, 첫 스캔은 규칙 없이 프레임
# 텍스트만 수집한다(경계는 규칙 확정 시 계산한다). 구분자는 관측된 두 포맷 커버.
# 기본 구분자에서 공백은 제외한다(`_`, `-`만). 슬레이트 필드 구분은 `_`/`-`이고
# 공백은 필드 "안"에 들어가는 경우가 많다(예: "Seq 11B", "Panel 3"). OCR이 같은
# 슬레이트에서 공백을 들쭉날쭉 읽으면("Seq01A" vs "Seq 11B") 공백 분해 시 토큰
# 인덱스가 프레임마다 어긋나 고정 인덱스 규칙이 깨진다(실기 관측). 공백을 필드
# 구분자로 쓰는 슬레이트는 규칙의 delimiters로 명시 지정한다(UI 토글).
# "/"는 OCR이 "_"를 어긋 읽는 상수적 오독(실기 075/0080·120/0010) —
# 구분자로 취급하면 오독 텍스트도 같은 토큰으로 쪼개져 키가 정렬된다.
_DEFAULT_DELIMS = ["_", "-", "/"]

# 스캔 프레임 샘플 간격(초). 슬레이트는 한 샷 내내 떠 있으므로 촘촘히 볼 필요가
# 없다. 2초면 경계 정밀도는 충분하고 긴 영상의 OCR 프레임 수를 절반으로 줄인다
# (22분 영상 실측: 1초=1316프레임 → 2초=658프레임). 경계는 필름스트립에서 수동
# 조정 가능하고, 후속으로 경계 근처만 프레임 단위 재탐색할 수 있다.
_SCAN_INTERVAL_S = 2.0


def load_ocr_region(external_id: UUID | str) -> tuple | None:
    """저장된 OCR 영역(비율 x,y,w,h) — 사용자가 드래그로 지정한 슬레이트 구역.
    없으면 None(전체 프레임 + 상단 밴드 가정, 기존 동작)."""
    data = load_scenes(external_id) or {}
    r = data.get("ocr_region")
    if not r:
        return None
    try:
        return (float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"]))
    except (KeyError, TypeError, ValueError):
        return None


# 크롭된 입력에서는 상단 밴드 가정을 쓰지 않는다 — 크롭 자체가 영역 필터다.
def _band_for(region: tuple | None) -> float:
    return 1.0 if region else _TOP_BAND_DEFAULT


_TOP_BAND_DEFAULT = 0.35


async def run_scene_scan(external_id: UUID,
                         interval_s: float = _SCAN_INTERVAL_S) -> None:
    """burned.mp4에서 프레임을 추출·OCR해 프레임별 슬레이트 텍스트를 모아
    scenes.json에 저장한다. 경계는 규칙 확정(/scenes/rule) 때 계산한다.

    긴 영상은 OCR이 오래 걸리므로 진행률을 scenes.json에 증분 기록한다
    (`scanning`/`total_frames`/`ocr_done`) — 프론트가 폴링하며 표시하고, 완료 시
    `scanning=False`로 전환한다. 진짜 실패 시 `error`를 기록해 프론트 폴링을
    멈춘다. 취소(세대 변경)는 아무 것도 기록하지 않는다(다음 실행이 덮어씀).
    스캔은 굽기와 세마포어를 공유해 배타적으로 돈다."""
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    try:
        workdir = job_dir(external_id)
        burned = workdir / "burned.mp4"
        if not burned.exists():
            raise RuntimeError("굽기 완료본(burned.mp4)이 없습니다.")
        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")

        frames_dir = workdir / "scene_frames"
        thumbs_dir = workdir / "scene_thumbs"
        # 이전 스캔 잔여 제거
        for d in (frames_dir, thumbs_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

        interval_ms = int(interval_s * 1000)
        # 썸네일 간격은 스캔(OCR) 간격과 분리한다 — 스캔을 0.25s로 촘촘히 떠도
        # 썸네일까지 그러면 필름스트립이 수천 칸이 된다. 썸네일은 최소 2s로 성기게.
        thumb_interval_s = max(2.0, interval_s)
        thumb_interval_ms = int(thumb_interval_s * 1000)
        # 사용자가 지정한 슬레이트 구역 — scenes.json을 덮어쓰기 전에 읽어두고,
        # 이후 모든 저장에 되실어 재스캔해도 지정이 사라지지 않게 한다.
        region = load_ocr_region(external_id)
        band = _band_for(region)
        region_out = ({"x": region[0], "y": region[1],
                       "w": region[2], "h": region[3]} if region else None)

        def _prog(extra: dict) -> dict:
            return {"scanning": True, "interval_ms": interval_ms,
                    "thumb_interval_ms": thumb_interval_ms,
                    "ocr_region": region_out, **extra}

        def _work() -> tuple[list[FrameSample], int]:
            extract_frames(ffmpeg, burned, frames_dir, interval_s,
                           proc_key=str(external_id), region=region)
            extract_thumbnails(ffmpeg, burned, thumbs_dir, thumb_interval_s,
                               proc_key=str(external_id))
            thumb_count = len(list(thumbs_dir.glob("thumb_*.jpg")))
            pngs = sorted(frames_dir.glob("frame_*.png"))
            total = len(pngs)
            # 진행률 초기화 — 긴 영상은 OCR이 오래 걸려 프론트가 폴링하며 표시한다.
            save_scenes(external_id, _prog({"total_frames": total, "ocr_done": 0,
                                            "frames": [], "thumb_count": thumb_count}))
            samples: list[FrameSample] = []
            # 촘촘한 스캔은 OCR 호출이 많으므로 병렬화한다(정밀화와 같은 이유·설정).
            def _read(ipng):
                i, png = ipng
                if generation != _current_generation(external_id):
                    raise StaleRunCancelled(external_id)
                return i, read_slate_line(png, _DEFAULT_DELIMS, top_frac=band)

            texts: dict[int, str] = {}
            done = 0
            with ThreadPoolExecutor(max_workers=_refine_workers()) as pool:
                for i, text in pool.map(_read, enumerate(pngs)):
                    texts[i] = text
                    done += 1
                    # 증분 진행률 — 매 프레임 쓰면 I/O 과다라 20개마다(+마지막).
                    if done % 20 == 0 or done == total:
                        save_scenes(external_id, _prog(
                            {"total_frames": total, "ocr_done": done,
                             "frames": [], "thumb_count": thumb_count}))
            samples = [FrameSample(index=i, t_ms=i * interval_ms,
                                   text=texts.get(i, "")) for i in range(total)]
            return samples, thumb_count

        try:
            samples, thumb_count = await asyncio.to_thread(_work)
        finally:
            # OCR용 원본 프레임은 크므로 제거(썸네일만 남긴다) — 실패해도 제거한다.
            shutil.rmtree(frames_dir, ignore_errors=True)

        save_scenes(external_id, {
            "scanning": False,
            "interval_ms": interval_ms,
            "thumb_interval_ms": thumb_interval_ms,
            "thumb_count": thumb_count,
            "frame_count": len(samples),
            "frames": [{"t_ms": s.t_ms, "text": s.text} for s in samples],
            "ocr_region": region_out,
        })
    except StaleRunCancelled:
        logger.info("scene scan %s cancelled (gen %d)", external_id, generation)
        # 멈추는 쪽이 자기 플래그를 내린다(정밀화와 동일한 경합) — 취소 직후
        # 이 워커의 진행률 저장이 scanning=true를 되살리면 폴링이 안 끝난다.
        # 부분 판독은 남기지 않되(완료로 오인 방지) 구역 설정은 보존한다.
        save_scenes(external_id, {"scanning": False, "interval_ms": interval_ms,
                                  "ocr_region": region_out})
    except Exception:  # noqa: BLE001
        if generation != _current_generation(external_id):
            # kill_active로 죽은 ffmpeg(extract_frames/extract_thumbnails)가
            # FfmpegError로 표면화된 경우 — 세대가 이미 넘어갔으면(취소·재생성)
            # 실패가 아니라 취소이므로 조용히 정리한다.
            logger.info("scene scan %s cancelled mid-ffmpeg (gen %d)",
                        external_id, generation)
            return
        logger.exception("scene scan %s failed", external_id)
        # 진짜 실패 — error를 기록해 프론트 폴링이 멈추게 한다(3분 헛대기 방지).
        try:
            save_scenes(external_id, {"scanning": False, "frames": [],
                                      "error": "스캔에 실패했습니다. 서버 로그를 확인하세요."})
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        _BURN_SEMAPHORE.release()


# 지문 클러스터 흡수 캡 — 프론트 '오독 갈라짐 정리'(FLANK_MAX_MS)와 동일 5초.
# 이보다 긴 블록은 진짜 비단조 씬일 수 있어 보존한다.
_FP_FLANK_MAX_MS = 5000


def build_fingerprint_segments(runs_raw: list[dict], rule_dict: dict) -> dict:
    """지문 런 + 규칙 → 양 모드 세그먼트(순수 함수, build_scene_data의 지문판).
    경계는 이미 프레임 정확한 컷이라 min_ms 흡수·중앙정렬·정밀화가 없다 —
    규칙은 런들을 같은 키로 병합하는 데만 쓴다.

    그룹핑 전에 런 텍스트를 canonical화하고(구분자 유실 오독 → 같은 키로 병합),
    교정 못 한 오독은 클러스터 흡수(≤5s)로 걷어낸다 — 지문은 런 중간(흐릿한
    프레임 근처)을 읽어 오독률이 높아(실기 11.5%) 이 두 단계가 없으면 오독
    하나가 세그먼트 하나로 굳는다(실기 씬 806→481·시퀀스 322→19)."""
    rule = SlateRule(
        delimiters=rule_dict.get("delimiters", ["_", " ", "-"]),
        seq_tokens=rule_dict["seq_tokens"],
        scene_tokens=rule_dict.get("scene_tokens", []),
    )
    texts = canonicalize_texts([r.get("text", "") for r in runs_raw],
                               rule.delimiters)
    runs = [SceneRun(start_ms=r["start_ms"], end_ms=r["end_ms"], text=t,
                     cut_diff=r.get("cut_diff", 0))
            for r, t in zip(runs_raw, texts)]
    return {
        "segments_scene": [
            {"label": s.label, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in runs_to_segments(runs, rule, "scene",
                                      absorb_flanked_ms=_FP_FLANK_MAX_MS)],
        "segments_sequence": [
            {"label": s.label, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in runs_to_segments(runs, rule, "sequence",
                                      absorb_flanked_ms=_FP_FLANK_MAX_MS)],
    }


# 지문 스캔에서 구역 미지정 시 상단 밴드를 크롭으로 쓴다(_TOP_BAND_DEFAULT와
# 같은 비율) — 지문은 크롭이 필수라(전체 프레임이면 애니 전체의 변화가 다 컷으로
# 잡힌다) 기존 '상단 밴드 가정'을 크롭으로 실체화한 것. 썸네일 간격은 간격
# 스캔의 상한과 동일한 2초 고정(지문에는 샘플 간격 개념이 없다).
_FP_FALLBACK_REGION = (0.0, 0.0, 1.0, _TOP_BAND_DEFAULT)
_FP_THUMB_INTERVAL_S = 2.0


def _text_side(text: str | None, prev_text: str, next_text: str,
               delimiters: list[str]) -> str | None:
    """판독 텍스트가 이전/다음 어느 쪽 슬레이트인지 — squash 접두 상호 일치
    (오독·꼬리 잘림 내성). 판독불가·양쪽 다 일치(공통 접두만 읽힘)면 None."""
    def sq(s: str) -> str:
        # 소문자화 — OCR이 v01/V01을 오가며 읽어(실기) 대소문자 구분 비교는
        # '어느 쪽도 아님'을 만들고 OCR 권위를 무력화한다.
        return "".join("".join(t.split()) for t in tokenize(s, delimiters)).lower()

    x = sq(text or "")
    if not x:
        return None
    prev_sq, next_sq = sq(prev_text), sq(next_text)
    match_prev = x.startswith(prev_sq) or prev_sq.startswith(x)
    match_next = x.startswith(next_sq) or next_sq.startswith(x)
    if match_prev == match_next:
        return None
    return "next" if match_next else "prev"


def _clamp_fp_move(ocr_side, cur: int, target: int) -> int:
    """지문 유사도 이동을 OCR 가독성으로 캡 — 읽히는 프레임의 소속은 OCR이 권위.

    유사도 정렬은 판독불가 페이드에는 옳지만, 새 슬레이트가 옛 그림 위에 일찍
    떠오르는 반대 극성 디졸브에서는 OCR로 이미 '다음'이 읽히는 프레임까지 이전
    쪽으로 밀어버린다(실기 090_0180 꼬리에 0190). 오른쪽 이동은 prev로 재배정될
    구간에서 next로 읽히는 첫 프레임에서 멈추고, 왼쪽 이동은 next로 재배정될
    구간에서 prev로 읽히는 프레임 뒤로 물린다."""
    if target > cur:
        for frame in range(cur, target):
            if ocr_side(frame) == "next":
                return frame
        return target
    if target < cur:
        best = target
        for frame in range(target, cur):
            if ocr_side(frame) == "prev":
                best = frame + 1
        return best
    return cur


def _align_cut(read_at, cut: int, prev_text: str, next_text: str,
               lo: int, hi: int, delimiters: list[str],
               max_probe: int = 8) -> int:
    """지문 컷을 '다음 슬레이트가 읽히는 첫 프레임'으로 정렬한다.

    지문 컷(픽셀 전환 지점)은 디졸브에서 슬레이트 '가독' 전환과 어긋난다
    (실기: 130→140 컷 6프레임 지각 — 클립 꼬리가 다음 시퀀스로 읽힘,
    030→040은 1프레임 조기). read_at(frame)->text로 컷 주변을 읽어, 컷 직전
    프레임이 이미 다음으로 읽히면 왼쪽으로, 컷 프레임이 아직 이전으로 읽히면
    오른쪽으로 걷는다. 판정은 squash 접두 상호 일치(오독·꼬리 잘림 내성) —
    양쪽 다 일치(공통 접두만 읽힘)하거나 판독불가면 근거가 없으므로 멈춘다
    (보수적 — 원래 컷 유지가 기본). lo/hi는 이웃 런 침범 방지 경계(exclusive)."""
    def side(frame: int) -> str | None:
        return _text_side(read_at(frame), prev_text, next_text, delimiters)

    before = side(cut - 1)
    if before == "next":
        # 컷 지각 — 다음 슬레이트가 읽히는 가장 이른 프레임까지 왼쪽으로.
        new = cut - 1
        frame, probes = cut - 2, 0
        while frame > lo and probes < max_probe and side(frame) == "next":
            new = frame
            frame -= 1
            probes += 1
        return new
    if side(cut) == "prev":
        # 컷 조기 — 이전 슬레이트가 끝나는 지점(다음이 읽히는 첫 프레임)까지.
        # 직전 프레임(before)이 판독불가여도 컷 프레임이 '이전'으로 읽히면
        # 걷는다 — before까지 요구하던 가드가 디졸브 경계의 ±1프레임 잔존
        # 4건을 남겼다(실기 468클립 검사).
        frame, probes = cut + 1, 0
        while frame < hi and probes < max_probe:
            s = side(frame)
            if s == "next":
                return frame
            if s != "prev":
                break
            frame += 1
            probes += 1
    return cut


def _fp_align(fp_at, cut: int, ref_prev, ref_next, lo: int, hi: int,
              window: int = 8) -> int | None:
    """지문 유사도 플립 지점으로 컷을 정렬 — 판독불가 페이드 프레임의 귀속.

    디졸브의 페이드 프레임은 OCR로 못 읽지만 픽셀은 아직 이전 슬레이트의
    잔상이다(실기 030_0190→0200: 페이드 2프레임의 지문 거리 4823 vs 8044로
    이전 쪽, 다음 첫 프레임은 7951 vs 127로 다음 쪽 — 사람 눈의 경계와 일치).
    컷 주변 창에서 프레임 지문이 이전/다음 런 대표 지문(안정 프레임) 중 어느
    쪽에 가까운지를 훑어 '다음 쪽에 처음 가까워지는 프레임'을 경계로 삼는다.
    창 안에 플립이 없으면 None(유지). OCR 정렬과 달리 판독 불가 프레임에서도
    동작하고, 이미 추출된 지문 PNG를 재사용해 ffmpeg·OCR 호출이 없다.
    lo/hi는 이웃 런 침범 방지 경계(lo exclusive, hi exclusive)."""
    import numpy as np

    def is_next(frame: int) -> bool:
        fp = fp_at(frame)
        return int(np.sum(fp != ref_prev)) >= int(np.sum(fp != ref_next))

    start = max(lo + 1, cut - window)
    end = min(hi, cut + window + 1)
    prior: bool | None = None
    for frame in range(start, end):
        cur = is_next(frame)
        if cur and prior is not True:
            return frame
        prior = cur
    return None


async def run_scene_scan_fingerprint(external_id: UUID) -> None:
    """burned.mp4 전 프레임의 텍스트 이진화 지문으로 컷을 찾고, 컷 사이 런마다
    슬레이트를 OCR해 scenes.json에 method="fingerprint"로 저장한다. 경계는 규칙
    확정(/scenes/rule) 때 runs_to_segments가 계산한다 — 간격 스캔과 같은 2단계
    UX이되, 경계가 이미 프레임 정확이라 정밀화 단계가 없다.

    진행률: 추출·지문 단계는 total_frames=0(프론트 '프레임 추출 중…' 표시),
    런 OCR 단계부터 ocr_done/total_frames(=런 수)로 증분 기록. 취소·실패·세마포어
    규약은 run_scene_scan과 동일하되 method를 함께 보존한다(방식 선택 유지)."""
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    region_out = None
    try:
        workdir = job_dir(external_id)
        burned = workdir / "burned.mp4"
        if not burned.exists():
            raise RuntimeError("굽기 완료본(burned.mp4)이 없습니다.")
        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")
        # 컷 프레임 인덱스 ↔ 시각(ms) 변환의 기준 — 반드시 측정 fps(showinfo).
        fps = video_fps(ffmpeg, burned)
        if not fps:
            raise RuntimeError("소스 프레임레이트를 측정하지 못했습니다.")

        frames_dir = workdir / "scene_fp_frames"
        thumbs_dir = workdir / "scene_thumbs"
        for d in (frames_dir, thumbs_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

        region = load_ocr_region(external_id)
        region_out = ({"x": region[0], "y": region[1],
                       "w": region[2], "h": region[3]} if region else None)
        eff_region = region or _FP_FALLBACK_REGION
        thumb_interval_ms = int(_FP_THUMB_INTERVAL_S * 1000)

        def _prog(extra: dict) -> dict:
            return {"scanning": True, "method": "fingerprint",
                    "thumb_interval_ms": thumb_interval_ms,
                    "ocr_region": region_out, **extra}

        def _check_cancel() -> None:
            if generation != _current_generation(external_id):
                raise StaleRunCancelled(external_id)

        def _work() -> tuple[list[SceneRun], int, int]:
            extract_fingerprint_frames(ffmpeg, burned, frames_dir, eff_region,
                                       proc_key=str(external_id))
            extract_thumbnails(ffmpeg, burned, thumbs_dir, _FP_THUMB_INTERVAL_S,
                               proc_key=str(external_id))
            thumb_count = len(list(thumbs_dir.glob("thumb_*.jpg")))
            pngs = sorted(frames_dir.glob("f_*.png"))
            n_frames = len(pngs)
            if n_frames == 0:
                raise RuntimeError("프레임을 추출하지 못했습니다.")
            save_scenes(external_id, _prog(
                {"total_frames": 0, "ocr_done": 0, "frames": [],
                 "thumb_count": thumb_count}))
            # 인접+윈도우 diff 한 패스 — 윈도우가 느린 페이드(인접 diff가 임계를
            # 못 넘는 디졸브)의 컷 누락을 막는다(실기: 씬 통째 흡수).
            diffs, wdiffs = diff_series(pngs, FADE_WINDOW,
                                        check_cancel=_check_cancel)
            runs_f = frame_runs(
                detect_cuts_with_fades(diffs, wdiffs, FADE_WINDOW), n_frames)
            total = len(runs_f)
            save_scenes(external_id, _prog(
                {"total_frames": total, "ocr_done": 0, "frames": [],
                 "thumb_count": thumb_count}))

            tmpdir = workdir / "fp_ocr_tmp"
            tmpdir.mkdir(parents=True, exist_ok=True)

            # 런마다 '정지' 프레임(인접 diff 최소)을 골라 한 번의 디코드 패스로
            # 일괄 추출한다 — 런마다 -ss 시킹하면 830ms×수천 런=수 분이 시킹에
            # 녹고(실측 총 9분), 흐릿한 중간 프레임을 읽어 오독도 는다.
            picks = [stable_frame(diffs, s, e) for s, e in runs_f]
            batch = extract_frames_at(ffmpeg, burned, picks, tmpdir, eff_region,
                                      proc_key=str(external_id),
                                      workers=_refine_workers())

            def _read_run(item: tuple[int, tuple[int, int]]) -> str:
                idx, (start_f, end_f) = item
                _check_cancel()
                png = batch.get(picks[idx])
                text = (read_slate_line(png, _DEFAULT_DELIMS, top_frac=1.0)
                        if png is not None else "")
                if text:
                    return text
                # 배치 프레임 판독 실패 — 컷에서 떨어진 다른 프레임을 시킹
                # 추출해 재시도(드묾: 실기 2658런 중 14). 런 내부는 텍스트가
                # 동일하다는 게 지문 방식의 전제라 자리만 바꿔 본다.
                span = end_f - start_f
                for frac in (0.25, 0.75):
                    fi = min(end_f - 1, start_f + int(span * frac))
                    dst = tmpdir / f"r_{threading.get_ident()}_{fi}.png"
                    extract_frame(ffmpeg, burned, frame_boundary_ms(fi, fps), dst,
                                  proc_key=str(external_id), region=eff_region)
                    text = read_slate_line(dst, _DEFAULT_DELIMS, top_frac=1.0)
                    try:
                        dst.unlink()
                    except OSError:
                        pass
                    if text:
                        return text
                return ""

            texts: list[str] = []
            done = 0
            try:
                # 런 판독은 서로 독립 — 정밀화·스캔과 같은 이유·설정으로 병렬화.
                with ThreadPoolExecutor(max_workers=_refine_workers()) as pool:
                    for text in pool.map(_read_run, enumerate(runs_f)):
                        texts.append(text)
                        done += 1
                        if done % 10 == 0 or done == total:
                            save_scenes(external_id, _prog(
                                {"total_frames": total, "ocr_done": done,
                                 "frames": [], "thumb_count": thumb_count}))

                # 디졸브 경계 정렬 — 텍스트가 달라지는 컷마다 전후 프레임을 읽어
                # 슬레이트 가독 전환 프레임으로 옮긴다(_align_cut 참조). 전후
                # 프레임은 배치로 미리 뜨고, 걷기(드묾)만 개별 시킹한다.
                texts_c = canonicalize_texts(texts, _DEFAULT_DELIMS)
                bounds = [i for i in range(1, len(runs_f))
                          if texts_c[i - 1] and texts_c[i]
                          and texts_c[i - 1] != texts_c[i]]
                align_dir = tmpdir / "align"
                prefetch = (extract_frames_at(
                    ffmpeg, burned,
                    sorted({f for i in bounds
                            for f in (runs_f[i][0] - 1, runs_f[i][0])}),
                    align_dir, eff_region, proc_key=str(external_id),
                    workers=_refine_workers()) if bounds else {})
                read_cache: dict[int, str] = {}

                def _read_frame(fi: int) -> str:
                    if fi in read_cache:
                        return read_cache[fi]
                    png = prefetch.get(fi)
                    if png is None:
                        png = align_dir / f"nb_{fi}.png"
                        extract_frame(ffmpeg, burned,
                                      frame_boundary_ms(fi, fps), png,
                                      proc_key=str(external_id),
                                      region=eff_region)
                    text = read_slate_line(png, _DEFAULT_DELIMS, top_frac=1.0)
                    read_cache[fi] = text
                    return text

                starts = [s for s, _e in runs_f]
                # ① 지문 유사도 정렬 — OCR이 못 읽는 페이드 프레임의 귀속을
                # 픽셀 잔상으로 판정한다(_fp_align 참조). 이동은 OCR 가독성으로
                # 캡(_clamp_fp_move). 추가 ffmpeg·OCR 호출 없음(지문 PNG 재사용).
                fp_cache: dict[int, object] = {}

                def _fp_at(fi: int):
                    fp = fp_cache.get(fi)
                    if fp is None:
                        fp = load_fingerprint(pngs[fi])
                        fp_cache[fi] = fp
                    return fp

                for i in bounds:
                    _check_cancel()
                    aligned = _fp_align(
                        _fp_at, starts[i], _fp_at(picks[i - 1]), _fp_at(picks[i]),
                        lo=starts[i - 1], hi=runs_f[i][1])
                    if aligned is not None and aligned != starts[i]:
                        prev_t, next_t = texts_c[i - 1], texts_c[i]

                        def _side(fi: int, p=prev_t, n=next_t) -> str | None:
                            return _text_side(_read_frame(fi), p, n,
                                              _DEFAULT_DELIMS)

                        starts[i] = _clamp_fp_move(_side, starts[i], aligned)

                # ② OCR 정렬을 '마지막'에 — 읽히는 프레임의 소속은 OCR이 최종
                # 권위다. 유사도가 어떤 이유로든(캡의 판정 불가 프레임 등) 경계를
                # 어긋내면 여기서 교정된다(실기: 하드컷·선명 슬레이트 잔존 오차).
                for bi, i in enumerate(bounds):
                    _check_cancel()
                    starts[i] = _align_cut(
                        _read_frame, starts[i], texts_c[i - 1], texts_c[i],
                        lo=runs_f[i - 1][0], hi=runs_f[i][1],
                        delimiters=_DEFAULT_DELIMS)
                    if bi % 20 == 0 or bi == len(bounds) - 1:
                        save_scenes(external_id, _prog(
                            {"total_frames": total + len(bounds),
                             "ocr_done": total + bi + 1, "frames": [],
                             "thumb_count": thumb_count}))

                # 정렬 결과로 런 재구성 — 연속성 유지(끝=다음 시작), 극단적으로
                # 이웃 경계가 서로를 지나치면(짧은 런 양끝이 동시 이동) 단조 보정.
                for i in range(1, len(starts)):
                    starts[i] = max(starts[i], starts[i - 1] + 1)
                runs_f = [(starts[i],
                           starts[i + 1] if i + 1 < len(starts) else n_frames)
                          for i in range(len(runs_f))]
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

            # cut_diff=각 런을 연 컷의 지문 세기(정렬 후 최종 시작 프레임 기준) —
            # 판독불가 블록 귀속(runs_to_segments)의 유일한 판정 신호다.
            runs = [SceneRun(start_ms=frame_boundary_ms(s, fps),
                             end_ms=frame_boundary_ms(e, fps), text=t,
                             cut_diff=(diffs[s - 1]
                                       if 0 < s <= len(diffs) else 0))
                    for (s, e), t in zip(runs_f, texts)]
            return runs, thumb_count, n_frames

        try:
            runs, thumb_count, n_frames = await asyncio.to_thread(_work)
        finally:
            # 지문용 프레임은 수만 장이라 크다 — 실패해도 제거한다.
            shutil.rmtree(frames_dir, ignore_errors=True)

        save_scenes(external_id, {
            "scanning": False,
            "method": "fingerprint",
            "video_fps": fps,
            "total_ms": frame_boundary_ms(n_frames, fps),
            "thumb_interval_ms": thumb_interval_ms,
            "thumb_count": thumb_count,
            "frame_count": len(runs),
            "runs": [{"start_ms": r.start_ms, "end_ms": r.end_ms,
                      "text": r.text, "cut_diff": r.cut_diff} for r in runs],
            # frames는 토큰 선택 UI 호환용 — 런 시작 시각을 샘플로 노출한다.
            "frames": [{"t_ms": r.start_ms, "text": r.text} for r in runs],
            "ocr_region": region_out,
        })
    except StaleRunCancelled:
        logger.info("scene fp scan %s cancelled (gen %d)", external_id, generation)
        # 멈추는 쪽이 자기 플래그를 내린다(run_scene_scan과 동일 경합 방지).
        # 부분 판독은 남기지 않되 구역·방식 선택은 보존한다.
        save_scenes(external_id, {"scanning": False, "method": "fingerprint",
                                  "ocr_region": region_out})
    except Exception:  # noqa: BLE001
        if generation != _current_generation(external_id):
            # kill_active로 죽은 ffmpeg가 FfmpegError로 표면화된 경우 —
            # 세대가 넘어갔으면 실패가 아니라 취소이므로 조용히 정리한다.
            logger.info("scene fp scan %s cancelled mid-ffmpeg (gen %d)",
                        external_id, generation)
            return
        logger.exception("scene fp scan %s failed", external_id)
        try:
            save_scenes(external_id, {"scanning": False, "method": "fingerprint",
                                      "frames": [], "ocr_region": region_out,
                                      "error": "스캔에 실패했습니다. 서버 로그를 확인하세요."})
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        _BURN_SEMAPHORE.release()


def export_status_path(external_id: UUID | str) -> Path:
    return job_dir(external_id) / "export_status.json"


def save_export_status(external_id: UUID | str, data: dict) -> None:
    path = export_status_path(external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_export_status(external_id: UUID | str) -> dict | None:
    path = export_status_path(external_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


async def run_scene_export(external_id: UUID, mode: str,
                           out_dir: str | None = None) -> list[str]:
    """확정된 scenes.json 경계로 세그먼트를 재인코딩해 out_dir(미지정 시 잡
    디렉토리 scene_out/)에 슬레이트 라벨 파일명으로 저장한다. 저장 경로 목록 반환.

    진행률은 export_status.json에 증분 기록한다(exporting/done/total/error) —
    프론트가 폴링하며 진행바를 표시하고, 완료 시 exporting=False로 전환한다."""
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    try:
        data = load_scenes(external_id)
        if not data:
            raise RuntimeError("먼저 씬 스캔을 실행하세요.")
        key = "segments_sequence" if mode == "sequence" else "segments_scene"
        segments = data.get(key) or []
        if not segments:
            raise RuntimeError("자를 세그먼트가 없습니다 — 규칙을 확정하세요.")

        workdir = job_dir(external_id)
        burned = workdir / "burned.mp4"
        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")
        dest = Path(out_dir) if out_dir else (workdir / "scene_out")
        dest.mkdir(parents=True, exist_ok=True)
        # 컷 경계를 프레임 간 간격 중앙에 놓아 경계 프레임 중복/유실을 없앤다
        # (cut_segment 참조). 소스 전체가 동일 fps라 한 번만 프로브한다.
        fps = video_fps(ffmpeg, burned)
        total = len(segments)
        save_export_status(external_id, {"exporting": True, "done": 0,
                                         "total": total, "out_dir": str(dest),
                                         "error": None})

        def _work() -> list[str]:
            written: list[str] = []
            # 비단조 슬레이트 순서(예: 020→021→020)에서 같은 라벨이 인접하지
            # 않은 채로 두 번 나올 수 있다 — 전체 세그먼트를 미리 dedupe해
            # 파일명 충돌(덮어쓰기로 인한 데이터 손실)을 막는다.
            deduped = dedupe_labels(
                [_sanitize_label(seg["label"]) for seg in segments])
            for i, seg in enumerate(segments):
                if generation != _current_generation(external_id):
                    raise StaleRunCancelled(external_id)
                out_path = dest / f"{deduped[i]}.mp4"
                cut_segment(ffmpeg, burned, out_path,
                            seg["start_ms"], seg["end_ms"],
                            proc_key=str(external_id), fps=fps)
                written.append(str(out_path))
                save_export_status(external_id, {"exporting": True, "done": i + 1,
                                                 "total": total,
                                                 "out_dir": str(dest), "error": None})
            return written

        written = await asyncio.to_thread(_work)
        save_export_status(external_id, {"exporting": False, "done": total,
                                         "total": total, "out_dir": str(dest),
                                         "error": None, "files": written})
        return written
    except StaleRunCancelled:
        logger.info("scene export %s cancelled (gen %d)", external_id, generation)
        try:
            st = load_export_status(external_id) or {}
            save_export_status(external_id, {**st, "exporting": False})
        except Exception:  # noqa: BLE001 — 정리 실패가 취소 경로를 깨뜨리지 않게
            logger.exception("failed to clear exporting flag for %s", external_id)
        return []
    except Exception:  # noqa: BLE001
        if generation != _current_generation(external_id):
            # kill_active로 죽은 ffmpeg(cut_segment)가 FfmpegError로 표면화된
            # 경우 — 세대가 이미 넘어갔으면(취소·재생성) 실패가 아니라 취소이다.
            logger.info("scene export %s cancelled mid-ffmpeg (gen %d)",
                        external_id, generation)
            return []
        # fire-and-forget 태스크(start_job_task)라 재발생시키지 않는다 —
        # unretrieved task exception 경고를 피하고, run_burn_job과 달리 여기는
        # 반환값(경로 목록)이 실패 신호를 이미 겸한다.
        logger.exception("scene export %s failed", external_id)
        try:
            save_export_status(external_id, {"exporting": False, "error":
                                             "익스포트에 실패했습니다. 서버 로그를 확인하세요."})
        except Exception:  # noqa: BLE001
            pass
        return []
    finally:
        _BURN_SEMAPHORE.release()


def refine_status_path(external_id: UUID | str) -> Path:
    return job_dir(external_id) / "refine_status.json"


def save_refine_status(external_id: UUID | str, data: dict) -> None:
    path = refine_status_path(external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# 정밀화 병렬 워커 수. ffmpeg 디코딩과 onnxruntime이 이미 내부적으로 멀티스레드라
# 프로브 하나만으로도 CPU가 거의 포화된다 — 실측(8코어 Intel, 24프로브): 순차 7.2초,
# 4워커 5.5초(1.3배), 6워커 6.4초, 8워커 6.4초로 4를 넘기면 오히려 나빠진다.
# 병렬화 이득은 1.3배가 상한이며, 그 이상은 추출 방식을 바꿔야 한다.
def _refine_workers() -> int:
    raw = os.environ.get("YESON_REFINE_WORKERS")
    if raw and raw.isdigit() and int(raw) > 0:
        return int(raw)
    return max(1, min(4, (os.cpu_count() or 4) // 2))


def _clear_refining(external_id: UUID | str) -> None:
    """정밀화 종료(취소 포함) 시 진행 플래그를 내린다. 켜진 채 남으면 프론트가
    끝나지 않는 작업을 영원히 폴링한다."""
    try:
        st = load_refine_status(external_id) or {}
        save_refine_status(external_id, {**st, "refining": False})
    except Exception:  # noqa: BLE001 — 정리 실패가 취소 경로를 깨뜨리지 않게
        logger.exception("failed to clear refining flag for %s", external_id)


def load_refine_status(external_id: UUID | str) -> dict | None:
    path = refine_status_path(external_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


async def run_scene_refine(external_id: UUID, mode: str) -> None:
    """현재 모드 세그먼트의 각 경계를 이진탐색 OCR로 실제 전환 프레임까지 좁힌다.

    2초 샘플링 격자로는 컷이 ±1초 어긋나(이웃 시퀀스가 클립에 남음), 중앙정렬로
    반감해도 잔여가 있다. 경계마다 [b-half, b+half] 창을 이진탐색해 라벨이 next로
    바뀌는 지점(<1프레임 정밀도)을 찾아 경계를 그 프레임으로 옮긴다. 진행률은
    refine_status.json에 증분 기록한다(refining/done/total/error)."""
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    try:
        data = load_scenes(external_id)
        if not data or not data.get("rule"):
            raise RuntimeError("먼저 규칙을 확정하세요.")
        seg_key = "segments_sequence" if mode == "sequence" else "segments_scene"
        segments = [dict(s) for s in (data.get(seg_key) or [])]
        # 내부 경계 + (앞머리가 판독실패 구간이면) 첫 세그 시작도 정밀화 대상.
        total = (len(segments) - 1) + (1 if segments and
                                       segments[0]["start_ms"] > 0 else 0)
        if total < 1:
            save_refine_status(external_id, {"refining": False, "done": 0,
                                             "total": 0, "error": None})
            return
        rd = data["rule"]
        delimiters = rd.get("delimiters", ["_", "-"])
        indices = (rd["seq_tokens"] if mode == "sequence"
                   else rd["seq_tokens"] + rd.get("scene_tokens", []))
        upto = max(indices) if indices else -1
        interval_ms = data.get("interval_ms", 2000)
        burned = job_dir(external_id) / "burned.mp4"
        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")
        tmpdir = job_dir(external_id) / "refine_tmp"
        save_refine_status(external_id, {"refining": True, "done": 0,
                                         "total": total, "error": None})

        # 스캔과 같은 영역·밴드로 읽어야 경계가 흔들리지 않는다.
        region = load_ocr_region(external_id)
        band = _band_for(region)

        def label_at(t_ms: int) -> str:
            # 파일명에 스레드 id를 넣는다 — 병렬 워커가 같은 시각을 볼 때 서로의
            # 임시 프레임을 덮어쓰지 않도록.
            dst = tmpdir / f"r_{threading.get_ident()}_{t_ms}.png"
            extract_frame(ffmpeg, burned, t_ms, dst, proc_key=str(external_id),
                          region=region)
            text = read_slate_line(dst, delimiters, top_frac=band)
            try:
                dst.unlink()
            except OSError:
                pass
            toks = tokenize(text, delimiters) if text else []
            return build_label(toks, upto)

        # 경계 하나를 푼다 — '원래' 이웃 값만 보고 계산하며 segments를 건드리지
        # 않는다. 그래야 경계끼리 독립이 되어 병렬로 돌릴 수 있고(적용은 나중에
        # 한 번에), 결과가 순차 실행과 같다.
        def _solve(i: int) -> tuple[int, int] | None:
            if generation != _current_generation(external_id):
                raise StaleRunCancelled(external_id)
            if i == 0:
                # 첫 세그 시작 — 앞머리가 타이틀카드 등 판독실패 구간이면 첫 세그
                # 시작이 첫 유효 샘플에 붙어 실제 시작보다 최대 interval만큼 늦다
                # (실기 010 첫 1초=24프레임 유실). 판독실패("")는 라벨 불일치라
                # 이진탐색 오라클이 자연스럽게 '전환 전'으로 분류한다.
                b, floor = segments[0]["start_ms"], 0
                ceil_ms = segments[0]["end_ms"]
                label, other = segments[0]["label"], ""
            else:
                b = segments[i]["start_ms"]
                floor = segments[i - 1]["start_ms"]
                ceil_ms = segments[i]["end_ms"]
                label, other = segments[i]["label"], segments[i - 1]["label"]

            # 오독 내성 라벨 판정 — OCR이 구분자를 놓쳐 토큰이 붙어 읽혀도
            # ("HH0307_1200010"; 실기에서 경계 2초+ 지각) 같은 쪽으로 분류.
            def at_target(t_ms: int) -> bool:
                return label_matches(label_at(t_ms), label, other, delimiters)

            # 창을 ±interval로 넓힌다 — 스캔 프레임시각(fps 필터)과 컷/정밀화가
            # 쓰는 -ss 시각이 최대 ~1.5초 어긋나므로, ±half(±1초)로는 실제 전환을
            # 못 담는다(실측). 이웃 구간 범위로 클램프해 next-next로 넘치지 않게.
            lo = max(floor, b - interval_ms)
            hi = min(ceil_ms, b + interval_ms)
            # 창 시작이 이미 target이면 전환이 창보다 앞이다(오독 세그먼트가 직전에
            # 흡수돼 사전 경계가 지각한 실측 케이스) — 직전 구간 시작까지 창을
            # 왼쪽으로 확장한다(회당 2×interval, 유한 반복).
            for _ in range(8):
                if lo <= floor or not at_target(lo):
                    break
                lo = max(floor, lo - 2 * interval_ms)
            # 창 끝=target, 창 시작≠target 여야 전환이 창 안에 있다(아니면 중앙정렬
            # 유지). 종료 임계는 1프레임(50fps=20ms)보다 작아야 한다 — 150ms
            # (≈3.6프레임@23.976)로는 경계가 전환 프레임 뒤로 수렴해(실측 10/15
            # 지각) 새 시퀀스 첫 프레임들이 직전 클립 끝에 새 나간다.
            if not (at_target(hi) and not at_target(lo)):
                return None
            while hi - lo > 20:
                mid = (lo + hi) // 2
                if at_target(mid):
                    hi = mid
                else:
                    lo = mid
            return (i, hi)

        def _work() -> list[dict]:
            tmpdir.mkdir(parents=True, exist_ok=True)
            targets = list(range(1, len(segments)))
            if segments and segments[0]["start_ms"] > 0:
                targets.insert(0, 0)

            done = 0
            lock = threading.Lock()

            def _run_one(i: int):
                nonlocal done
                out = _solve(i)
                with lock:
                    done += 1
                    # 진행률 저장은 I/O라 매번 쓰지 않는다(병렬이면 더 잦다).
                    if done % 5 == 0 or done == total:
                        save_refine_status(external_id,
                                           {"refining": True, "done": done,
                                            "total": total, "error": None})
                return out

            # 경계는 서로 독립이고 병목이 ffmpeg 프레임 추출(실측 184ms, 판독의 4배)
            # 이라 병렬로 처리한다. 워커는 물리 코어 절반 수준으로 잡는다 — 더 늘리면
            # ffmpeg끼리 경합해 이득이 줄고 메모리(스레드당 OCR 엔진)만 는다.
            results: list[tuple[int, int] | None] = []
            with ThreadPoolExecutor(max_workers=_refine_workers()) as pool:
                futures = [pool.submit(_run_one, i) for i in targets]
                try:
                    for fut in futures:
                        results.append(fut.result())
                except BaseException:
                    for fut in futures:
                        fut.cancel()
                    raise

            # 적용은 순차로 한 번에 — 병렬 계산 중에는 segments를 건드리지 않았다.
            for out in results:
                if out is None:
                    continue
                i, new_start = out
                segments[i]["start_ms"] = new_start
                if i > 0:
                    segments[i - 1]["end_ms"] = new_start
            return segments

        refined = await asyncio.to_thread(_work)
        shutil.rmtree(tmpdir, ignore_errors=True)
        data[seg_key] = refined
        save_scenes(external_id, data)
        save_refine_status(external_id, {"refining": False, "done": total,
                                         "total": total, "error": None})
    except StaleRunCancelled:
        logger.info("scene refine %s cancelled (gen %d)", external_id, generation)
        # 멈추는 쪽이 자기 플래그를 내린다 — 취소 엔드포인트가 내려도 그 직후
        # 이 워커가 진행률을 다시 써 refining=true로 되살아나던 경합(실기).
        _clear_refining(external_id)
    except Exception:  # noqa: BLE001
        if generation != _current_generation(external_id):
            _clear_refining(external_id)
            return
        logger.exception("scene refine %s failed", external_id)
        try:
            save_refine_status(external_id, {"refining": False, "error":
                                             "경계 정밀화에 실패했습니다. 서버 로그를 확인하세요."})
        except Exception:  # noqa: BLE001
            pass
    finally:
        _BURN_SEMAPHORE.release()


def _sanitize_label(label: str) -> str:
    """파일명 안전화 — 경로 구분자·제어문자 제거. 공백은 유지(슬레이트 원문 존중),
    빈 라벨은 'segment'로 폴백."""
    bad = '/\\:*?"<>|\n\r\t'
    cleaned = "".join("_" if c in bad else c for c in label).strip()
    return cleaned or "segment"
