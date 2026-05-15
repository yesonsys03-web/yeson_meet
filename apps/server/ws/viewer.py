"""Viewer WebSocket router (/ws/viewer). Implemented in S1-L1.

Viewer (browser) connects with ?token=<session_token>, server resolves the
session, subscribes to `bus`, and streams `utterance.transcribed` events.
Incoming client messages are ignored (read-only fan-out).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from apps.server.db.models import Session, SessionToken
from apps.server.db.session import AsyncSessionLocal
from apps.server.ws.bus import bus

router = APIRouter()


@router.websocket("/ws/viewer")
async def ws_viewer(ws: WebSocket) -> None:
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with AsyncSessionLocal() as db:
        token_row = (
            await db.execute(select(SessionToken).where(SessionToken.token == token))
        ).scalar_one_or_none()
        if token_row is None:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        if (
            token_row.expires_at is not None
            and token_row.expires_at < datetime.now(timezone.utc)
        ):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        meeting = (
            await db.execute(select(Session).where(Session.id == token_row.session_id))
        ).scalar_one_or_none()
        if meeting is None or meeting.status == "ended":
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        session_uuid = meeting.external_id

    await ws.accept()
    q = bus.subscribe(session_uuid)

    async def _drain_incoming() -> None:
        # Read-only stream; drop anything the viewer sends.
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            return

    drain = asyncio.create_task(_drain_incoming())
    try:
        while True:
            payload = await q.get()
            await ws.send_json(payload)
    except WebSocketDisconnect:
        return
    finally:
        bus.unsubscribe(session_uuid, q)
        drain.cancel()
