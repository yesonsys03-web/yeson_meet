"""PDF 스토리보드 번역 작업 API — video_jobs.py와 동형의 얇은 라우트."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.api.v1.video_jobs import _default_owner_id
from apps.server.db.models import PdfJob
from apps.server.db.session import get_session
from apps.server.domain.pdf_translate.backend import open_pdf
from apps.server.domain.pdf_translate.pdf_run import (
    prune_old_pdf_jobs,
    run_pdf_job,
)
from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir
from apps.server.domain.pdf_translate.pdf_tasks import (
    cancel_pdf_task,
    start_background_task,
    start_pdf_task,
)
from apps.server.domain.video_captions.ingest import save_upload
from apps.server.domain.video_captions.translate_cli import list_translate_engines

router = APIRouter(tags=["pdf-jobs"], prefix="/pdf-jobs")

# 엔진 목록에서 자동 도출 — video_jobs와 동일 이유(하드코딩 드리프트 방지)
_PROVIDER_PATTERN = "^(" + "|".join(
    e["value"] for e in list_translate_engines()) + ")$"

_TERMINAL = ("done", "error", "cancelled")


def _start_pdf_pipeline(external_id: UUID) -> None:  # test seam
    start_pdf_task(external_id, run_pdf_job(external_id))


def _prune_old_jobs() -> None:  # test seam
    # 새 작업이 생길 때마다 최근 RETENTION_KEEP개만 유지 (개수 상한 정책).
    # 응답을 막지 않도록 fire-and-forget — 방금 만든 작업은 queued(in-flight)라
    # 삭제 대상에서 제외된다. video_jobs._prune_old_jobs 미러.
    start_background_task(prune_old_pdf_jobs())


async def _get_job(db: AsyncSession, job_id: UUID) -> PdfJob:
    job = (await db.execute(
        select(PdfJob).where(PdfJob.external_id == job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "작업을 찾을 수 없습니다")
    return job


def _summary(job: PdfJob) -> dict:
    return {
        "job_id": str(job.external_id), "title": job.title,
        "source_ref": job.source_ref, "format": job.format,
        "translate_provider": job.translate_provider,
        "status": job.status, "progress": job.progress, "error": job.error,
        "page_count": job.page_count, "block_count": job.block_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def create_pdf_job(
    db: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    translate_provider: Annotated[
        str | None, Form(pattern=_PROVIDER_PATTERN)] = None,
    translate_cli_model: Annotated[str | None, Form()] = None,
) -> dict:
    filename = file.filename or "upload.pdf"
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "PDF 파일만 업로드할 수 있습니다")
    external_id = uuid4()
    dest = pdf_job_dir(external_id) / "source.pdf"
    try:
        await save_upload(file, dest)
        owner_id = await _default_owner_id(db)
        job = PdfJob(external_id=external_id, owner_user_id=owner_id,
                     title=title or filename, source_ref=filename,
                     translate_provider=translate_provider,
                     translate_cli_model=translate_cli_model,
                     status="queued", source_path=str(dest))
        db.add(job)
        await db.commit()
    except Exception:
        shutil.rmtree(pdf_job_dir(external_id), ignore_errors=True)
        raise
    _prune_old_jobs()
    _start_pdf_pipeline(external_id)
    return {"job_id": str(external_id)}


@router.get("")
async def list_pdf_jobs(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    rows = (await db.execute(
        select(PdfJob).order_by(PdfJob.created_at.desc(), PdfJob.id.desc())
    )).scalars().all()
    return {"items": [_summary(j) for j in rows]}


@router.get("/{job_id}")
async def get_pdf_job(
    job_id: UUID, db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    return _summary(await _get_job(db, job_id))


def _render_page(path: str, page: int) -> bytes:
    doc = open_pdf(Path(path))
    try:
        if page < 0 or page >= doc.page_count:
            raise IndexError(page)
        return doc.render_png(page, dpi=120)
    finally:
        doc.close()


@router.get("/{job_id}/page/{page}")
async def get_pdf_page_png(
    job_id: UUID, page: int,
    db: Annotated[AsyncSession, Depends(get_session)],
    variant: str = "source",
) -> Response:
    job = await _get_job(db, job_id)
    path = job.translated_path if variant == "translated" else job.source_path
    if not path or not Path(path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PDF가 아직 없습니다")
    try:
        png = await asyncio.to_thread(_render_page, path, page)
    except IndexError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "페이지 범위 밖입니다")
    return Response(content=png, media_type="image/png")


@router.get("/{job_id}/download")
async def download_pdf(
    job_id: UUID, db: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    job = await _get_job(db, job_id)
    if job.status != "done" or not job.translated_path:
        raise HTTPException(status.HTTP_409_CONFLICT, "아직 번역이 끝나지 않았습니다")
    path = Path(job.translated_path)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "번역 PDF가 없습니다")
    name = f"{Path(job.source_ref).stem}_번역.pdf"
    return FileResponse(path, media_type="application/pdf", filename=name)


@router.post("/{job_id}/cancel")
async def cancel_pdf_job(
    job_id: UUID, db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await _get_job(db, job_id)
    if job.status in _TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 끝난 작업입니다")
    cancel_pdf_task(job_id)
    job.status = "cancelled"
    job.progress = 0
    await db.commit()
    return {"status": "cancelled"}


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pdf_job(
    job_id: UUID, db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    job = await _get_job(db, job_id)
    cancel_pdf_task(job_id)
    await db.delete(job)
    await db.commit()
    shutil.rmtree(pdf_job_dir(job_id), ignore_errors=True)
