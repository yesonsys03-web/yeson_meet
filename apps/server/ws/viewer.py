# === ANCHOR: VIEWER_START ===
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
# === ANCHOR: VIEWER_WS_VIEWER_START ===
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
        if meeting is None:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        session_uuid = meeting.external_id
        session_status = meeting.status
        ended_at = meeting.ended_at

    await ws.accept()
    if session_status == "ended" and ended_at is not None:
        await ws.send_json(
            {
                "type": "session.ended",
                "session_id": str(session_uuid),
                "occurred_at": ended_at.isoformat(),
                "ended_at": ended_at.isoformat(),
            }
        )
        await ws.close()
        return
    q = bus.subscribe(session_uuid)

    # === ANCHOR: VIEWER__DRAIN_INCOMING_START ===
    async def _drain_incoming() -> None:
        # Read-only stream; drop anything the viewer sends.
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            return
    # === ANCHOR: VIEWER__DRAIN_INCOMING_END ===

    drain = asyncio.create_task(_drain_incoming())
    try:
        while True:
            next_payload = asyncio.create_task(q.get())
            done, pending = await asyncio.wait(
                {drain, next_payload},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if drain in done:
                for task in pending:
                    task.cancel()
                return
            payload = next_payload.result()
            await ws.send_json(payload)
    except (RuntimeError, WebSocketDisconnect):
        return
    finally:
# === ANCHOR: VIEWER_WS_VIEWER_END ===
        bus.unsubscribe(session_uuid, q)
        drain.cancel()
# === ANCHOR: VIEWER_END ===
