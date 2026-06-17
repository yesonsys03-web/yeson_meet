"""S3 meeting max-duration safety tests."""
from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.password import hash_password
from apps.server.db.models import AppUser, Session
from apps.server.ops.alerts import MEETING_MAX_DURATION_EXCEEDED, operator_alerts
from apps.server.ops.session_safety import enforce_meeting_duration_limit


@pytest.fixture(autouse=True)
def reset_operator_alerts() -> Generator[None]:
    operator_alerts.reset()
    yield
    operator_alerts.reset()


async def _create_meeting(
    db_session: AsyncSession,
    started_at: datetime,
) -> Session:
    admin = AppUser(
        email=f"session-safety-{uuid4()}@test.example",
        name="Session Safety",
        password_hash=hash_password("pw"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    await db_session.flush()

    meeting = Session(
        external_id=uuid4(),
        owner_user_id=admin.id,
        title="Max Duration Test",
        status="live",
        started_at=started_at,
    )
    db_session.add(meeting)
    await db_session.flush()
    return meeting


@pytest.mark.asyncio
async def test_new_session_disconnected_at_defaults_none(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(minutes=1))
    assert meeting.disconnected_at is None


@pytest.mark.asyncio
async def test_enforce_meeting_duration_limit_ends_overdue_session(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(hours=3, minutes=1))

    enforced = await enforce_meeting_duration_limit(db_session, meeting, now=now)

    assert enforced is True
    assert meeting.status == "ended"
    assert meeting.ended_at == now
    alerts = operator_alerts.active()
    assert len(alerts) == 1
    assert alerts[0].code == f"{MEETING_MAX_DURATION_EXCEEDED}:{meeting.external_id}"
    assert alerts[0].severity == "critical"


@pytest.mark.asyncio
async def test_enforce_meeting_duration_limit_leaves_active_session_live(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(hours=2))

    enforced = await enforce_meeting_duration_limit(db_session, meeting, now=now)

    assert enforced is False
    assert meeting.status == "live"
    assert meeting.ended_at is None
    assert operator_alerts.active() == []


@pytest.mark.asyncio
async def test_enforce_meeting_duration_limit_publishes_session_ended(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.server.ws.bus import bus

    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(hours=3, minutes=1))

    queue = bus.subscribe(meeting.external_id)
    try:
        enforced = await enforce_meeting_duration_limit(db_session, meeting, now=now)
        assert enforced is True
        payload = queue.get_nowait()
    finally:
        bus.unsubscribe(meeting.external_id, queue)

    assert payload["type"] == "session.ended"
    assert payload["session_id"] == str(meeting.external_id)


@pytest.mark.asyncio
async def test_enforce_meeting_duration_limit_active_session_does_not_publish(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.server.ws.bus import bus

    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(hours=2))

    queue = bus.subscribe(meeting.external_id)
    try:
        enforced = await enforce_meeting_duration_limit(db_session, meeting, now=now)
        assert enforced is False
        assert queue.empty()
    finally:
        bus.unsubscribe(meeting.external_id, queue)
