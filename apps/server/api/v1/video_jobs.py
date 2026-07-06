"""Video caption job endpoints.

The entire video captions API is deliberately UNAUTHENTICATED (product
decision 2026-07-06): this deployment treats the LAN as the trust boundary,
the same acceptance already made for viewer tokens. /media in particular
can never carry an Authorization header (HTML5 <video> cannot attach one),
so its unguessable job UUID acts as the capability URL — the other
endpoints extend that same trust decision rather than being a special case.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile, status)
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.db.models import AppUser, VideoJob, VideoSegment
from apps.server.db.session import get_session
from apps.server.domain.video_captions.ingest import save_upload
from apps.server.domain.video_captions.pipeline import (job_dir, run_burn_job,
                                                        run_video_job, start_task)
from apps.server.domain.video_captions.srt import SubSegment, segments_to_srt
from apps.server.domain.video_captions.whisper_models import CATALOG, is_downloaded

router = APIRouter(tags=["video-jobs"], prefix="/video-jobs")


def _start_pipeline(external_id: UUID) -> None:  # test seam
    start_task(run_video_job(external_id))


def _start_burn(external_id: UUID, position: str, margin_v: int,
                font_size: int) -> None:  # test seam
    start_task(run_burn_job(external_id, position, margin_v, font_size))


class VideoJobCreateIn(BaseModel):
    youtube_url: str
    whisper_model: str
    title: str | None = None
    translate_provider: str | None = Field(
        default=None, pattern="^(gemini|claude|codex|agy|opencode)$")
    translate_cli_model: str | None = None


class BurnIn(BaseModel):
    position: str = Field(pattern="^(bottom|top)$")
    margin_v: int = Field(ge=0, le=300)
    font_size: int = Field(ge=8, le=72)


def _job_out(job: VideoJob) -> dict:
    return {
        "job_id": str(job.external_id),
        "title": job.title,
        "source_type": job.source_type,
        "source_ref": job.source_ref,
        "whisper_model": job.whisper_model,
        "translate_provider": job.translate_provider,
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


def _require_model(name: str) -> None:
    if name not in CATALOG:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown whisper model")
    if not is_downloaded(name):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"whisper 모델 '{name}'이 설치되어 있지 않습니다. 먼저 다운로드하세요.")


async def _get_job_or_404(db: AsyncSession, external_id: UUID) -> VideoJob:
    job = (await db.execute(
        select(VideoJob).where(VideoJob.external_id == external_id)
    )).scalar_one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video job not found")
    return job


async def _default_owner_id(db: AsyncSession) -> int:
    """무인증 개방 후 VideoJob.owner_user_id(NOT NULL FK) 채우기용 — 시스템의
    첫 사용자를 소유자로 해석한다. 서버 콘솔 온보딩이 항상 계정을 먼저
    만들므로 실질적으로 503에 도달하지 않는다."""
    owner_id = (await db.execute(
        select(AppUser.id).order_by(AppUser.id).limit(1)
    )).scalar_one_or_none()
    if owner_id is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "운영자 계정이 아직 없습니다 — 서버 콘솔에서 계정을 먼저 만드세요")
    return owner_id


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_video_job(
    body: VideoJobCreateIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require_model(body.whisper_model)
    owner_id = await _default_owner_id(db)
    job = VideoJob(external_id=uuid4(), owner_user_id=owner_id,
                   title=body.title or body.youtube_url, source_type="youtube",
                   source_ref=body.youtube_url, whisper_model=body.whisper_model,
                   translate_provider=body.translate_provider,
                   translate_cli_model=body.translate_cli_model,
                   status="queued")
    db.add(job)
    await db.commit()
    _start_pipeline(job.external_id)
    return {"job_id": str(job.external_id)}


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def create_upload_job(
    db: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    whisper_model: Annotated[str, Form()],
    title: Annotated[str | None, Form()] = None,
    translate_provider: Annotated[
        str | None, Form(pattern="^(gemini|claude|codex|agy|opencode)$")] = None,
    translate_cli_model: Annotated[str | None, Form()] = None,
) -> dict:
    _require_model(whisper_model)
    external_id = uuid4()
    filename = file.filename or "upload.mp4"
    suffix = Path(filename).suffix or ".mp4"
    dest = job_dir(external_id) / f"source{suffix}"
    try:
        await save_upload(file, dest)
        owner_id = await _default_owner_id(db)
        job = VideoJob(external_id=external_id, owner_user_id=owner_id,
                       title=title or filename, source_type="upload",
                       source_ref=filename, whisper_model=whisper_model,
                       translate_provider=translate_provider,
                       translate_cli_model=translate_cli_model,
                       status="queued", media_path=str(dest))
        db.add(job)
        await db.commit()
    except Exception:
        # 실패 시 방금 쓴 파일/디렉터리 정리 — DB 행 없는 고아 파일 방지
        shutil.rmtree(job_dir(external_id), ignore_errors=True)
        raise
    _start_pipeline(external_id)
    return {"job_id": str(external_id)}


@router.get("")
async def list_video_jobs(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    jobs = (await db.execute(
        select(VideoJob).order_by(VideoJob.created_at.desc()).limit(100)
    )).scalars().all()
    return {"items": [_job_out(j) for j in jobs]}


@router.get("/{external_id}")
async def get_video_job(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await _get_job_or_404(db, external_id)
    rows = (await db.execute(
        select(VideoSegment).where(VideoSegment.job_id == job.id)
        .order_by(VideoSegment.seq)
    )).scalars().all()
    out = _job_out(job)
    out["segments"] = [{"seq": r.seq, "start_ms": r.start_ms, "end_ms": r.end_ms,
                        "text_en": r.text_en, "text_ko": r.text_ko} for r in rows]
    return out


class SegmentEdit(BaseModel):
    seq: int
    text_ko: str


class SegmentsPatchIn(BaseModel):
    edits: list[SegmentEdit]


@router.patch("/{external_id}/segments")
async def patch_segments(
    external_id: UUID,
    body: SegmentsPatchIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await _get_job_or_404(db, external_id)
    by_seq = {e.seq: e.text_ko for e in body.edits}
    rows = (await db.execute(
        select(VideoSegment).where(VideoSegment.job_id == job.id,
                                   VideoSegment.seq.in_(list(by_seq)))
    )).scalars().all()
    for row in rows:
        row.text_ko = by_seq[row.seq]
    await db.commit()
    return {"updated": len(rows)}


@router.post("/{external_id}/burn", status_code=status.HTTP_202_ACCEPTED)
async def burn_video_job(
    external_id: UUID,
    body: BurnIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await _get_job_or_404(db, external_id)
    if job.status not in ("review", "done", "error"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"검수 가능한 상태가 아닙니다 (status={job.status})")
    job.status = "burning"
    job.progress = 0
    job.error = None
    await db.commit()
    _start_burn(external_id, body.position, body.margin_v, body.font_size)
    return {"status": "burning"}


@router.get("/{external_id}/download")
async def download_video_job(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
    kind: Annotated[str, Query(pattern="^(video|srt)$")] = "video",
):
    job = await _get_job_or_404(db, external_id)
    if kind == "srt":
        rows = (await db.execute(
            select(VideoSegment).where(VideoSegment.job_id == job.id)
            .order_by(VideoSegment.seq)
        )).scalars().all()
        srt = segments_to_srt(
            [SubSegment(r.seq, r.start_ms, r.end_ms, r.text_ko) for r in rows])
        return Response(
            content=srt.encode("utf-8"), media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="{job.external_id}.srt"'})
    if not job.burned_path or not Path(job.burned_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "burned video not ready")
    return FileResponse(job.burned_path, media_type="video/mp4",
                        filename=f"{job.title[:60]}-captioned.mp4")


@router.get("/{external_id}/media")
async def stream_video_media(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    # capability URL — see module docstring
    job = await _get_job_or_404(db, external_id)
    path = job.preview_path or job.media_path
    if not path or not Path(path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media not ready")
    return FileResponse(path, media_type="video/mp4")


@router.delete("/{external_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video_job(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    job = await _get_job_or_404(db, external_id)
    shutil.rmtree(job_dir(external_id), ignore_errors=True)
    await db.delete(job)
    await db.commit()
