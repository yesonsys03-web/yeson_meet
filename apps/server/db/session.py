# === ANCHOR: SESSION_START ===
"""Async DB engine/session factory."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://yeson:devpw@localhost:5432/yeson_meet",
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# === ANCHOR: SESSION_GET_SESSION_START ===
async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an AsyncSession."""
    async with AsyncSessionLocal() as session:
        yield session
# === ANCHOR: SESSION_GET_SESSION_END ===
# === ANCHOR: SESSION_END ===
