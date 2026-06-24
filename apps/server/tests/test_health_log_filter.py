# === ANCHOR: TEST_HEALTH_LOG_FILTER_START ===
"""The uvicorn access-log filter drops health-poll spam, keeps real requests.

Run with --noconftest (no DB needed); env is pinned before importing main so the
SQLAlchemy engine binds at import time without a live Postgres.
"""
from __future__ import annotations

import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test-health-filter.db")
os.environ.setdefault("JWT_SECRET", "test-secret")

from apps.server.main import _HealthAccessLogFilter  # noqa: E402


def _access_record(path: str) -> logging.LogRecord:
    # Mirror uvicorn.access record args: (client, method, path, http_version, status).
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %s',
        args=("127.0.0.1", "GET", path, "1.1", 200),
        exc_info=None,
    )


def test_drops_health_poll_lines() -> None:
    f = _HealthAccessLogFilter()
    assert f.filter(_access_record("/api/v1/health/live-sessions")) is False
    assert f.filter(_access_record("/api/v1/health/live")) is False


def test_keeps_real_requests() -> None:
    f = _HealthAccessLogFilter()
    assert f.filter(_access_record("/api/v1/sessions/x/report")) is True
    assert f.filter(_access_record("/api/v1/sessions/x/report.docx")) is True
    assert f.filter(_access_record("/api/v1/auth/login")) is True


def test_tolerates_non_access_records() -> None:
    # A record without the uvicorn.access arg shape must not be dropped.
    f = _HealthAccessLogFilter()
    plain = logging.LogRecord("apps.server", logging.INFO, "", 0, "hello", None, None)
    assert f.filter(plain) is True
# === ANCHOR: TEST_HEALTH_LOG_FILTER_END ===
