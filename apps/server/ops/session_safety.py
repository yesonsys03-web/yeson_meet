# === ANCHOR: SESSION_SAFETY_START ===
"""Meeting duration safety controls for Gemini cost containment."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.db.models import Session
from apps.server.ops.alerts import (
    raise_meeting_disconnect_alert,
    raise_meeting_max_duration_alert,
)
from apps.server.domain.events import SessionEnded, serialize
from apps.server.ws.bus import bus

DEFAULT_MAX_DURATION_HOURS = 3.0
MAX_DURATION_ENV = "YESON_MEETING_MAX_DURATION_HOURS"
DEFAULT_DISCONNECT_GRACE_SECONDS = 300.0
DISCONNECT_GRACE_ENV = "YESON_MEETING_DISCONNECT_GRACE_SECONDS"


# === ANCHOR: SESSION_SAFETY_MEETING_MAX_DURATION_START ===
def meeting_max_duration() -> timedelta:
    """Return the configured per-meeting max duration; non-positive disables it."""
    hours = float(os.environ.get(MAX_DURATION_ENV, str(DEFAULT_MAX_DURATION_HOURS)))
    if hours <= 0:
        return timedelta.max
    return timedelta(hours=hours)
# === ANCHOR: SESSION_SAFETY_MEETING_MAX_DURATION_END ===


# === ANCHOR: SESSION_SAFETY_SESSION_STARTED_AT_EXCEEDS_MAX_DURATION_START ===
def session_started_at_exceeds_max_duration(
    started_at: datetime,
    now: datetime | None = None,
# === ANCHOR: SESSION_SAFETY_SESSION_STARTED_AT_EXCEEDS_MAX_DURATION_END ===
) -> bool:
    """Return True when a live meeting should be force-ended for cost safety."""
    current = now or datetime.now(timezone.utc)
    return _as_utc(current) - _as_utc(started_at) >= meeting_max_duration()


# === ANCHOR: SESSION_SAFETY_ENFORCE_MEETING_DURATION_LIMIT_START ===
async def enforce_meeting_duration_limit(
    db: AsyncSession,
    meeting: Session,
    now: datetime | None = None,
# === ANCHOR: SESSION_SAFETY_ENFORCE_MEETING_DURATION_LIMIT_END ===
) -> bool:
    """Mark an over-duration live meeting ended and raise an operator alert."""
    if meeting.status == "ended":
        return False
    ended_at = _as_utc(now or datetime.now(timezone.utc))
    if not session_started_at_exceeds_max_duration(meeting.started_at, ended_at):
        return False

    meeting.status = "ended"
    meeting.ended_at = ended_at
    await db.commit()
    raise_meeting_max_duration_alert(str(meeting.external_id))
    await bus.publish(
        meeting.external_id,
        serialize(
            SessionEnded(
                session_id=meeting.external_id,
                occurred_at=ended_at,
                ended_at=ended_at,
            )
        ),
    )
    return True


# === ANCHOR: SESSION_SAFETY_DISCONNECT_GRACE_START ===
def disconnect_grace() -> timedelta:
    """Grace before a disconnected live meeting is force-ended; non-positive disables it."""
    raw = os.environ.get(DISCONNECT_GRACE_ENV, str(DEFAULT_DISCONNECT_GRACE_SECONDS))
    try:
        seconds = float(raw)
    except ValueError:
        seconds = DEFAULT_DISCONNECT_GRACE_SECONDS
    if seconds <= 0:
        return timedelta.max
    return timedelta(seconds=seconds)
# === ANCHOR: SESSION_SAFETY_DISCONNECT_GRACE_END ===


# === ANCHOR: SESSION_SAFETY_SESSION_DISCONNECT_EXCEEDS_GRACE_START ===
def session_disconnect_exceeds_grace(
    disconnected_at: datetime,
    now: datetime | None = None,
) -> bool:
    """Return True when a disconnected live meeting should be force-ended."""
    current = now or datetime.now(timezone.utc)
    return _as_utc(current) - _as_utc(disconnected_at) >= disconnect_grace()
# === ANCHOR: SESSION_SAFETY_SESSION_DISCONNECT_EXCEEDS_GRACE_END ===


# === ANCHOR: SESSION_SAFETY_ENFORCE_SIDECAR_DISCONNECT_LIMIT_START ===
async def enforce_sidecar_disconnect_limit(
    db: AsyncSession,
    meeting: Session,
    now: datetime | None = None,
) -> bool:
    """Mark a live meeting ended once its sidecar has been gone past the grace period."""
    if meeting.status == "ended":
        return False
    if meeting.disconnected_at is None:
        return False
    ended_at = _as_utc(now or datetime.now(timezone.utc))
    if not session_disconnect_exceeds_grace(meeting.disconnected_at, ended_at):
        return False

    meeting.status = "ended"
    meeting.ended_at = ended_at
    await db.commit()
    raise_meeting_disconnect_alert(str(meeting.external_id))
    await bus.publish(
        meeting.external_id,
        serialize(
            SessionEnded(
                session_id=meeting.external_id,
                occurred_at=ended_at,
                ended_at=ended_at,
            )
        ),
    )
    return True
# === ANCHOR: SESSION_SAFETY_ENFORCE_SIDECAR_DISCONNECT_LIMIT_END ===


# === ANCHOR: SESSION_SAFETY__AS_UTC_START ===
def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
# === ANCHOR: SESSION_SAFETY__AS_UTC_END ===
# === ANCHOR: SESSION_SAFETY_END ===
