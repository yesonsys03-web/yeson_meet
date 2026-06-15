# === ANCHOR: FIXTURE_EMITTER_START ===
"""Fixture utterance generator. 1초마다 PRD 부록 B sample round-robin 발화.

Slice 1: 오디오 캡처/Gemini 호출 없이 자막 fan-out 골격만 검증.
fixture 모드 전용 — 실제 캡처는 native 헬퍼(NativePipeSource)가 담당.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID

from apps.client_sidecar.transport.event_schema import UtteranceTranscribed


# PRD 부록 B 그대로
FIXTURES: list[tuple[str, str]] = [
    ("Can we finalize the layout revisions before Thursday?",
     "layout 수정본 목요일 전 확정 가능?"),
    ("The delivery might slip by one day because of render issues.",
     "render 문제로 delivery 하루 지연 가능성"),
    ("Please send the BG fix by Friday.",
     "금요일까지 BG 수정본 전달 요청"),
    ("Let me check the schedule with the producer.",
     "프로듀서와 스케줄 확인 필요"),
    ("The retake request needs final approval from the client.",
     "리테이크 요청은 클라이언트 최종 승인 필요"),
]


# === ANCHOR: FIXTURE_EMITTER_FIXTURE_STREAM_START ===
async def fixture_stream(session_id: UUID, interval_seconds: float = 1.0) -> AsyncIterator[dict]:
    """Yield one UtteranceTranscribed JSON dict per `interval_seconds`."""
    seq = 0
    while True:
        seq += 1
        text_en, text_ko = FIXTURES[(seq - 1) % len(FIXTURES)]
        now = datetime.now(timezone.utc)
        evt = UtteranceTranscribed(
            session_id=session_id,
            occurred_at=now,
            seq=seq,
            speaker=None,
            text_en=text_en,
            text_ko=text_ko,
            started_at=now,
            ended_at=now,
            is_final=True,
        )
        yield evt.to_json_dict()
        await asyncio.sleep(interval_seconds)
# === ANCHOR: FIXTURE_EMITTER_FIXTURE_STREAM_END ===
# === ANCHOR: FIXTURE_EMITTER_END ===
