# === ANCHOR: UTTERANCES_START ===
"""Utterances router stub. Body implemented in S1-L1 (GET viewer backfill)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.deps import require_operator
from apps.server.db.models import AppUser, Session, SessionToken, Utterance
from apps.server.db.session import get_session

router = APIRouter(tags=["utterances"])


# === ANCHOR: UTTERANCES_UTTERANCEOUT_START ===
class UtteranceOut(BaseModel):
    seq: int
    speaker: str | None
    text_en: str
    text_ko: str
    started_at: datetime
    ended_at: datetime
    is_final: bool
# === ANCHOR: UTTERANCES_UTTERANCEOUT_END ===


# === ANCHOR: UTTERANCES_UTTERANCELISTOUT_START ===
class UtteranceListOut(BaseModel):
    utterances: list[UtteranceOut]
    session_status: str = "live"
# === ANCHOR: UTTERANCES_UTTERANCELISTOUT_END ===


# === ANCHOR: UTTERANCES__LIST_UTTERANCES_START ===
async def _list_utterances(
    db: AsyncSession,
    session_pk: int,
    since: int | None,
    limit: int,
# === ANCHOR: UTTERANCES__LIST_UTTERANCES_END ===
) -> list[Utterance]:
    if since is None:
        stmt = (
            select(Utterance)
            .where(Utterance.session_id == session_pk)
            .order_by(Utterance.seq.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).scalars().all()
        # Newest-first when no `since`; reverse so caller gets ascending order.
        return list(reversed(rows))
    stmt = (
        select(Utterance)
        .where(Utterance.session_id == session_pk, Utterance.seq > since)
        .order_by(Utterance.seq.asc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


# === ANCHOR: UTTERANCES__TO_OUT_START ===
def _to_out(row: Utterance) -> UtteranceOut:
    return UtteranceOut(
        seq=row.seq,
        speaker=row.speaker,
        text_en=row.text_en,
        text_ko=row.text_ko,
        started_at=row.started_at,
        ended_at=row.ended_at,
        is_final=row.is_final,
    )
# === ANCHOR: UTTERANCES__TO_OUT_END ===


@router.get("/sessions/{external_id}/utterances", response_model=UtteranceListOut)
# === ANCHOR: UTTERANCES_LIST_SESSION_UTTERANCES_START ===
async def list_session_utterances(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
    since: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
# === ANCHOR: UTTERANCES_LIST_SESSION_UTTERANCES_END ===
) -> UtteranceListOut:
    meeting = (
        await db.execute(select(Session).where(Session.external_id == external_id))
    ).scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    rows = await _list_utterances(db, meeting.id, since, limit)
    return UtteranceListOut(
        utterances=[_to_out(r) for r in rows],
        session_status=meeting.status,
    )


@router.get("/viewer/utterances", response_model=UtteranceListOut)
# === ANCHOR: UTTERANCES_LIST_VIEWER_UTTERANCES_START ===
async def list_viewer_utterances(
    db: Annotated[AsyncSession, Depends(get_session)],
    token: str = Query(...),
    since: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
# === ANCHOR: UTTERANCES_LIST_VIEWER_UTTERANCES_END ===
) -> UtteranceListOut:
    token_row = (
        await db.execute(select(SessionToken).where(SessionToken.token == token))
    ).scalar_one_or_none()
    if token_row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid viewer token")
    if token_row.expires_at is not None and token_row.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    meeting = (
        await db.execute(select(Session).where(Session.id == token_row.session_id))
    ).scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session not found")
    rows = await _list_utterances(db, meeting.id, since, limit)
    return UtteranceListOut(
        utterances=[_to_out(r) for r in rows],
        session_status=meeting.status,
    )
# === ANCHOR: UTTERANCES_END ===
