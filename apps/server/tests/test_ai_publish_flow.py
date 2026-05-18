# === ANCHOR: TEST_AI_PUBLISH_FLOW_START ===
"""S3 AI utterance persistence + viewer bus fan-out tests."""
from __future__ import annotations

from datetime import datetime, timezone
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
# === ANCHOR: TEST_AI_PUBLISH_FLOW_END ===
