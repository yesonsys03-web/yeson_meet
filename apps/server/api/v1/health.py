# === ANCHOR: HEALTH_START ===
from fastapi import APIRouter

from apps.server.ai.gemini_live import GeminiConfigHealth, gemini_config_health
from apps.server.ai.google_stt_translate import google_stt_translate_health
from apps.server.ops.alerts import sync_gemini_config_alert

router = APIRouter(tags=["health"])


@router.get("/health")
# === ANCHOR: HEALTH_HEALTH_START ===
async def health() -> dict[str, str]:
    return {"status": "ok"}
# === ANCHOR: HEALTH_HEALTH_END ===


@router.get("/health/ai")
async def ai_health() -> dict[str, object]:
    gemini = gemini_config_health()
    sync_gemini_config_alert(gemini["configured"])
    return {"gemini": gemini, "google_stt_translate": google_stt_translate_health()}
# === ANCHOR: HEALTH_END ===
