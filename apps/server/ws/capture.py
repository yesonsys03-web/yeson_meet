# === ANCHOR: WS_CAPTURE_START ===
"""웹 캡처 전용 WS(/ws/capture) — 첫 메시지 인증(세션 캡처 토큰).

URL 쿼리에 아무것도 싣지 않는다(에지 로그·히스토리 잔존 방지). 인증 후에는
sidecar와 동일한 오디오 계약을 run_capture_stream()으로 재사용한다.
디바이스 바인딩은 없다 — 세션당 1개뿐인 캡처 토큰이 접근을 가둔다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from apps.server.auth.capture_tokens import capture_tokens
from apps.server.db.models import Session
from apps.server.db.session import AsyncSessionLocal
from apps.server.ops.session_safety import enforce_meeting_duration_limit
from apps.server.ws.sidecar import run_capture_stream

router = APIRouter()
logger = logging.getLogger(__name__)

AUTH_TIMEOUT_SECONDS = 5.0


@router.websocket("/ws/capture")
async def ws_capture(ws: WebSocket) -> None:
    # 첫 메시지 인증을 받으려면 accept가 선행되어야 한다(쿼리 인증인 sidecar와
    # 달리 handshake 403이 불가능한 구조 — 실패는 close 1008로만 표현).
    await ws.accept()
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        msg = json.loads(raw)
        session_uuid = UUID(str(msg["session"]))
        token = str(msg["token"])
        if msg.get("type") != "auth":
            raise ValueError("not an auth message")
    except (ValueError, KeyError, TypeError):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    if not capture_tokens.validate(token, session_uuid):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with AsyncSessionLocal() as db:
        meeting = (
            await db.execute(select(Session).where(Session.external_id == session_uuid))
        ).scalar_one_or_none()
        if meeting is None or meeting.status == "ended":
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        if meeting.disconnected_at is not None:
            meeting.disconnected_at = None
            await db.commit()
        if await enforce_meeting_duration_limit(db, meeting):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        session_pk = meeting.id
        meeting_started_at = meeting.started_at

    await ws.send_text(json.dumps({"type": "auth.ok"}))
    logger.info("Capture websocket authenticated", extra={"session_id": str(session_uuid)})
    await run_capture_stream(ws, session_pk, session_uuid, meeting_started_at)
# === ANCHOR: WS_CAPTURE_END ===
