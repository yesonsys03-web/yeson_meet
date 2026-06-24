# === ANCHOR: SESSIONS_START ===
"""Sessions router stub. Body implemented in S1-L1 (POST /sessions)."""
from __future__ import annotations

import asyncio
import os
import secrets
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.deps import require_operator
from apps.server.db.models import AppUser, Session, SessionToken, Utterance
from apps.server.db.session import get_session
from apps.server.domain.events import SessionEnded, serialize
from apps.server.domain.report_docx import build_session_report_docx
from apps.server.domain.report_html import build_session_report_html
from apps.server.domain.report_pdf import convert_docx_to_pdf
from apps.server.domain.reports import (
    regenerate_report_with_summary,
    report_path,
    summary_path,
    write_session_report,
)
from apps.server.ws.bus import bus

router = APIRouter(tags=["sessions"], prefix="/sessions")


# === ANCHOR: SESSIONS__VIEWER_BASE_START ===
def _viewer_base() -> str:
    return os.environ.get("VIEWER_BASE", "http://localhost:5173")
# === ANCHOR: SESSIONS__VIEWER_BASE_END ===


# === ANCHOR: SESSIONS__STORAGE_ROOT_START ===
def _storage_root() -> str:
    return os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")
# === ANCHOR: SESSIONS__STORAGE_ROOT_END ===


# === ANCHOR: SESSIONS_SESSIONCREATEIN_START ===
class SessionCreateIn(BaseModel):
    title: str
    client_label: str | None = None
    visibility: str = "org"
# === ANCHOR: SESSIONS_SESSIONCREATEIN_END ===


# === ANCHOR: SESSIONS_SESSIONCREATEOUT_START ===
class SessionCreateOut(BaseModel):
    session_id: UUID
    viewer_url: str
# === ANCHOR: SESSIONS_SESSIONCREATEOUT_END ===


# === ANCHOR: SESSIONS_SESSIONENDOUT_START ===
class SessionEndOut(BaseModel):
    session_id: UUID
    status: str
    ended_at: datetime
    report_path: str
# === ANCHOR: SESSIONS_SESSIONENDOUT_END ===


@router.post("", response_model=SessionCreateOut, status_code=status.HTTP_201_CREATED)
# === ANCHOR: SESSIONS_CREATE_SESSION_START ===
async def create_session(
    body: SessionCreateIn,
    user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
# === ANCHOR: SESSIONS_CREATE_SESSION_END ===
) -> SessionCreateOut:
    meeting = Session(
        external_id=uuid4(),
        owner_user_id=user.id,
        title=body.title,
        client_label=body.client_label,
        visibility=body.visibility,
        status="live",
    )
    db.add(meeting)
    await db.flush()

    viewer_token = secrets.token_urlsafe(32)
    token_row = SessionToken(
        session_id=meeting.id,
        token=viewer_token,
        kind="viewer",
    )
    db.add(token_row)
    await db.commit()

    return SessionCreateOut(
        session_id=meeting.external_id,
        viewer_url=f"{_viewer_base()}/v/{viewer_token}",
    )


# === ANCHOR: SESSIONS__GET_OPERATOR_SESSION_OR_404_START ===
async def _get_operator_session_or_404(
    db: AsyncSession,
    external_id: UUID,
# === ANCHOR: SESSIONS__GET_OPERATOR_SESSION_OR_404_END ===
) -> Session:
    meeting = (
        await db.execute(select(Session).where(Session.external_id == external_id))
    ).scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return meeting


# === ANCHOR: SESSIONS__SESSION_UTTERANCES_START ===
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
# === ANCHOR: SESSIONS__SESSION_UTTERANCES_END ===


