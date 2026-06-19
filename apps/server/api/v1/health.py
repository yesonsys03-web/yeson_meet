# === ANCHOR: HEALTH_START ===
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.ai.gemini_live import GeminiConfigHealth, gemini_config_health
from apps.server.ai.google_stt_translate import google_stt_translate_health
from apps.server.db.models import Session
from apps.server.db.session import get_session
from apps.server.ops.alerts import sync_gemini_config_alert

router = APIRouter(tags=["health"])


@router.get("/health")
# === ANCHOR: HEALTH_HEALTH_START ===
async def health() -> dict[str, str]:
    return {"status": "ok"}
# === ANCHOR: HEALTH_HEALTH_END ===


@router.get("/health/live-sessions")
# === ANCHOR: HEALTH_LIVE_SESSIONS_START ===
async def live_sessions(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    """Read-only count of currently-live meetings (``status == "live"``).

    Authoritative, cross-process, restart-surviving signal: it is the SAME DB
    flag ``create_session`` sets (``sessions.py``), ``end_session`` clears, and
    the safety watchdog queries (``session_safety_scheduler.py``). The packaged
    Tauri shell GETs this over loopback BEFORE restarting the server to gate the
    restart on "no live meeting" (the restart SIGTERMs the server's process
    group and would hard-kill any active meeting). Exposes only a count — no PII
    — and is intentionally NOT on the public-tunnel allowlist (LAN-only).
    """
    count = (
        await db.execute(
            select(func.count()).select_from(Session).where(Session.status == "live")
        )
    ).scalar_one()
    return {"live": int(count)}
# === ANCHOR: HEALTH_LIVE_SESSIONS_END ===


@router.get("/health/ai")
async def ai_health() -> dict[str, object]:
    gemini = gemini_config_health()
    sync_gemini_config_alert(gemini["configured"])
    return {"gemini": gemini, "google_stt_translate": google_stt_translate_health()}
# === ANCHOR: HEALTH_END ===
