"""Batch transcription via local faster-whisper (기본 CPU int8, 옵트인 CUDA)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from . import gpu_pack
from .srt import SubSegment
from .whisper_models import is_downloaded, model_dir

logger = logging.getLogger("yeson.video.transcribe")

MAX_CUE_SECONDS = 6.0
MAX_CUE_CHARS = 90  # EN 기준; KO 번역은 보통 더 짧음

_BREAK_AFTER = (".", "?", "!", ",", ";", ":")


class ModelNotDownloadedError(RuntimeError):
    pass


class StaleRunCancelled(Exception):
    """진행률 콜백이 스테일(취소·재생성된) 실행 세대를 감지했을 때 던진다.

    CPU 집약적 전사/굽기는 asyncio 취소에 반응하지 않는 워커 스레드에서 돌기
    때문에, task.cancel()이 걸려도 스레드는 끝까지 실행된다. 콜백이 이 예외를
    던지면 스레드가 남은 작업을 마저 태우지 않고 즉시 빠져나간다. leaf 모듈인
    여기에 정의해 pipeline↔transcribe 순환 임포트 없이 양쪽에서 쓴다."""


def _load_model(model_name: str, device: str = "cpu",
                compute_type: str = "int8"):  # test seam
    from faster_whisper import WhisperModel

    return WhisperModel(str(model_dir(model_name)), device=device,
                        compute_type=compute_type)


def _cue_text(chunk: list) -> str:
    return " ".join(" ".join(w.word.strip() for w in chunk).split())


def _last_break_index(chunk: list) -> int | None:
    for idx in range(len(chunk) - 1, -1, -1):
        if chunk[idx].word.strip().endswith(_BREAK_AFTER):
            return idx
    return None


def words_to_cues(words: list, max_seconds: float = MAX_CUE_SECONDS,
                  max_chars: int = MAX_CUE_CHARS) -> list[SubSegment]:
    """단어 타임스탬프를 자막 큐로 재분할.

    규칙: 누적 큐가 max_seconds/max_chars를 넘기 전에 끊되, 최근에 지나온
    문장부호(_BREAK_AFTER로 끝나는 단어) 뒤가 있으면 거기서 우선 분할해
    문장이 어색하게 잘리는 것을 줄인다. words 항목은 .start/.end/.word
    (faster-whisper Word). 빈/공백 word는 스킵.
    """
    cues: list[SubSegment] = []
    buf: list = []

    def emit(chunk: list) -> None:
        if not chunk:
            return
        cues.append(SubSegment(seq=0, start_ms=int(chunk[0].start * 1000),
                               end_ms=int(chunk[-1].end * 1000), text=_cue_text(chunk)))

    for word in words:
        w_text = getattr(word, "word", "")
        if not w_text or not w_text.strip():
            continue
        tentative = buf + [word]
        duration = tentative[-1].end - tentative[0].start
        text_len = len(_cue_text(tentative))
        if buf and (duration > max_seconds or text_len > max_chars):
            brk = _last_break_index(buf)
            if brk is not None:
                emit(buf[: brk + 1])
                buf = buf[brk + 1:] + [word]
            else:
                emit(buf)
                buf = [word]
        else:
            buf = tentative

    emit(buf)

    for i, cue in enumerate(cues, start=1):
        cue.seq = i
    return cues


def transcribe_audio(audio_path: Path, model_name: str,
                     progress_cb: Callable[[float], None] | None = None) -> list[SubSegment]:
    """Blocking CPU/GPU work — call via asyncio.to_thread.

    디바이스는 gpu_pack.resolve_device()가 정한다(기본 CPU, 옵트인 CUDA).
    CUDA는 로드/전사 어느 지점에서든 실패할 수 있어(드라이버·VRAM 등)
    실패 시 CPU int8로 1회 폴백한다.
    """
    if not is_downloaded(model_name):
        raise ModelNotDownloadedError(
            f"whisper 모델 '{model_name}'이 설치되어 있지 않습니다. 먼저 다운로드하세요."
        )
    device, compute_type = gpu_pack.resolve_device()
    try:
        return _transcribe_on(audio_path, model_name, device, compute_type, progress_cb)
    except StaleRunCancelled:
        raise  # 취소 신호 — CUDA 실패가 아니므로 CPU 폴백으로 재전사하지 않는다
    except Exception:
        if device == "cpu":
            raise
        logger.warning("transcribe: CUDA 실패 — CPU로 폴백", exc_info=True)
        return _transcribe_on(audio_path, model_name, "cpu", "int8", progress_cb)


def _transcribe_on(audio_path: Path, model_name: str, device: str, compute_type: str,
                   progress_cb: Callable[[float], None] | None) -> list[SubSegment]:
    model = _load_model(model_name, device, compute_type)
    # initial_prompt(용어사전) 주입 금지 — 회의용 프롬프트가 본편 대사 전사에서
    # 오도성 문맥이 되어, base 모델이 30초 윈도우 하나를 통째로 버리는 회귀를
    # 일으켰다(2026-07-08, 14.9~30.9s 유실 실측·분리실험으로 확정). 용어 매핑은
    # 번역 단계(build_translation_prompt의 glossary_block)가 전담한다.
    segments, info = model.transcribe(
        str(audio_path),
        language="en",
        vad_filter=True,
        word_timestamps=True,
    )
    duration = getattr(info, "duration", None)
    all_words: list = []
    fallback: list[SubSegment] = []
    for i, seg in enumerate(segments, start=1):
        text = seg.text.strip()
        if progress_cb is not None and duration:
            progress_cb(min(seg.end / duration, 1.0))
        if not text:
            continue
        seg_words = getattr(seg, "words", None) or []
        if seg_words:
            all_words.extend(seg_words)
        else:
            fallback.append(SubSegment(seq=i, start_ms=int(seg.start * 1000),
                                       end_ms=int(seg.end * 1000), text=text))
    out = words_to_cues(all_words) if all_words else fallback
    logger.info("transcribe: %d segments (model=%s, device=%s)",
                len(out), model_name, device)
    return out
