# === ANCHOR: OPERATOR_WS_START ===
"""Operator WebSocket router (/ws/operator)."""
from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from apps.server.auth.jwt import decode_token
from apps.server.db.models import AppUser, Session
from apps.server.db.session import AsyncSessionLocal
from apps.server.ws.bus import bus

router = APIRouter()


@router.websocket("/ws/operator")
# === ANCHOR: OPERATOR_WS_OPERATOR_START ===
async def ws_operator(ws: WebSocket) -> None:
    access_jwt = ws.query_params.get("access")
    raw_session_id = ws.query_params.get("session")
    if not access_jwt or not raw_session_id:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        session_uuid = UUID(raw_session_id)
        payload = decode_token(access_jwt)
        if payload.get("kind") != "access":
            raise ValueError("wrong token kind")
        user_id = int(payload["sub"])
    except Exception:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(
                select(AppUser).where(
                    AppUser.id == user_id,
                    AppUser.is_active == True,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if user is None or user.role not in ("admin", "operator"):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        meeting = (
            await db.execute(select(Session).where(Session.external_id == session_uuid))
        ).scalar_one_or_none()
        if meeting is None:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
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

    async def _drain_incoming() -> None:
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            return

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
            await ws.send_json(next_payload.result())
    except (RuntimeError, WebSocketDisconnect):
        return
    finally:
        bus.unsubscribe(session_uuid, q)
        _ = drain.cancel()
# === ANCHOR: OPERATOR_WS_OPERATOR_END ===
# === ANCHOR: OPERATOR_WS_END ===
