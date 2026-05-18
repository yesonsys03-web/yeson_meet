# === ANCHOR: SESSIONS_START ===
"""Sessions router stub. Body implemented in S1-L1 (POST /sessions)."""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.deps import require_operator
from apps.server.db.models import AppUser, Session, SessionToken, Utterance
from apps.server.db.session import get_session
from apps.server.domain.events import SessionEnded, serialize
from apps.server.domain.reports import report_path, write_session_report
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


@router.post("/{external_id}/end", response_model=SessionEndOut)
# === ANCHOR: SESSIONS_END_SESSION_START ===
async def end_session(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
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
    path = write_session_report(_storage_root(), meeting, utterances)
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
# === ANCHOR: SESSIONS_END ===