def _snap_meeting(meeting: Session) -> object:
    """Return a plain-object snapshot of the fields used by report builders.

    BackgroundTasks run after the request DB session is closed, so ORM objects
    become detached.  Copying only the fields we need avoids DetachedInstanceError.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        title=meeting.title,
        external_id=meeting.external_id,
        status=meeting.status,
        started_at=meeting.started_at,
        ended_at=meeting.ended_at,
        client_label=meeting.client_label,
    )


def _snap_utterances(utterances: list[Utterance]) -> list[object]:
    """Return plain-object snapshots of utterances used by report builders."""
    from types import SimpleNamespace

    return [
        SimpleNamespace(
            seq=u.seq,
            speaker=u.speaker,
            text_en=u.text_en,
            text_ko=u.text_ko,
            started_at=u.started_at,
            ended_at=u.ended_at,
        )
        for u in utterances
    ]


@router.post("/{external_id}/end", response_model=SessionEndOut)
# === ANCHOR: SESSIONS_END_SESSION_START ===
async def end_session(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
    background_tasks: BackgroundTasks,
# === ANCHOR: SESSIONS_END_SESSION_END ===
) -> SessionEndOut:
    meeting = await _get_operator_session_or_404(db, external_id)
    if meeting.status != "ended":
        meeting.status = "ended"
        meeting.ended_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(meeting)
    elif meeting.ended_at is None:
        meeting.ended_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(meeting)

    utterances = await _session_utterances(db, meeting.id)

    # Take ORM snapshots before DB session closes (detached-object safety)
    snap_meeting = _snap_meeting(meeting)
    snap_utts = _snap_utterances(utterances)

    # Report files (md/html/docx/pdf + summary) are emitted off the request path
    # by the background task below — ending stays fast and never blocks on the
    # LibreOffice PDF conversion or the LLM summary. GET /report* regenerates
    # on demand, so the path is valid even before the background write finishes.
    storage_root = _storage_root()
    path = report_path(storage_root, str(meeting.external_id), "md")

    # Background: emit all report formats (with LLM summary when available).
    background_tasks.add_task(
        regenerate_report_with_summary, storage_root, snap_meeting, snap_utts
    )

    await bus.publish(
        meeting.external_id,
        serialize(
            SessionEnded(
                session_id=meeting.external_id,
                occurred_at=meeting.ended_at,
                ended_at=meeting.ended_at,
            )
        ),
    )
    return SessionEndOut(
        session_id=meeting.external_id,
        status=meeting.status,
        ended_at=meeting.ended_at,
        report_path=str(path),
    )


@router.get("/{external_id}/report")
# === ANCHOR: SESSIONS_DOWNLOAD_SESSION_REPORT_START ===
async def download_session_report(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
# === ANCHOR: SESSIONS_DOWNLOAD_SESSION_REPORT_END ===
) -> FileResponse:
    meeting = await _get_operator_session_or_404(db, external_id)
    path = report_path(_storage_root(), str(meeting.external_id))
    if not path.exists():
        if meeting.status != "ended":
            raise HTTPException(status.HTTP_409_CONFLICT, "Session has not ended")
        utterances = await _session_utterances(db, meeting.id)
        path = write_session_report(_storage_root(), meeting, utterances)
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{meeting.external_id}.md",
    )


@router.get("/{external_id}/report.html")
async def download_session_report_html(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    meeting = await _get_operator_session_or_404(db, external_id)
    if meeting.status != "ended":
        raise HTTPException(status.HTTP_409_CONFLICT, "Session has not ended")
    utterances = await _session_utterances(db, meeting.id)
    html_content = build_session_report_html(meeting, utterances)
    return Response(
        content=html_content,
        media_type="text/html; charset=utf-8",
    )


@router.get("/{external_id}/report.docx")
async def download_session_report_docx(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    meeting = await _get_operator_session_or_404(db, external_id)
    if meeting.status != "ended":
        raise HTTPException(status.HTTP_409_CONFLICT, "Session has not ended")
    utterances = await _session_utterances(db, meeting.id)
    docx_bytes = build_session_report_docx(meeting, utterances)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=\"report.docx\""},
    )


@router.get("/{external_id}/report.pdf")
async def download_session_report_pdf(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    meeting = await _get_operator_session_or_404(db, external_id)
    if meeting.status != "ended":
        raise HTTPException(status.HTTP_409_CONFLICT, "Session has not ended")
    utterances = await _session_utterances(db, meeting.id)
    docx_bytes = build_session_report_docx(meeting, utterances)
    pdf_bytes = await asyncio.to_thread(convert_docx_to_pdf, docx_bytes)
    if pdf_bytes is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF 변환 불가 — 서버에 LibreOffice(soffice)가 설치되어 있지 않습니다.",
        )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=\"report.pdf\""},
    )


@router.get("/{external_id}/report.summary")
async def download_session_report_summary(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Return the standalone summary.md file if available.

    The summary is generated asynchronously after session end.
    Returns 404 with a descriptive message if not yet available.
    """
    meeting = await _get_operator_session_or_404(db, external_id)
    path = summary_path(_storage_root(), str(meeting.external_id))
    if not path.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="요약이 아직 생성되지 않았습니다. 회의 종료 후 잠시 후 다시 시도하세요.",
        )
    return Response(
        content=path.read_text(encoding="utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=\"summary.md\""},
    )
# === ANCHOR: SESSIONS_END ===
