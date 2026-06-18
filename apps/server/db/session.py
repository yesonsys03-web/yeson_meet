# === ANCHOR: SESSION_START ===
"""Async DB engine/session factory."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://yeson:devpw@localhost:5432/yeson_meet",
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """Enable FK enforcement + WAL on every SQLite connection (packaged app).

    On the Postgres deploy this connect listener is never registered, so the PG
    path stays identical (``pool_pre_ping=True`` etc. unchanged).
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


if DATABASE_URL.startswith("sqlite"):
    # Attach to the underlying sync engine so the pragma runs on the real DBAPI
    # connection regardless of the async (aiosqlite) wrapper.
    event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)


# === ANCHOR: SESSION_GET_SESSION_START ===
async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an AsyncSession."""
    async with AsyncSessionLocal() as session:
        yield session
# === ANCHOR: SESSION_GET_SESSION_END ===
# === ANCHOR: SESSION_END ===
