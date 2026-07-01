# === ANCHOR: SESSION_SAFETY_SCHEDULER_START ===
"""Background watchdog that force-ends over-duration live meetings.

The ingress enforcement in ``apps/server/ws/sidecar.py`` only fires while audio
chunks flow. A zombie session (sidecar silent or hung) is never re-checked, so
Gemini cost could accrue indefinitely. This watchdog polls live sessions on a
fixed interval and applies the same ``enforce_meeting_duration_limit``, closing
the gap.

Single-process assumption: ``InMemoryBus`` is per-process. Under multiple workers
each worker would run its own watchdog; ``enforce_meeting_duration_limit`` is
idempotent so DB state stays correct, but viewers on other workers would miss the
``SessionEnded`` publish — a pre-existing multi-worker limitation, out of scope.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.server.db.models import Session
from apps.server.db.session import AsyncSessionLocal
from apps.server.ops.session_safety import (
    enforce_meeting_duration_limit,
    enforce_sidecar_disconnect_limit,
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 60.0
POLL_SECONDS_ENV = "YESON_MEETING_SAFETY_POLL_SECONDS"


# === ANCHOR: SESSION_SAFETY_SCHEDULER_POLL_INTERVAL_START ===
def safety_poll_interval() -> float:
    """Watchdog poll interval in seconds; non-positive disables the watchdog."""
    raw = os.environ.get(POLL_SECONDS_ENV, str(DEFAULT_POLL_SECONDS))
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default", POLL_SECONDS_ENV, raw)
        return DEFAULT_POLL_SECONDS
# === ANCHOR: SESSION_SAFETY_SCHEDULER_POLL_INTERVAL_END ===


# === ANCHOR: SESSION_SAFETY_SCHEDULER_SWEEP_ONCE_START ===
async def _sweep_once(session_factory: async_sessionmaker) -> int:
    """Force-end every over-duration or long-disconnected live meeting."""
    ended = 0
    async with session_factory() as db:
        live = (
            await db.execute(select(Session).where(Session.status == "live"))
        ).scalars().all()
        for meeting in live:
            if await enforce_meeting_duration_limit(db, meeting) or (
                await enforce_sidecar_disconnect_limit(db, meeting)
            ):
                ended += 1
    return ended
# === ANCHOR: SESSION_SAFETY_SCHEDULER_SWEEP_ONCE_END ===


# === ANCHOR: SESSION_SAFETY_SCHEDULER_STAMP_LIVE_DISCONNECTED_START ===
async def stamp_live_sessions_disconnected(
    session_factory: async_sessionmaker,
    now: datetime | None = None,
) -> int:
    """Stamp disconnected_at=now on every live session lacking it (startup only).

    On a fresh boot no sidecar is connected yet, so live rows with a NULL
    disconnected_at are made eligible for the disconnect watchdog. A genuinely
    live sidecar reconnects within its backoff and clears the stamp well inside
    the grace period, so no active meeting is wrongly ended.
    """
    when = now or datetime.now(timezone.utc)
    stamped = 0
    async with session_factory() as db:
        live = (
            await db.execute(
                select(Session).where(
                    Session.status == "live",
                    Session.disconnected_at.is_(None),
                )
            )
        ).scalars().all()
        for meeting in live:
            meeting.disconnected_at = when
            stamped += 1
        if stamped:
            await db.commit()
    return stamped
# === ANCHOR: SESSION_SAFETY_SCHEDULER_STAMP_LIVE_DISCONNECTED_END ===


# === ANCHOR: SESSION_SAFETY_SCHEDULER_END_LIVE_AT_STARTUP_START ===
async def end_live_sessions_at_startup(
    session_factory: async_sessionmaker,
    now: datetime | None = None,
) -> int:
    """Force-end every lingering ``live`` session at server boot.

    A freshly started server has no sidecar connected yet, so any ``status ==
    "live"`` row is a leftover from a previous run that terminated without a
    clean end (app force-quit, crash, or client hard-reload) — never a real
    meeting. Ending them here gives the operator the "fresh environment on
    restart" they expect and clears the go-public guard immediately, instead of
    leaving ghosts in the ~5-minute watchdog-grace limbo. Runtime sidecar drops
    while the server keeps running are still handled by the grace-based watchdog
    (``_sweep_once``), so a brief reconnect can still resume a live meeting.
    """
    when = now or datetime.now(timezone.utc)
    ended = 0
    async with session_factory() as db:
        live = (
            await db.execute(select(Session).where(Session.status == "live"))
        ).scalars().all()
        for meeting in live:
            meeting.status = "ended"
            if meeting.ended_at is None:
                meeting.ended_at = when
            ended += 1
        if ended:
            await db.commit()
    return ended
# === ANCHOR: SESSION_SAFETY_SCHEDULER_END_LIVE_AT_STARTUP_END ===


# === ANCHOR: SESSION_SAFETY_SCHEDULER_RUN_WATCHDOG_START ===
async def run_meeting_safety_watchdog(
    interval_seconds: float,
    *,
    session_factory: async_sessionmaker = AsyncSessionLocal,
) -> None:
    """Poll live meetings forever, force-ending over-duration ones each cycle."""
    logger.info(
        "Meeting safety watchdog started", extra={"interval_seconds": interval_seconds}
    )
    while True:
        try:
            ended = await _sweep_once(session_factory)
            if ended:
                logger.info(
                    "Meeting safety watchdog ended sessions", extra={"count": ended}
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Meeting safety watchdog sweep failed")
        await asyncio.sleep(interval_seconds)
# === ANCHOR: SESSION_SAFETY_SCHEDULER_RUN_WATCHDOG_END ===
# === ANCHOR: SESSION_SAFETY_SCHEDULER_END ===
