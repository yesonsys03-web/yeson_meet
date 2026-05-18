# === ANCHOR: TEST_AI_PUBLISH_FLOW_START ===
"""S3 AI utterance persistence + viewer bus fan-out tests."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.ai.providers import TranslatedUtterance
from apps.server.auth.password import hash_password
from apps.server.db.models import AppUser, Session, Utterance
from apps.server.ws.bus import bus
from apps.server.ws.sidecar import _persist_and_publish_ai_utterance


@pytest.mark.asyncio
async def test_persist_and_publish_ai_utterance(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.server.ws import sidecar

    class DbSessionContext:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(sidecar, "AsyncSessionLocal", lambda: DbSessionContext())

    admin = AppUser(
        email="ai-publish@test.example",
        name="AI Publish",
        password_hash=hash_password("pw"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    await db_session.flush()

    session_uuid = uuid4()
    meeting = Session(
        external_id=session_uuid,
        owner_user_id=admin.id,
        title="AI Publish Test",
        status="live",
    )
    db_session.add(meeting)
    await db_session.flush()
    await db_session.commit()

    queue = bus.subscribe(session_uuid)
    now = datetime.now(timezone.utc)
    try:
        await _persist_and_publish_ai_utterance(
            meeting.id,
            session_uuid,
            TranslatedUtterance(
                seq=1,
                text_en="Please review the layout.",
                text_ko="layout을 검토해 주세요.",
                started_at=now,
                ended_at=now,
                is_final=True,
            ),
        )
        payload = await queue.get()
    finally:
        bus.unsubscribe(session_uuid, queue)

    assert payload["type"] == "utterance.transcribed"
    assert payload["text_en"] == "Please review the layout."
    assert payload["text_ko"] == "layout을 검토해 주세요."

    row = (
        await db_session.execute(
            select(Utterance).where(
                Utterance.session_id == meeting.id,
                Utterance.seq == 1,
            )
        )
    ).scalar_one()
    assert row.text_en == "Please review the layout."
    assert row.text_ko == "layout을 검토해 주세요."


@pytest.mark.asyncio
async def test_persist_and_publish_ai_utterance_logs_latency(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from apps.server.ws import sidecar

    class DbSessionContext:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(sidecar, "AsyncSessionLocal", lambda: DbSessionContext())

    admin = AppUser(
        email="ai-latency@test.example",
        name="AI Latency",
        password_hash=hash_password("pw"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    await db_session.flush()

    session_uuid = uuid4()
    meeting = Session(
        external_id=session_uuid,
        owner_user_id=admin.id,
        title="AI Latency Test",
        status="live",
    )
    db_session.add(meeting)
    await db_session.flush()
    await db_session.commit()

    now = datetime.now(timezone.utc)
    queue = bus.subscribe(session_uuid)
    try:
        with caplog.at_level(logging.INFO, logger="apps.server.ws.sidecar"):
            await _persist_and_publish_ai_utterance(
                meeting.id,
                session_uuid,
                TranslatedUtterance(
                    seq=7,
                    text_en="Latency sample.",
                    text_ko="지연 샘플.",
                    started_at=now,
                    ended_at=now,
                    is_final=True,
                ),
            )
        await queue.get()
    finally:
        bus.unsubscribe(session_uuid, queue)

    record = next(
        item for item in caplog.records if item.message == "AI utterance published"
    )
    assert record.session_id == str(session_uuid)
    assert record.seq == 7
    assert isinstance(record.ai_publish_latency_ms, int)
    assert record.ai_publish_latency_ms >= 0
# === ANCHOR: TEST_AI_PUBLISH_FLOW_END ===
