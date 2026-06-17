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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.server.db.models import Session
from apps.server.db.session import AsyncSessionLocal
from apps.server.ops.session_safety import enforce_meeting_duration_limit

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
    """Force-end every over-duration live meeting; return how many were ended."""
    ended = 0
    async with session_factory() as db:
        live = (
            await db.execute(select(Session).where(Session.status == "live"))
        ).scalars().all()
        for meeting in live:
            if await enforce_meeting_duration_limit(db, meeting):
                ended += 1
    return ended
# === ANCHOR: SESSION_SAFETY_SCHEDULER_SWEEP_ONCE_END ===


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
