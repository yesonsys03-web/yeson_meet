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

_PROGRESS = {"queued": 0, "extracting": 5, "translating": 0,
             "overlaying": 95, "done": 100}

_tasks: set[asyncio.Task] = set()
_PDF_SEMAPHORE = asyncio.Semaphore(1)  # 번역 작업 직렬화 (배치 순서 보장)
_job_tasks: dict[str, asyncio.Task] = {}
_job_generation: dict[str, int] = {}


def _bump_generation(external_id: UUID | str) -> int:
    key = str(external_id)
    gen = _job_generation.get(key, 0) + 1
    _job_generation[key] = gen
    return gen


def _current_generation(external_id: UUID | str) -> int:
    return _job_generation.get(str(external_id), 0)


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
