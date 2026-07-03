"""Batch transcription via local faster-whisper (CPU int8)."""
from __future__ import annotations

import logging
from pathlib import Path

from apps.server.ai.glossary import load_glossary
from .srt import SubSegment
from .whisper_models import is_downloaded, model_dir

logger = logging.getLogger("yeson.video.transcribe")


class ModelNotDownloadedError(RuntimeError):
    pass


def glossary_initial_prompt(max_terms: int = 40) -> str:
    """whisper initial_prompt는 ~224 토큰 제한 → 용어 키워드만 압축 주입."""
    terms = [en for en, _ko in load_glossary()[:max_terms]]
    return "Animation production meeting. Terms: " + ", ".join(terms)


def _load_model(model_name: str):  # test seam
    from faster_whisper import WhisperModel

    return WhisperModel(str(model_dir(model_name)), device="cpu", compute_type="int8")


def transcribe_audio(audio_path: Path, model_name: str) -> list[SubSegment]:
    """Blocking CPU work — call via asyncio.to_thread."""
    if not is_downloaded(model_name):
        raise ModelNotDownloadedError(
            f"whisper 모델 '{model_name}'이 설치되어 있지 않습니다. 먼저 다운로드하세요."
        )
    model = _load_model(model_name)
    segments, info = model.transcribe(
        str(audio_path),
        language="en",
        vad_filter=True,
        initial_prompt=glossary_initial_prompt(),
    )
    out: list[SubSegment] = []
    for i, seg in enumerate(segments, start=1):
        text = seg.text.strip()
        if not text:
            continue
        out.append(SubSegment(seq=i, start_ms=int(seg.start * 1000),
                              end_ms=int(seg.end * 1000), text=text))
    logger.info("transcribe: %d segments (model=%s)", len(out), model_name)
    return out
