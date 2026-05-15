"""DomainEvent SSOT for yeson-meet.

Locked in PRD §10 (Slice 1, 2026-05-15):
- MVP-α Slice 1 emits ONLY ``utterance.transcribed``. Other event types
  (session.started/ended, status.changed, keyword.detected, action.detected,
  report.generated) are added in Slice 2~β-3.
- This module is the single source of truth. Sidecar mirrors it as a dataclass
  in apps/client_sidecar/transport/event_schema.py; web mirrors in
  apps/web/src/types/events.ts. Keep all three in sync — drift breaks the contract.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

EVENT_VERSION = "1"
"""Bump when DomainEvent schema changes incompatibly. Currently 1 (Slice 1)."""


class DomainEvent(BaseModel):
    """Base domain event. Subclasses set ``type`` literal."""

    model_config = ConfigDict(frozen=True)

    type: str
    session_id: UUID
    occurred_at: datetime


class UtteranceTranscribed(DomainEvent):
    """An English utterance was transcribed and translated to Korean."""

    type: Literal["utterance.transcribed"] = "utterance.transcribed"
    seq: int = Field(..., ge=1, description="Monotonic per-session sequence (idempotency key).")
    speaker: str | None = None
    text_en: str
    text_ko: str
    started_at: datetime
    ended_at: datetime
    is_final: bool = False


def serialize(event: DomainEvent) -> dict:
    """JSON-serializable dict for WebSocket fan-out."""
    return event.model_dump(mode="json")
