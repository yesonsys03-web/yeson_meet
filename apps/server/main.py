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
from apps.server.api.v1.backup import router as backup_router
from apps.server.api.v1.devices import router as devices_router
from apps.server.api.v1.glossary import router as glossary_router
from apps.server.api.v1.health import router as health_router
from apps.server.api.v1.operator_alerts import router as operator_alerts_router
from apps.server.api.v1.sessions import router as sessions_router
from apps.server.api.v1.utterances import router as utterances_router
from apps.server.api.v1.audio_stats import router as audio_stats_router
from apps.server.api.v1.video_models import router as video_models_router
from apps.server.api.v1.translate_models import router as translate_models_router
from apps.server.api.v1.video_jobs import router as video_jobs_router
from apps.server.api.v1.reports import router as reports_router
from apps.server.ai.gemini_live import gemini_config_health
from apps.server.domain.video_captions.pipeline import (
    clear_stale_scan_flags_at_startup,
    fail_inflight_video_jobs_at_startup,
    prune_old_video_jobs_at_startup,
)
from apps.server.ops.alerts import sync_gemini_config_alert
from apps.server.ops.session_safety_scheduler import (
    end_live_sessions_at_startup,
    run_meeting_safety_watchdog,
    safety_poll_interval,
)
from apps.server.db.session import AsyncSessionLocal
from apps.server.ws.capture import router as ws_capture_router
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


# 앱 로그가 나가는 두 뿌리. "apps.server"만 설정하면 "yeson.*"(영상 파이프라인·
# ffmpeg·OCR)은 핸들러도 레벨도 없는 상태라 INFO가 통째로 버려진다 — 실기: 윈도우
# "익스포트 파일이 안 생긴다" 신고에서 서버 로그 1000줄이 전부 uvicorn 액세스
# 로그였고, 익스포트가 '어디에 썼는지' 남기는 진단 로그가 한 줄도 없었다.
# (ERROR만 파이썬 lastResort로 stderr에 새어 나와 더 헷갈렸다.)
_LOG_ROOTS = ("apps.server", "yeson")

for _name in _LOG_ROOTS:
    _lg = logging.getLogger(_name)
    _lg.setLevel(logging.INFO)
    if not _lg.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(ExtraFormatter("%(levelname)s:%(name)s:%(message)s"))
        _lg.addHandler(handler)
server_logger = logging.getLogger("apps.server")
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

    # Fresh boot has no sidecar connected, so any lingering "live" session is a
    # ghost from a previous run that didn't end cleanly (app force-quit, crash,
    # or client hard-reload). End them immediately so restarting the app gives a
    # clean environment and the go-public guard isn't blocked by a phantom
    # meeting. Runtime sidecar drops (server still up) are handled by the
    # grace-based watchdog below, so a brief reconnect can still resume. Runs
    # regardless of the watchdog interval.
    try:
        ended = await end_live_sessions_at_startup(AsyncSessionLocal)
        if ended:
            logger.info("Ended stale live sessions at startup", extra={"count": ended})
    except Exception:
        logger.exception("Startup stale-session cleanup failed")

    # Video caption jobs run as in-process asyncio tasks with no resume path, so a
    # restart mid-job leaves it permanently stuck in an in-flight status. Sweep
    # those to error at startup — same rationale as the stale live-session sweep.
    try:
        await fail_inflight_video_jobs_at_startup()
    except Exception:
        logger.exception("Startup video-job sweep failed")

    # 위 스윕은 DB의 job 상태만 본다. 씬 분할의 진행 플래그는 작업 폴더 JSON에
    # 있어 재시작 뒤에도 '실행중'으로 남는다(뒤에 도는 작업은 없는데도) — 같은
    # 이유로 함께 내린다.
    try:
        await clear_stale_scan_flags_at_startup()
    except Exception:
        logger.exception("Startup scene-flag sweep failed")

    # 자막 메이커 작업 폴더(원본/preview/burned mp4)가 무한정 쌓이지 않도록, 스윕
    # 직후 최근 RETENTION_KEEP개만 남기고 오래된 작업을 회수한다. 스윕과 동일한
    # '다른 인스턴스가 서빙 중' 가드로 보호되므로(이중 기동된 비소유 프로세스는
    # 프루닝하지 않음), 살아있는 인스턴스의 작업을 지우지 않는다.
    try:
        await prune_old_video_jobs_at_startup()
    except Exception:
        logger.exception("Startup video-job retention prune failed")

    interval = safety_poll_interval()
    if interval > 0:
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
app.include_router(backup_router, prefix="/api/v1")
app.include_router(devices_router, prefix="/api/v1")
app.include_router(glossary_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")
app.include_router(utterances_router, prefix="/api/v1")
app.include_router(audio_stats_router, prefix="/api/v1")
app.include_router(operator_alerts_router, prefix="/api/v1")
app.include_router(video_models_router, prefix="/api/v1")
app.include_router(translate_models_router, prefix="/api/v1")
app.include_router(video_jobs_router, prefix="/api/v1")
app.include_router(reports_router, prefix="/api/v1")
app.include_router(ws_operator_router)
app.include_router(ws_sidecar_router)
app.include_router(ws_capture_router)
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
