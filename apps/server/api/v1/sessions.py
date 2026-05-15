"""Sessions router stub. Body implemented in S1-L1 (POST /sessions)."""
from __future__ import annotations

import os
import secrets
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.deps import require_operator
from apps.server.db.models import AppUser, Session, SessionToken
from apps.server.db.session import get_session

router = APIRouter(tags=["sessions"], prefix="/sessions")


def _viewer_base() -> str:
    return os.environ.get("VIEWER_BASE", "http://localhost:5173")


class SessionCreateIn(BaseModel):
    title: str
    client_label: str | None = None
    visibility: str = "org"


class SessionCreateOut(BaseModel):
    session_id: UUID
    viewer_url: str


@router.post("", response_model=SessionCreateOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreateIn,
    user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
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
