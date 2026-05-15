"""Sidecar WebSocket router (/ws/sidecar). Implemented in S1-L1.

Sidecar (capture device) connects with ?key=<device_api_key>&session=<external_uuid>,
streams `utterance.transcribed` JSON frames, server persists with idempotency on
(session_id, seq) and fans out via `bus` to /ws/viewer subscribers.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.server.auth.device import hash_api_key
from apps.server.db.models import Device, Session, Utterance
from apps.server.db.session import AsyncSessionLocal
from apps.server.domain.events import UtteranceTranscribed, serialize
from apps.server.ws.bus import bus

router = APIRouter()


@router.websocket("/ws/sidecar")
async def ws_sidecar(ws: WebSocket) -> None:
    key = ws.query_params.get("key")
    session_str = ws.query_params.get("session")
    if not key or not session_str:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        session_uuid = UUID(session_str)
    except ValueError:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Authenticate device by hash + resolve session
    async with AsyncSessionLocal() as db:
        hashed = hash_api_key(key)
        device = (
            await db.execute(
                select(Device).where(
                    Device.api_key_hash == hashed,
                    Device.is_active == True,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if device is None:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        meeting = (
            await db.execute(select(Session).where(Session.external_id == session_uuid))
        ).scalar_one_or_none()
        if meeting is None or meeting.status == "ended":
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        session_pk = meeting.id

    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                evt = UtteranceTranscribed.model_validate_json(raw)
            except ValidationError:
                await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                return

            async with AsyncSessionLocal() as db:
                stmt = (
                    pg_insert(Utterance)
                    .values(
                        session_id=session_pk,
                        seq=evt.seq,
                        speaker=evt.speaker,
                        text_en=evt.text_en,
                        text_ko=evt.text_ko,
                        started_at=evt.started_at,
                        ended_at=evt.ended_at,
                        is_final=evt.is_final,
                    )
                    .on_conflict_do_nothing(index_elements=["session_id", "seq"])
                )
                await db.execute(stmt)
                await db.commit()

            await bus.publish(session_uuid, serialize(evt))
    except WebSocketDisconnect:
        return
