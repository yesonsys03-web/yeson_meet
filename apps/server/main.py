# === ANCHOR: MAIN_START ===
"""yeson-meet FastAPI server entrypoint."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
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
from apps.server.ops.session_safety_scheduler import (
    run_meeting_safety_watchdog,
    safety_poll_interval,
    stamp_live_sessions_disconnected,
)
from apps.server.db.session import AsyncSessionLocal
from apps.server.ws.operator import router as ws_operator_router
from apps.server.ws.sidecar import router as ws_sidecar_router
from apps.server.ws.viewer import router as ws_viewer_router

_LOG_RECORD_KEYS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class ExtraFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _LOG_RECORD_KEYS
            and key not in {"asctime", "message"}
            and not key.startswith("_")
        }
        if not extras:
            return message
        fields = " ".join(f"{key}={value}" for key, value in sorted(extras.items()))
        return f"{message} {fields}"


server_logger = logging.getLogger("apps.server")
server_logger.setLevel(logging.INFO)
if not server_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(ExtraFormatter("%(levelname)s:%(name)s:%(message)s"))
    server_logger.addHandler(handler)
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

    interval = safety_poll_interval()
    if interval > 0:
        try:
            stamped = await stamp_live_sessions_disconnected(AsyncSessionLocal)
            if stamped:
                logger.info(
                    "Stamped live sessions disconnected at startup",
                    extra={"count": stamped},
                )
        except Exception:
            logger.exception("Startup disconnect re-stamp failed")
        watchdog = asyncio.create_task(run_meeting_safety_watchdog(interval))
    else:
        watchdog = None
        logger.info("Meeting safety watchdog disabled (poll interval <= 0)")
    try:
        yield
    finally:
        if watchdog is not None:
            watchdog.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog


app = FastAPI(title="yeson-meet", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://tauri.localhost",  # Tauri bundled webview origin (Windows)
        "tauri://localhost",  # Tauri bundled webview origin (macOS/Linux)
        "https://tauri.localhost",  # Tauri bundled webview origin (https custom-protocol variant)
    ],
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
app.include_router(ws_operator_router)
app.include_router(ws_sidecar_router)
app.include_router(ws_viewer_router)


# === ANCHOR: MAIN_RUN_START ===
def run() -> None:
    import os
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("apps.server.main:app", host=host, port=port, reload=False)
# === ANCHOR: MAIN_RUN_END ===


if __name__ == "__main__":
    run()
# === ANCHOR: MAIN_END ===
