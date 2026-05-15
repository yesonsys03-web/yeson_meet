"""pytest conftest for yeson-meet server tests.

IMPORTANT: env vars are set BEFORE any apps.server import because
jwt.py reads JWT_SECRET and db/session.py reads DATABASE_URL at module-import time.
"""
from __future__ import annotations

# ── 1. Env vars first — must precede ALL apps.server imports ─────────────────
import os

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest-32bytes-padding!")
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://yeson:6fad32ad29a12088da075219fdeb809d"
    "@127.0.0.1:5432/yeson_meet_test"
)

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
_ADMIN_DSN = f"postgresql://yeson:{_PW}@127.0.0.1:5432/postgres"
_SYNC_DSN = f"postgresql+psycopg://yeson:{_PW}@127.0.0.1:5432/{_TEST_DB}"
_ASYNC_URL = os.environ["DATABASE_URL"]

# ── 4. Create test DB if missing (runs at collection time) ────────────────────
def _ensure_test_db() -> None:
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", [_TEST_DB]
        ).fetchone()
        if not row:
            conn.execute(f'CREATE DATABASE "{_TEST_DB}"')


_ensure_test_db()

# ── 5. Schema setup: drop+create once per session (sync engine, then dispose) ─
@pytest.fixture(scope="session", autouse=True)
def setup_schema() -> None:
    sync_engine = create_engine(_SYNC_DSN, echo=False)
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()


# ── 6. Per-test cleanup via sync psycopg (no event-loop dependency) ───────────
@pytest.fixture(autouse=True)
def clean_tables(setup_schema: None) -> None:  # type: ignore[return]
    yield
    with psycopg.connect(
        f"postgresql://yeson:{_PW}@127.0.0.1:5432/{_TEST_DB}"
    ) as conn:
        conn.execute("DELETE FROM utterance")
        conn.execute("DELETE FROM session_token")
        conn.execute("DELETE FROM session")
        conn.execute("DELETE FROM device")
        conn.execute("DELETE FROM app_user")
        conn.commit()


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
