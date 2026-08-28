"""PDF 번역 작업의 실행 기반 — video_captions/job_tasks.py 미러.

의도적 복제: 자막 잡 레지스트리와 상태(_tasks/세대/세마포어)를 공유하면
한쪽 취소·직렬화가 다른 도메인으로 번진다. 알고리즘은 같고 소유는 분리.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select

from apps.server.db.models import PdfJob
from apps.server.db.session import AsyncSessionLocal

logger = logging.getLogger("yeson.pdf.pipeline")

_PROGRESS = {"queued": 0, "extracting": 5, "transcribing": 0,
             "translating": 0, "overlaying": 95, "done": 100}

_tasks: set[asyncio.Task] = set()
_PDF_SEMAPHORE = asyncio.Semaphore(1)  # 번역 작업 직렬화 (배치 순서 보장)
_job_tasks: dict[str, asyncio.Task] = {}
_job_generation: dict[str, int] = {}

# 지금 재굽기 중인 잡 — 취소 라우트가 "번역 취소"와 "재굽기 취소"를 구분하는
# 유일한 수단이다. 번역 취소는 기존대로 `cancelled`로 끝나지만, 재굽기 취소는
# 멀쩡한 번역본이 디스크에 그대로 있으므로 `done`으로 수렴해야 한다
# (`cancelled`로 굳으면 /download가 영구 409가 되고 편집·rebake·retranslate가
# 전부 막히며, 그 상태는 in-flight가 아니라 프루닝 대상으로 승격된다).
_REBAKING: set[str] = set()


def mark_rebaking(external_id: UUID | str, active: bool) -> None:
    key = str(external_id)
    if active:
        _REBAKING.add(key)
    else:
        _REBAKING.discard(key)


def is_rebaking(external_id: UUID | str) -> bool:
    return str(external_id) in _REBAKING


def _bump_generation(external_id: UUID | str) -> int:
    key = str(external_id)
    gen = _job_generation.get(key, 0) + 1
    _job_generation[key] = gen
    return gen


def _current_generation(external_id: UUID | str) -> int:
    return _job_generation.get(str(external_id), 0)


def start_background_task(coro) -> None:
    """작업(external_id)에 매이지 않는 뒷정리 코루틴용 — job_tasks.start_task
    미러. _tasks에 강한 참조를 남겨 GC가 실행 중 태스크를 거두지 않게 한다."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def start_pdf_task(external_id: UUID, coro) -> None:
    key = str(external_id)
    task = asyncio.create_task(coro)
    _tasks.add(task)
    _job_tasks[key] = task

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if _job_tasks.get(key) is t:
            _job_tasks.pop(key, None)

    task.add_done_callback(_done)


def cancel_pdf_task(external_id: UUID) -> bool:
    _bump_generation(external_id)
    task = _job_tasks.get(str(external_id))
    if task is not None and not task.done():
        task.cancel()
        return True
    return False


async def _load_job(db, external_id: UUID) -> PdfJob:
    return (await db.execute(
        select(PdfJob).where(PdfJob.external_id == external_id)
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


async def _set_status_if_current(external_id: UUID, generation: int, status: str,
                                 *, error: str | None = None, **fields) -> bool:
    """세대가 여전히 유효할 때만 상태를 쓴다 — 취소된 작업의 뒤늦은 쓰기 차단.

    `_set_status`에는 가드가 **없다**(`_set_progress`(`:88-90`)와 다르다). 그래서
    취소 라우트가 확정한 최종 상태를 뒤늦게 끝난 태스크가 덮어쓸 수 있다.

    순서 가정이 필요 없는 이유: `cancel_pdf_task`(`:61-67`)가 `task.cancel()`보다
    **먼저** `_bump_generation`을 하고, 취소 라우트에는 그 뒤 `await db.commit()`
    전까지 await 지점이 없다 — 라우트가 자기 상태를 커밋하기도 전에 세대가 이미
    밀려 있다. 따라서 이 가드만으로 경합이 사라진다.
    """
    if generation != _current_generation(external_id):
        return False
    await _set_status(external_id, status, error=error, **fields)
    return True


async def _set_progress(external_id: UUID, pct: int, generation: int) -> None:
    if generation != _current_generation(external_id):
        return
    try:
        async with AsyncSessionLocal() as db:
            job = await _load_job(db, external_id)
            job.progress = pct
            await db.commit()
    except Exception:  # 진행률은 부가 정보 — 실패해도 작업을 죽이지 않는다
        logger.exception("failed to update progress for pdf job %s", external_id)


async def _try_set_error(external_id: UUID, message: str) -> None:
    try:
        await _set_status(external_id, "error", error=message)
    except Exception:
        logger.exception("failed to record error for pdf job %s", external_id)
