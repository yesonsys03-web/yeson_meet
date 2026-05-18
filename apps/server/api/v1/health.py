# === ANCHOR: HEALTH_START ===
from fastapi import APIRouter

from apps.server.ai.gemini_live import GeminiConfigHealth, gemini_config_health

router = APIRouter(tags=["health"])


@router.get("/health")
# === ANCHOR: HEALTH_HEALTH_START ===
async def health() -> dict[str, str]:
    return {"status": "ok"}
# === ANCHOR: HEALTH_HEALTH_END ===


@router.get("/health/ai")
async def ai_health() -> dict[str, GeminiConfigHealth]:
    return {"gemini": gemini_config_health()}
# === ANCHOR: HEALTH_END ===
