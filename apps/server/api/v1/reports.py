"""보고서 관리 라우터 — 서버 콘솔 전용 무인증 loopback REST.

video_jobs.py와 동일한 control-plane 모델(127.0.0.1, 인증 없음). 보고서는 이미
서버 파일시스템 자산({STORAGE_ROOT}/{session_external_id}/report.*)이므로 서버
콘솔이 관리 주체가 된다. 보고서 생성 로직은 domain/report_*.py 빌더를 재사용한다.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.db.models import Session, Utterance
from apps.server.db.session import get_session
from apps.server.domain.report_docx import build_session_report_docx, build_summary_docx
from apps.server.domain.report_html import build_session_report_html, build_summary_html
from apps.server.domain.report_pdf import convert_docx_to_pdf
from apps.server.domain.reports import (
    build_session_report,
    regenerate_report_with_summary,
    report_path,
    summary_path,
)

router = APIRouter(prefix="/reports", tags=["reports-admin"])

_REPORT_FORMATS = ("md", "html", "docx", "pdf")


def _storage_root() -> str:
    return os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")


def _report_dir(sid: str) -> Path:
    return Path(_storage_root()) / sid


def _dir_size(sid: str) -> int:
    d = _report_dir(sid)
    total = 0
    if d.exists():
        for path in d.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
    return total


async def _get_session_or_404(db: AsyncSession, external_id: UUID) -> Session:
    meeting = (
        await db.execute(select(Session).where(Session.external_id == external_id))
    ).scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "세션을 찾을 수 없습니다")
    return meeting


async def _session_utterances(db: AsyncSession, session_pk: int) -> list[Utterance]:
    return list(
        (
            await db.execute(
                select(Utterance)
                .where(Utterance.session_id == session_pk)
                .order_by(Utterance.started_at.asc(), Utterance.seq.asc())
            )
        ).scalars().all()
    )


def _report_ready(meeting: Session) -> bool:
    sid = str(meeting.external_id)
    if report_path(_storage_root(), sid, "md").exists():
        return True
    return meeting.status == "ended"


def _summary_ready(meeting: Session) -> bool:
    return summary_path(_storage_root(), str(meeting.external_id), "md").exists()


def _row(meeting: Session, *, with_sizes: bool) -> dict:
    sid = str(meeting.external_id)
    out = {
        "session_id": sid,
        "title": meeting.title,
        "status": meeting.status,
        "started_at": meeting.started_at.isoformat() if meeting.started_at else None,
        "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
        "report_ready": _report_ready(meeting),
        "summary_ready": _summary_ready(meeting),
    }
    if with_sizes:
        out["size_bytes"] = _dir_size(sid)
    return out


@router.get("")
async def list_reports(
    db: Annotated[AsyncSession, Depends(get_session)],
    with_sizes: Annotated[bool, Query()] = False,
) -> dict:
    sessions = (
        await db.execute(select(Session).order_by(Session.started_at.desc()).limit(200))
    ).scalars().all()
    return {"items": [_row(s, with_sizes=with_sizes) for s in sessions]}


@router.get("/storage")
async def storage_usage(db: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    root = Path(_storage_root())
    total = 0
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
    count = (await db.execute(select(func.count()).select_from(Session))).scalar_one()
    return {"total_bytes": total, "session_count": count}


async def _load_summary_text(db: AsyncSession, meeting: Session) -> str | None:
    p = summary_path(_storage_root(), str(meeting.external_id), "md")
    if p.exists():
        return p.read_text(encoding="utf-8").strip() or None
    utterances = await _session_utterances(db, meeting.id)
    await regenerate_report_with_summary(_storage_root(), meeting, utterances)
    if p.exists():
        return p.read_text(encoding="utf-8").strip() or None
    return None


@router.get("/{external_id}/view", response_class=HTMLResponse)
async def report_view(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    meeting = await _get_session_or_404(db, external_id)
    utterances = await _session_utterances(db, meeting.id)
    html = build_session_report_html(meeting, utterances)
    return HTMLResponse(content=html)


@router.get("/{external_id}/summary/view", response_class=HTMLResponse)
async def summary_view(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    meeting = await _get_session_or_404(db, external_id)
    summary = await _load_summary_text(db, meeting)
    if not summary:
        return HTMLResponse(content="<p>요약이 아직 없습니다.</p>")
    return HTMLResponse(content=build_summary_html(meeting, summary))


_MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


def _check_fmt(fmt: str) -> None:
    if fmt not in _REPORT_FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"지원하지 않는 형식: {fmt}")


@router.get("/{external_id}/download")
async def report_download(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
    fmt: Annotated[str, Query()] = "md",
) -> Response:
    _check_fmt(fmt)
    meeting = await _get_session_or_404(db, external_id)
    utterances = await _session_utterances(db, meeting.id)
    if fmt == "md":
        data = build_session_report(meeting, utterances).encode("utf-8")
    elif fmt == "html":
        data = build_session_report_html(meeting, utterances).encode("utf-8")
    elif fmt == "docx":
        data = build_session_report_docx(meeting, utterances)
    else:  # pdf
        pdf = convert_docx_to_pdf(build_session_report_docx(meeting, utterances))
        if pdf is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "PDF 변환 엔진 없음")
        data = pdf
    return Response(content=data, media_type=_MEDIA_TYPES[fmt])


@router.get("/{external_id}/summary/download")
async def summary_download(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
    fmt: Annotated[str, Query()] = "md",
) -> Response:
    _check_fmt(fmt)
    meeting = await _get_session_or_404(db, external_id)
    summary = await _load_summary_text(db, meeting)
    if not summary:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "요약이 없습니다")
    if fmt == "md":
        data = summary.encode("utf-8")
    elif fmt == "html":
        data = build_summary_html(meeting, summary).encode("utf-8")
    elif fmt == "docx":
        data = build_summary_docx(meeting, summary)
    else:  # pdf
        pdf = convert_docx_to_pdf(build_summary_docx(meeting, summary))
        if pdf is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "PDF 변환 엔진 없음")
        data = pdf
    return Response(content=data, media_type=_MEDIA_TYPES[fmt])
