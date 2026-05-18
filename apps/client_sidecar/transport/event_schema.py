# === ANCHOR: EVENT_SCHEMA_START ===
"""Sidecar-side mirror of apps/server/domain/events.py.

SSOT lives in apps/server/domain/events.py (Pydantic). This dataclass mirror
keeps the sidecar free of server deps. Drift breaks the contract — update both
files in the same commit when DomainEvent shape changes.

Locked in PRD §10 (Slice 1): only ``utterance.transcribed`` event.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any
from uuid import UUID


EVENT_VERSION = "1"


@dataclass(frozen=True, slots=True)
# === ANCHOR: EVENT_SCHEMA_UTTERANCETRANSCRIBED_START ===
class UtteranceTranscribed:
    session_id: UUID
    occurred_at: datetime
    seq: int
    started_at: datetime
    ended_at: datetime
    text_en: str
    text_ko: str
    speaker: str | None = None
    is_final: bool = False
    type: str = "utterance.transcribed"

    # === ANCHOR: EVENT_SCHEMA_TO_JSON_DICT_START ===
    def to_json_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["session_id"] = str(self.session_id)
        d["occurred_at"] = self.occurred_at.isoformat()
        d["started_at"] = self.started_at.isoformat()
        d["ended_at"] = self.ended_at.isoformat()
# === ANCHOR: EVENT_SCHEMA_UTTERANCETRANSCRIBED_END ===
        return d
    # === ANCHOR: EVENT_SCHEMA_TO_JSON_DICT_END ===
# === ANCHOR: EVENT_SCHEMA_END ===
