# === ANCHOR: SIDECAR_START ===
"""Sidecar WebSocket router (/ws/sidecar). Implemented in S1-L1.

Sidecar (capture device) connects with ?key=<device_api_key>&session=<external_uuid>,
streams `utterance.transcribed` JSON frames, server persists with idempotency on
(session_id, seq) and fans out via `bus` to /ws/viewer subscribers.

S2: receive loop upgraded to dict dispatch — binary frames → audio_stats, text frames
    try S2 control first, fall back to S1 fixture UtteranceTranscribed (regression compat).
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.server.auth.device import hash_api_key
from apps.server.ai.gemini_live import GeminiLiveProvider
from apps.server.ai.google_stt_translate import GoogleSttTranslateProvider
from apps.server.ai.live_session import AudioLiveSession
from apps.server.ai.providers import STTProvider, TranslatedUtterance
from apps.server.db.models import Device, Session, Utterance
from apps.server.db.session import AsyncSessionLocal
from apps.server.domain.events import UtteranceTranscribed, serialize
from apps.server.ops.session_safety import (
    enforce_meeting_duration_limit,
    session_started_at_exceeds_max_duration,
    stamp_sidecar_disconnected,
)
from apps.server.ws.audio_stats import audio_stats
from apps.server.ws.bus import bus
from apps.server.ws.control_messages import (
    AudioStarted,
    AudioStopped,
    ChunkMeta,
    ControlMessage,
    parse_control_message,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Tracks the currently-active AI live session for each meeting external_id.
# Used so that when a sidecar reconnects (new ws handler accepts for the same
# session), the prior handler's still-running AudioLiveSession is stopped
# before a second one starts emitting in parallel.
_active_ai_sessions: dict[UUID, "AudioLiveSession"] = {}


class AISequenceNormalizer:
    """Keep provider subtitle seq values monotonic across Live reconnects.

    Provider yields seq starting from 1 within each segment, and `provider_segment`
    increments on each reconnect/cycle. We detect a segment boundary by watching
    `provider_segment` change — that's also when partial-only segments correctly
    bump the offset (the previous heuristic missed partial-cut-off cycles).
    """

    def __init__(self, initial_offset: int = 0) -> None:
        self._offset = initial_offset
        self._last_assigned_seq = initial_offset
        self._last_segment = 0
        self._provider_to_assigned: dict[int, int] = {}

    def normalize(self, utterance: TranslatedUtterance) -> TranslatedUtterance:
        if utterance.provider_segment != self._last_segment:
            self._offset = self._last_assigned_seq
            self._provider_to_assigned.clear()
            self._last_segment = utterance.provider_segment

        assigned_seq = self._provider_to_assigned.setdefault(
            utterance.seq,
            self._offset + utterance.seq,
        )
        self._last_assigned_seq = max(self._last_assigned_seq, assigned_seq)
        if assigned_seq == utterance.seq:
            return utterance
        return replace(utterance, seq=assigned_seq)


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return max(0, round((end - start).total_seconds() * 1000))


def _elapsed_monotonic_ms(start: float, end: float | None = None) -> int:
    return max(0, round(((end if end is not None else time.monotonic()) - start) * 1000))


def _mark_stale_device_session_ended(
    stale_session: Session,
    replacement_session: Session,
) -> bool:
    if stale_session.started_at >= replacement_session.started_at:
        return False
    now = datetime.now(timezone.utc)
    stale_session.status = "ended"
    stale_session.ended_at = now
    logger.info(
        "Ended stale sidecar device session before accepting replacement",
        extra={
            "stale_session_id": str(stale_session.external_id),
            "replacement_session_id": str(replacement_session.external_id),
            "device_id": stale_session.device_id,
        },
    )
    return True


def create_ai_provider(trace_extra: Mapping[str, object] | None = None) -> STTProvider | None:
    """Return the configured AI provider, otherwise keep S2 count-only mode."""
    provider_name = os.environ.get("YESON_AI_PROVIDER", "gemini_live").lower()
    if provider_name in {"google_stt_translate", "google_stt", "stt_translate"}:
        if not (
            os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        ):
            return None
        return GoogleSttTranslateProvider()
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    return GeminiLiveProvider(trace_extra=trace_extra)


def _handle_control(session_id: UUID, ctrl: ControlMessage) -> None:
    """Dispatch a control message to in-memory audio_stats. No DB writes."""
    if isinstance(ctrl, AudioStarted):
        audio_stats.mark_started(session_id, ctrl.sample_rate, ctrl.channels, ctrl.started_at)
    elif isinstance(ctrl, ChunkMeta):
        audio_stats.note_seq(session_id, ctrl.seq)
    elif isinstance(ctrl, AudioStopped):
        audio_stats.mark_stopped(session_id, ctrl.reason)


async def _persist_and_publish_ai_utterance(
    session_pk: int,
    session_uuid: UUID,
    utterance: TranslatedUtterance,
) -> None:
    evt = UtteranceTranscribed(
        session_id=session_uuid,
        occurred_at=datetime.now(timezone.utc),
        seq=utterance.seq,
        speaker=utterance.speaker,
        text_en=utterance.text_en,
        text_ko=utterance.text_ko,
        started_at=utterance.started_at,
        ended_at=utterance.ended_at,
        is_final=utterance.is_final,
    )
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
            .on_conflict_do_update(
                index_elements=["session_id", "seq"],
                set_={
                    "speaker": evt.speaker,
                    "text_en": evt.text_en,
                    "text_ko": evt.text_ko,
                    "started_at": evt.started_at,
                    "ended_at": evt.ended_at,
                    "is_final": evt.is_final,
                },
            )
        )
        await db.execute(stmt)
        await db.commit()
    await bus.publish(session_uuid, serialize(evt))
    logger.info(
        "AI utterance published",
        extra={
            "session_id": str(session_uuid),
            "seq": evt.seq,
            "is_final": evt.is_final,
            "ai_publish_latency_ms": _elapsed_ms(utterance.ended_at, evt.occurred_at),
        },
    )


async def _publish_provider_error(session_uuid: UUID, error: BaseException) -> None:
    """Surface a permanent AI-provider failure (billing/quota/auth) to operator
    clients so the meeting host sees why subtitles stopped, instead of silence.
    Forwarded verbatim by /ws/operator; unknown to viewers, which ignore it."""
    await bus.publish(
        session_uuid,
        {
            "type": "ai.status",
            "status": "provider_error",
            "session_id": str(session_uuid),
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "detail": str(error)[:200],
        },
    )


async def _last_utterance_seq(session_pk: int) -> int:
    async with AsyncSessionLocal() as db:
        value = (
            await db.execute(
                select(func.max(Utterance.seq)).where(Utterance.session_id == session_pk)
            )
        ).scalar_one_or_none()
    return int(value or 0)


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
        if meeting.device_id is not None and meeting.device_id != device.id:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        active_for_device = (
            await db.execute(
                select(Session).where(
                    Session.device_id == device.id,
                    Session.status == "live",
                    Session.id != meeting.id,
                )
            )
        ).scalar_one_or_none()
        if active_for_device is not None:
            if not _mark_stale_device_session_ended(active_for_device, meeting):
                await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            await db.commit()
        if meeting.device_id is None:
            meeting.device_id = device.id
            await db.commit()
        if meeting.disconnected_at is not None:
            meeting.disconnected_at = None
            await db.commit()
        if await enforce_meeting_duration_limit(db, meeting):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        session_pk = meeting.id
        meeting_started_at = meeting.started_at

    await ws.accept()
    # Stop any AI session left over by a prior sidecar that didn't cleanly
    # disconnect — prevents two AudioLiveSessions publishing in parallel.
    stale_ai_session = _active_ai_sessions.pop(session_uuid, None)
    if stale_ai_session is not None:
        logger.info(
            "Stopping stale AI live session before accepting replacement sidecar",
            extra={"session_id": str(session_uuid)},
        )
        await stale_ai_session.stop()
    ai_session: AudioLiveSession | None = None
    ai_sequence_normalizer = AISequenceNormalizer(
        initial_offset=await _last_utterance_seq(session_pk)
    )
    accepted_at = time.monotonic()
    first_audio_chunk_at: float | None = None
    trace_extra: dict[str, object] = {"session_id": str(session_uuid)}
    logger.info("Sidecar websocket accepted", extra=trace_extra)
    try:
        while True:
            msg = await ws.receive()
            msg_type = msg.get("type")
            if msg_type == "websocket.disconnect":
                return
            if "text" in msg and msg["text"] is not None:
                text = msg["text"]
                # 1) try S2 control message first
                try:
                    control = parse_control_message(text)
                    _handle_control(session_uuid, control)  # in-process log + audio_stats hooks
                    if isinstance(control, AudioStarted) and ai_session is None:
                        audio_started_at = time.monotonic()
                        logger.info(
                            "Sidecar audio stream started",
                            extra={
                                **trace_extra,
                                "sidecar_accept_to_audio_started_ms": _elapsed_monotonic_ms(
                                    accepted_at,
                                    audio_started_at,
                                ),
                                "audio_sample_rate": control.sample_rate,
                                "audio_channels": control.channels,
                            },
                        )
                        provider = create_ai_provider(trace_extra=trace_extra)
                        if provider is not None:
                            ai_start_at = time.monotonic()
                            logger.info(
                                "AI live session starting",
                                extra={
                                    **trace_extra,
                                    "audio_started_to_ai_start_ms": _elapsed_monotonic_ms(
                                        audio_started_at,
                                        ai_start_at,
                                    ),
                                },
                            )
                            new_ai_session = AudioLiveSession(
                                provider=provider,
                                on_utterance=lambda utterance: _persist_and_publish_ai_utterance(
                                    session_pk,
                                    session_uuid,
                                    ai_sequence_normalizer.normalize(utterance),
                                ),
                                on_permanent_error=lambda error: _publish_provider_error(
                                    session_uuid, error
                                ),
                            )
                            await new_ai_session.start()
                            ai_session = new_ai_session
                            _active_ai_sessions[session_uuid] = ai_session
                            logger.info(
                                "AI live session started",
                                extra={
                                    **trace_extra,
                                    "ai_session_start_latency_ms": _elapsed_monotonic_ms(
                                        ai_start_at
                                    ),
                                },
                            )
                        else:
                            logger.info("AI provider unavailable", extra=trace_extra)
                    elif isinstance(control, AudioStopped) and ai_session is not None:
                        if _active_ai_sessions.get(session_uuid) is ai_session:
                            del _active_ai_sessions[session_uuid]
                        await ai_session.stop()
                        ai_session = None
                    continue
                except ValueError:
                    pass
                # 2) fall back to S1 fixture UtteranceTranscribed (regression compat)
                try:
                    evt = UtteranceTranscribed.model_validate_json(text)
                except ValidationError:
                    await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                    return
                # existing insert + bus.publish path
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
            elif "bytes" in msg and msg["bytes"] is not None:
                if session_started_at_exceeds_max_duration(meeting_started_at):
                    async with AsyncSessionLocal() as db:
                        meeting = (
                            await db.execute(
                                select(Session).where(Session.id == session_pk)
                            )
                        ).scalar_one_or_none()
                        if meeting is not None:
                            await enforce_meeting_duration_limit(db, meeting)
                    await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                    return
                audio_stats.record(session_uuid, len(msg["bytes"]))
                if first_audio_chunk_at is None:
                    first_audio_chunk_at = time.monotonic()
                    logger.info(
                        "Sidecar first audio chunk received",
                        extra={
                            **trace_extra,
                            "sidecar_accept_to_first_chunk_ms": _elapsed_monotonic_ms(
                                accepted_at,
                                first_audio_chunk_at,
                            ),
                            "audio_first_chunk_bytes": len(msg["bytes"]),
                        },
                    )
                if ai_session is not None:
                    await ai_session.push_audio(msg["bytes"])
    except WebSocketDisconnect:
        return
    finally:
        if ai_session is not None:
            # Only deregister if a later handler hasn't already replaced us.
            if _active_ai_sessions.get(session_uuid) is ai_session:
                del _active_ai_sessions[session_uuid]
            await ai_session.stop()
        try:
            async with AsyncSessionLocal() as db:
                await stamp_sidecar_disconnected(db, session_pk)
        except Exception:
            logger.exception("Failed to stamp sidecar disconnect", extra=trace_extra)
    # NOTE(s4-session-lifecycle): do not audio_stats.discard() here — admin
    # view + tests rely on snapshot surviving sidecar disconnect to show final
    # totals. Session-end eviction is owned by S4 /api/v1/sessions/{id}/end.
# === ANCHOR: SIDECAR_END ===
