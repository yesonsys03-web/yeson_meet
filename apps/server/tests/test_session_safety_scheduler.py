"""Background meeting-safety watchdog tests."""
from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.server.auth.password import hash_password
from apps.server.db.models import AppUser, Session
from apps.server.ops.alerts import operator_alerts
from apps.server.ops.session_safety_scheduler import (
    _sweep_once,
    run_meeting_safety_watchdog,
    safety_poll_interval,
)


@pytest.fixture(autouse=True)
def reset_operator_alerts() -> Generator[None]:
    operator_alerts.reset()
    yield
    operator_alerts.reset()


async def _create_live_meeting(db_session: AsyncSession, started_at: datetime) -> Session:
    admin = AppUser(
        email=f"sched-{uuid4()}@test.example",
        name="Scheduler Test",
        password_hash=hash_password("pw"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    await db_session.flush()
    meeting = Session(
        external_id=uuid4(),
        owner_user_id=admin.id,
        title="Scheduler Max Duration",
        status="live",
        started_at=started_at,
    )
    db_session.add(meeting)
    await db_session.commit()
    return meeting


def _factory(db_session: AsyncSession) -> async_sessionmaker:
    return async_sessionmaker(
        db_session.bind, expire_on_commit=False, class_=AsyncSession
    )


def test_safety_poll_interval_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YESON_MEETING_SAFETY_POLL_SECONDS", raising=False)
    assert safety_poll_interval() == 60.0


def test_safety_poll_interval_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YESON_MEETING_SAFETY_POLL_SECONDS", "5")
    assert safety_poll_interval() == 5.0


@pytest.mark.asyncio
async def test_sweep_ends_overdue_session(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    now = datetime.now(timezone.utc)
    meeting = await _create_live_meeting(db_session, now - timedelta(hours=3, minutes=1))

    ended = await _sweep_once(_factory(db_session))

    assert ended == 1
    async with _factory(db_session)() as db2:
        refreshed = (
            await db2.execute(select(Session).where(Session.id == meeting.id))
        ).scalar_one()
    assert refreshed.status == "ended"
    assert len(operator_alerts.active()) == 1


@pytest.mark.asyncio
async def test_sweep_keeps_fresh_session_live(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    now = datetime.now(timezone.utc)
    meeting = await _create_live_meeting(db_session, now - timedelta(hours=1))

    ended = await _sweep_once(_factory(db_session))

    assert ended == 0
    async with _factory(db_session)() as db2:
        refreshed = (
            await db2.execute(select(Session).where(Session.id == meeting.id))
        ).scalar_one()
    assert refreshed.status == "live"
    assert operator_alerts.active() == []


@pytest.mark.asyncio
async def test_watchdog_sweeps_then_cancels_cleanly(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    now = datetime.now(timezone.utc)
    meeting = await _create_live_meeting(db_session, now - timedelta(hours=3, minutes=1))

    task = asyncio.create_task(
        run_meeting_safety_watchdog(0.01, session_factory=_factory(db_session))
    )
    # Give the loop time to run at least one sweep.
    for _ in range(50):
        await asyncio.sleep(0.01)
        async with _factory(db_session)() as db2:
            refreshed = (
                await db2.execute(select(Session).where(Session.id == meeting.id))
            ).scalar_one()
        if refreshed.status == "ended":
            break

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert refreshed.status == "ended"
    assert len(operator_alerts.active()) == 1
