"""pytest conftest for yeson-meet server tests.

IMPORTANT: env vars are set BEFORE any apps.server import because
jwt.py reads JWT_SECRET and db/session.py reads DATABASE_URL at module-import time.

기본 DB는 로컬 Postgres(deploy/docker-compose.yml)다. `TEST_DATABASE_URL`로 다른
엔진을 지정할 수 있다 — Docker가 없는 환경에서 대부분의 테스트를 돌리는 용도:

    TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests

SQLite에서는 20개가 실패한다(2026-07-27 실측: 990 passed / 20 failed). 대부분
엔진 전용 테스트(psycopg 직접 접속·PG 리스너)이거나 테스트 DB가 SQLite FTS5
가상 테이블을 만들지 않아서다. 전량 통과가 필요하면 Postgres로 돌려야 한다.
"""
from __future__ import annotations

# ── 1. Env vars first — must precede ALL apps.server imports ─────────────────
import os

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest-32bytes-padding!")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or (
    "postgresql+asyncpg://yeson:6fad32ad29a12088da075219fdeb809d"
    "@127.0.0.1:5432/yeson_meet_test"
)
# Session-report writes (end_session) must not touch the prod default
# /var/lib/yeson-meet/storage — point them at a writable temp dir for tests.
import tempfile

os.environ.setdefault(
    "STORAGE_ROOT", os.path.join(tempfile.gettempdir(), "yeson-meet-test-storage")
)
# end_session generates a report which (when enabled) shells out to a local
# claude/codex CLI for the LLM summary. Tests must never make real LLM calls,
# so default the feature OFF; a test that exercises summaries can monkeypatch
# YESON_REPORT_SUMMARY back on (with generate_summary itself mocked).
os.environ.setdefault("YESON_REPORT_SUMMARY", "0")

# ── 2. Now safe to import server modules ─────────────────────────────────────
import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.server.auth.password import hash_password
from apps.server.db.base import Base
from apps.server.db.models import AppUser
from apps.server.db.session import get_session
from apps.server.main import app

# ── 3. Connection strings ─────────────────────────────────────────────────────
_PW = "6fad32ad29a12088da075219fdeb809d"
_TEST_DB = "yeson_meet_test"
# _ADMIN_DSN은 테스트 DB를 만들기 위한 것 — 기본(로컬 compose Postgres)에만 쓰인다.
_ADMIN_DSN = f"postgresql://yeson:{_PW}@127.0.0.1:5432/postgres"
_ASYNC_URL = os.environ["DATABASE_URL"]
_IS_PG = _ASYNC_URL.startswith("postgresql")
# 동기 DSN은 비동기 URL에서 드라이버만 바꿔 파생시킨다 — 따로 적어두면
# TEST_DATABASE_URL로 바꿔도 스키마 생성만 옛 DB를 가리키는 사고가 난다.
_SYNC_DSN = (_ASYNC_URL.replace("+asyncpg", "+psycopg") if _IS_PG
             else _ASYNC_URL.replace("+aiosqlite", ""))

# ── 4. Create test DB if missing (runs at collection time) ────────────────────
def _ensure_test_db() -> None:
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", [_TEST_DB]
        ).fetchone()
        if not row:
            conn.execute(f'CREATE DATABASE "{_TEST_DB}"')



# ── 5. Schema setup: drop+create once per session (sync engine, then dispose) ─
@pytest.fixture(scope="session", autouse=True)
def setup_schema() -> None:
    # 접속은 세션 시작 시점에 한다(임포트 시점 금지) — 임포트에서 죽으면 pytest가
    # ImportError로 감싸 "무엇을 하면 되는지"가 안 보인다.
    if _IS_PG:
        try:
            _ensure_test_db()
        except psycopg.OperationalError as exc:
            pytest.exit(
                f"테스트 DB(Postgres)에 접속할 수 없습니다: {exc}\n"
                "  · Postgres 기동: "
                "docker compose -f deploy/docker-compose.yml up -d postgres\n"
                "  · 또는 SQLite로 실행(일부 실패): TEST_DATABASE_URL="
                '"sqlite+aiosqlite:///$(mktemp -d)/t.db" pytest apps/server/tests',
                returncode=4,
            )
    sync_engine = create_engine(_SYNC_DSN, echo=False)
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()


# ── 6. Per-test cleanup via sync psycopg (no event-loop dependency) ───────────
@pytest.fixture(autouse=True)
def clean_tables(setup_schema: None) -> None:  # type: ignore[return]
    yield
    # 엔진 중립 정리 — 외래키 역순으로 전 테이블 DELETE(테이블 목록이 모델에서
    # 파생되므로 새 테이블이 생겨도 빠뜨리지 않는다).
    sync_engine = create_engine(_SYNC_DSN, echo=False)
    with sync_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    sync_engine.dispose()


# ── 7. Per-test async engine + session (fresh engine per test = no loop reuse) -
@pytest_asyncio.fixture
async def db_session() -> AsyncSession:  # type: ignore[return]
    """Fresh async engine per test; yields an AsyncSession; tears down after."""
    engine = create_async_engine(_ASYNC_URL, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


# ── 8. HTTP client with DB dependency override ────────────────────────────────
@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncClient:  # type: ignore[return]
    """AsyncClient wired to the test DB via dependency_overrides."""

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_session, None)


# ── 9. Admin user (insert via the overridden test session) ────────────────────
@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> AppUser:
    user = AppUser(
        email="admin@test.example",
        name="Test Admin",
        password_hash=hash_password("test-admin-pw"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# ── 10. Admin JWT token ───────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def admin_token(admin_user: AppUser, client: AsyncClient) -> str:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "test-admin-pw"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]
