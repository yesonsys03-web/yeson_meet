# === ANCHOR: TEST_HEALTH_LOG_FILTER_START ===
"""The uvicorn access-log filter drops polling spam, keeps real requests.

Run with --noconftest (no DB needed); env is pinned before importing main so the
SQLAlchemy engine binds at import time without a live Postgres.
"""
from __future__ import annotations

import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test-health-filter.db")
os.environ.setdefault("JWT_SECRET", "test-secret")

from apps.server.main import _NoisyAccessLogFilter  # noqa: E402

JOB = "0c2f6a1e-1234-4abc-9def-0123456789ab"


def _access_record(
    path: str, method: str = "GET", status: int = 200
) -> logging.LogRecord:
    # Mirror uvicorn.access record args: (client, method, path, http_version, status).
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %s',
        args=("127.0.0.1", method, path, "1.1", status),
        exc_info=None,
    )


def test_drops_health_poll_lines() -> None:
    f = _NoisyAccessLogFilter()
    assert f.filter(_access_record("/api/v1/health/live-sessions")) is False
    assert f.filter(_access_record("/api/v1/health/live")) is False


def test_drops_the_paths_that_burned_the_buffer() -> None:
    """2026-08-05 윈도우 서버 로그에서 1000건 버퍼를 태운 실제 경로들.

    씬 썸네일 342건(35%) + 카탈로그·목록 폴링. 이걸 못 거르면 라이브 회의
    로그가 24분 만에 밀려나 콘솔 내보내기로 복구할 수 없다.
    """
    f = _NoisyAccessLogFilter()
    for path in (
        f"/api/v1/video-jobs/{JOB}/scenes/thumb/170",
        f"/api/v1/video-jobs/{JOB}/scenes/thumb-at?t_ms=1000&h=360",
        f"/api/v1/video-jobs/{JOB}/scenes/boundary-check/status",
        "/api/v1/video-models",
        "/api/v1/video-models/gpu",
        "/api/v1/translate-models",
        "/api/v1/translate-models?refresh=1",
        "/api/v1/video-jobs",
        "/api/v1/video-jobs/translate-engines",
        "/api/v1/video-jobs/storage",
        "/api/v1/pdf-jobs",
    ):
        assert f.filter(_access_record(path)) is False, path


def test_keeps_real_requests() -> None:
    f = _NoisyAccessLogFilter()
    assert f.filter(_access_record("/api/v1/sessions/x/report")) is True
    assert f.filter(_access_record("/api/v1/sessions/x/report.docx")) is True
    assert f.filter(_access_record("/api/v1/auth/login")) is True
    # 같은 트리의 진짜 요청 — 접두사로 뭉뚱그렸다면 여기서 걸린다.
    assert f.filter(_access_record(f"/api/v1/video-jobs/{JOB}")) is True
    assert f.filter(_access_record(f"/api/v1/video-jobs/{JOB}/scenes")) is True
    assert f.filter(_access_record(f"/api/v1/pdf-jobs/{JOB}/labels")) is True


def test_writes_are_never_dropped() -> None:
    """쓰기는 폴링 경로와 URL이 겹쳐도 남긴다 — 그게 실제 사건이다."""
    f = _NoisyAccessLogFilter()
    assert f.filter(_access_record("/api/v1/video-jobs", method="POST")) is True
    assert f.filter(_access_record("/api/v1/pdf-jobs", method="DELETE")) is True


def test_failures_are_never_dropped() -> None:
    """폴링이 깨지기 시작하면 그건 반드시 보여야 한다."""
    f = _NoisyAccessLogFilter()
    assert f.filter(_access_record("/api/v1/health/live", status=500)) is True
    assert f.filter(_access_record("/api/v1/video-models", status=404)) is True
    assert f.filter(_access_record(f"/api/v1/video-jobs/{JOB}/scenes/thumb/3",
                                   status=500)) is True


def test_tolerates_non_access_records() -> None:
    # A record without the uvicorn.access arg shape must not be dropped.
    f = _NoisyAccessLogFilter()
    plain = logging.LogRecord("apps.server", logging.INFO, "", 0, "hello", None, None)
    assert f.filter(plain) is True
    short = _access_record("/api/v1/health/live")
    short.args = ("127.0.0.1", "GET", "/api/v1/health/live")
    assert f.filter(short) is True
# === ANCHOR: TEST_HEALTH_LOG_FILTER_END ===
