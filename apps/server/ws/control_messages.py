"""Sidecar→Server text-frame control messages (S2)."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter


class AudioStarted(BaseModel):
    type: Literal["audio.started"] = "audio.started"
    sample_rate: int          # 16000 (locked)
    channels: int             # 1 (locked)
    format: Literal["pcm_s16le"] = "pcm_s16le"
    started_at: datetime


class ChunkMeta(BaseModel):
    type: Literal["chunk_meta"] = "chunk_meta"
    seq: int = Field(..., ge=1)
    started_at: datetime


class AudioStopped(BaseModel):
    type: Literal["audio.stopped"] = "audio.stopped"
    reason: str | None = None


ControlMessage = Annotated[
    Union[AudioStarted, ChunkMeta, AudioStopped],
    Field(discriminator="type"),
]
_adapter: TypeAdapter[ControlMessage] = TypeAdapter(ControlMessage)


def parse_control_message(raw: str) -> ControlMessage:
    """Parse a JSON control frame. Raises ValueError if not a known control type."""
    return _adapter.validate_json(raw)
