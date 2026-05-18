# === ANCHOR: AUDIO_STATS_START ===
"""S2 audio chunk telemetry endpoint.

In-memory counters via apps.server.ws.audio_stats. Resets on server restart.
Auth: operator/admin JWT (reuses existing require_operator dep).
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.deps import require_operator
from apps.server.db.models import AppUser, Session
from apps.server.db.session import get_session
from apps.server.ws.audio_stats import audio_stats

router = APIRouter(tags=["audio-stats"])


@router.get("/sessions/{external_id}/audio_stats")
# === ANCHOR: AUDIO_STATS_GET_AUDIO_STATS_START ===
async def get_audio_stats(
    external_id: UUID,
    _operator: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
# === ANCHOR: AUDIO_STATS_GET_AUDIO_STATS_END ===
) -> dict:
    """Return current in-memory audio-chunk telemetry for a session.

    Returns 404 if the session does not exist. Returns empty stats (zeros) if
    the session exists but no chunks have arrived yet.
    """
    session = (
        await db.execute(select(Session).where(Session.external_id == external_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")

    snap = audio_stats.snapshot(external_id)
    if snap is None:
        return {
            "session_id": str(external_id),
            "total_bytes": 0,
            "total_chunks": 0,
            "chunks_per_sec_1s": 0,
            "last_seq": None,
            "started_at": None,
            "stopped_at": None,
            "stopped_reason": None,
            "age_ms": None,
        }
    snap["session_id"] = str(external_id)
    return snap
# === ANCHOR: AUDIO_STATS_END ===
