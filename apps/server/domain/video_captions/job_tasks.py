"""영상 작업의 실행 기반 — 태스크 레지스트리·세대·세마포어·DB 상태 갱신.

pipeline.py에서 분리(2026-07-29 리팩토링, 동작 변경 0). 러너 모듈들이 공유하는
가변 상태(_tasks/_job_tasks/_job_generation/세마포어)의 단일 소유자다 — 여기
말고 다른 곳에 복제하면 취소·직렬화가 갈라진다.
"""
from __future__ import annotations

import asyncio
import logging
import os
from uuid import UUID

from sqlalchemy import select

from apps.server.db.models import VideoJob
from apps.server.db.session import AsyncSessionLocal
from .ffmpeg import kill_active

# 로거 이름은 분리 전과 동일하게 유지한다 — 핸들러 설정(main.py)·로그 필터가
# "yeson.video.pipeline"을 기준으로 하고, 모듈 분리가 로그 계보를 바꾸면 안 된다.
logger = logging.getLogger("yeson.video.pipeline")

# 계측되는 단계(전사/번역/굽기)는 단계 진입 시 0에서 시작해 단계 내부에서
# 0→100%로 채워진다 — UI 라벨이 단계명을 보여주므로 절대 % 의미는 없다.
# queued: 0 — 재생성(rebuild) 직후 목록에 옛 진행률(예: 77%)이 잠깐이라도
# 남지 않도록 대기 전환 시 명시적으로 리셋한다.
_PROGRESS = {"queued": 0, "ingesting": 5, "extracting": 15, "transcribing": 0,
             "translating": 0, "review": 100, "burning": 0, "done": 100}

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


# 정밀화 병렬 워커 수. ffmpeg 디코딩과 onnxruntime이 이미 내부적으로 멀티스레드라
# 프로브 하나만으로도 CPU가 거의 포화된다 — 실측(8코어 Intel, 24프로브): 순차 7.2초,
# 4워커 5.5초(1.3배), 6워커 6.4초, 8워커 6.4초로 4를 넘기면 오히려 나빠진다.
# 병렬화 이득은 1.3배가 상한이며, 그 이상은 추출 방식을 바꿔야 한다.
# (스캔·지문·경계 검사도 같은 이유·설정으로 이 값을 쓴다.)
def _refine_workers() -> int:
    raw = os.environ.get("YESON_REFINE_WORKERS")
    if raw and raw.isdigit() and int(raw) > 0:
        return int(raw)
    return max(1, min(4, (os.cpu_count() or 4) // 2))
