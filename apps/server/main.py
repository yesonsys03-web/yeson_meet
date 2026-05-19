# === ANCHOR: MAIN_START ===
"""yeson-meet FastAPI server entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.server.api.v1.auth import router as auth_router
from apps.server.api.v1.devices import router as devices_router
from apps.server.api.v1.health import router as health_router
from apps.server.api.v1.operator_alerts import router as operator_alerts_router
from apps.server.api.v1.sessions import router as sessions_router
from apps.server.api.v1.utterances import router as utterances_router
from apps.server.api.v1.audio_stats import router as audio_stats_router
from apps.server.ai.gemini_live import gemini_config_health
from apps.server.ops.alerts import sync_gemini_config_alert
from apps.server.ws.sidecar import router as ws_sidecar_router
from apps.server.ws.viewer import router as ws_viewer_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Alembic upgrade is run via deploy script or compose entrypoint; do not run here
    # to keep dev/prod start identical. Health endpoint stays cheap.
    gemini_health = gemini_config_health()
    sync_gemini_config_alert(gemini_health["configured"])
    if gemini_health["configured"]:
        logger.info("Gemini Live configured", extra={"model": gemini_health["model"]})
    else:
        logger.warning("Gemini Live disabled: GEMINI_API_KEY is not configured")
    yield


app = FastAPI(title="yeson-meet", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://tauri.localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(devices_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")
app.include_router(utterances_router, prefix="/api/v1")
app.include_router(audio_stats_router, prefix="/api/v1")
app.include_router(operator_alerts_router, prefix="/api/v1")
app.include_router(ws_sidecar_router)
app.include_router(ws_viewer_router)


# === ANCHOR: MAIN_RUN_START ===
def run() -> None:
    import uvicorn

    uvicorn.run("apps.server.main:app", host="0.0.0.0", port=8000, reload=False)
# === ANCHOR: MAIN_RUN_END ===


if __name__ == "__main__":
    run()
# === ANCHOR: MAIN_END ===
