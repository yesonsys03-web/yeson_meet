# === ANCHOR: MAIN_START ===
"""yeson-meet FastAPI server entrypoint."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

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


class _HealthAccessLogFilter(logging.Filter):
    """Drop uvicorn access-log lines for the high-frequency health endpoint.

    The server console polls ``/api/v1/health/*`` once per second to keep its
    uptime/PID display live. On a 24/7 server, logging every one of those
    requests would dominate the on-disk logs (~75% of all lines) and grow them
    by tens of MB/day. Real requests still log normally; only health polls are
    dropped. Cross-platform (pure stdlib).
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        args = record.args
        # uvicorn.access record args: (client, method, path, http_version, status)
        if isinstance(args, tuple) and len(args) >= 3:
            if str(args[2]).startswith("/api/v1/health/"):
                return False
        return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Quiet the per-second health-poll access logs so a 24/7 server's logs stay
    # small. Installed here (not at import) so it survives uvicorn's own logging
    # setup, which runs before the lifespan startup.
    logging.getLogger("uvicorn.access").addFilter(_HealthAccessLogFilter())

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

# Bundled-webview origins are always trusted (the packaged client/server consoles
# run from these custom-protocol origins). The localhost dev-server origins
# (vite) are trusted ONLY in dev — the Tauri shell injects `YESON_DEV=1` for a
# debug build (server_process.rs) — so a shipped production server never trusts a
# dev origin (security review finding #1).
_PROD_ORIGINS = [
    "http://tauri.localhost",  # Tauri bundled webview origin (Windows)
    "tauri://localhost",  # Tauri bundled webview origin (macOS/Linux)
    "https://tauri.localhost",  # Tauri bundled webview origin (https custom-protocol variant)
]
_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5274",  # server-console dev webview (apps/server_desktop)
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_PROD_ORIGINS + (_DEV_ORIGINS if os.getenv("YESON_DEV") == "1" else []),
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


# === ANCHOR: MAIN_VIEWER_SPA_START ===
def _web_dist_dir() -> Path | None:
    """Resolve the bundled viewer SPA dist dir.

    Frozen (PyInstaller): ``--add-data "apps/web/dist:web_dist"`` unpacks to
    ``sys._MEIPASS/web_dist`` (mirror ``server_entry.py``'s ``sys.frozen`` path
    resolution — NOT ``__file__``-relative-to-exe). Dev: ``apps/web/dist`` under
    the repo root (built by ``pnpm -C apps/web build``). ``YESON_WEB_DIST`` can
    override either. Returns ``None`` when no dist is present so dev ``/api``,
    ``/ws`` flows and the pytest suite stay unaffected (the mount is skipped).
    """
    override = os.environ.get("YESON_WEB_DIST")
    if override:
        candidate = Path(override)
        return candidate if (candidate / "index.html").is_file() else None
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "web_dist")
    # Dev fallback: repo-root apps/web/dist (this file is apps/server/main.py).
    candidates.append(Path(__file__).resolve().parents[2] / "apps" / "web" / "dist")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


def _mount_viewer_spa() -> None:
    """Serve the viewer SPA + its ``/v/{token}`` route from this same origin.

    Mounted AFTER every ``/api`` + ``/ws`` router so it never shadows them: a
    catch-all that 404s any ``/api``/``/ws`` miss (instead of returning the SPA)
    and otherwise serves a real static file, falling back to ``index.html`` for
    client-side routes (``/v/<token>``, ``/``). Replaces Caddy's old role.
    """
    dist = _web_dist_dir()
    if dist is None:
        logger.warning(
            "Viewer SPA not served: no web dist found (dev /api,/ws unaffected)"
        )
        return

    dist = dist.resolve()
    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_viewer_spa(full_path: str):
        if full_path.startswith(("api/", "ws/")) or full_path in {"api", "ws"}:
            raise HTTPException(status_code=404)
        candidate = (dist / full_path).resolve()
        # Serve a real bundled asset (e.g. assets/index-*.js) when it exists and
        # is safely inside the dist dir; otherwise fall back to the SPA shell so
        # client-side routes (/v/<token>, /) resolve.
        if full_path and dist in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

    logger.info("Viewer SPA mounted", extra={"web_dist": str(dist)})


_mount_viewer_spa()
# === ANCHOR: MAIN_VIEWER_SPA_END ===


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
