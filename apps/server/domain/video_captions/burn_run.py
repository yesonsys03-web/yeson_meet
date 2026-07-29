"""자막 굽기 러너 — SRT 생성→ffmpeg 하드번(run_burn_job).

pipeline.py에서 분리(2026-07-29 리팩토링, 동작 변경 0). 취소·세대·직렬화 규약은
job_tasks가 소유한다.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from apps.server.db.models import VideoSegment
from apps.server.db.session import AsyncSessionLocal
from .ffmpeg import burn_subtitles, locate_ffmpeg, wav_duration_seconds
from .job_store import job_dir
from .job_tasks import (
    _BURN_SEMAPHORE, _bump_generation, _current_generation, _load_job,
    _set_progress, _set_status, _try_set_error,
)
from .srt import SubSegment, build_force_style, segments_to_srt
from .transcribe import StaleRunCancelled

# 로거 이름은 분리 전과 동일하게 유지한다(job_tasks 참조).
logger = logging.getLogger("yeson.video.pipeline")


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
