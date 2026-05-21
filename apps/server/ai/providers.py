# === ANCHOR: PROVIDERS_START ===
"""Provider interfaces for Slice 3 speech-to-subtitle streaming."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# === ANCHOR: PROVIDERS_TRANSLATEDUTTERANCE_START ===
@dataclass(frozen=True)
class TranslatedUtterance:
    """One subtitle-ready utterance emitted by an STT/translation provider."""

    seq: int
    text_en: str
    text_ko: str
    started_at: datetime
    ended_at: datetime
    is_final: bool = False
    speaker: str | None = None
    # 같은 provider 세션 내 monotonically 증가하는 segment 번호. 재접속/cycle 시
    # 증가하므로 downstream에서 segment 경계를 식별할 수 있다 (예: 시퀀스 normalizer).
    provider_segment: int = 1
# === ANCHOR: PROVIDERS_TRANSLATEDUTTERANCE_END ===


# === ANCHOR: PROVIDERS_STTPROVIDER_START ===
class STTProvider(Protocol):
    def stream(
        self,
        audio: AsyncIterator[bytes],
        lang_hint: str,
    ) -> AsyncIterator[TranslatedUtterance]:
        """Consume 16kHz mono PCM chunks and yield subtitle utterances."""
        ...
# === ANCHOR: PROVIDERS_STTPROVIDER_END ===


# === ANCHOR: PROVIDERS_TRANSLATIONPROVIDER_START ===
class TranslationProvider(Protocol):
    async def translate(
        self,
        text: str,
        src: str,
        dst: str,
        glossary: dict[str, str] | None = None,
    ) -> str:
        """Translate text from src to dst."""
        ...
# === ANCHOR: PROVIDERS_TRANSLATIONPROVIDER_END ===
# === ANCHOR: PROVIDERS_END ===
