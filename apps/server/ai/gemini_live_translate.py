# === ANCHOR: GEMINI_LIVE_TRANSLATE_START ===
"""Gemini 3.5 Live Translate provider — continuous speech-to-caption stream.

Unlike ``gemini_live`` (turn-based: ~10s audio segments, transcription arrives
as one batch after each segment closes), ``gemini-3.5-live-translate-preview``
translates continuously WHILE the speaker talks: Korean caption text streams
~1.5-3s behind the speech (measured 2026-07-02). There are no turns and no
utterance boundaries in the model output — just an endless trickle of small
EN (input transcription) and KO (output transcription) text fragments — so
this module's core job is assembling that trickle into seq'd partial/final
``TranslatedUtterance``s for the existing pacer/report/DB pipeline.

The model accepts no system instructions or tools ("pure translation"), so the
prompt glossary cannot steer terminology; known-bad literal renderings are
patched post-hoc via ``glossary.apply_ko_corrections`` instead.

One live session per ``stream()`` call: on any session error the exception
propagates to live_session's reconnect loop, which calls ``stream()`` again —
``provider_segment`` increments per call so AISequenceNormalizer re-offsets.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from apps.server.ai.glossary import apply_ko_corrections, glossary_block
from apps.server.ai.providers import TranslatedUtterance

INPUT_SAMPLE_RATE = 16000
MODEL_ENV = "GEMINI_LIVE_TRANSLATE_MODEL"
TARGET_LANGUAGE_ENV = "GEMINI_LIVE_TRANSLATE_TARGET"
FORCE_FINAL_CHARS_ENV = "GEMINI_LT_FORCE_FINAL_CHARS"
MIN_FINAL_CHARS_ENV = "GEMINI_LT_MIN_FINAL_CHARS"
MAX_UTTERANCE_MS_ENV = "GEMINI_LT_MAX_UTTERANCE_MS"
IDLE_FINAL_MS_ENV = "GEMINI_LT_IDLE_FINAL_MS"
PARTIAL_MIN_DELTA_CHARS_ENV = "GEMINI_LT_PARTIAL_MIN_DELTA_CHARS"

FINAL_TRANSLATION_MODEL_ENV = "GEMINI_FINAL_TRANSLATION_MODEL"
FINAL_TRANSLATION_TIMEOUT_MS_ENV = "GEMINI_FINAL_TRANSLATION_TIMEOUT_MS"

DEFAULT_MODEL = "gemini-3.5-live-translate-preview"
DEFAULT_TARGET_LANGUAGE = "ko"
# 하이브리드 파이널 번역(트랙 C): 3.5는 프롬프트 주입이 불가해 용어·숫자
# 표기를 조종할 수 없다 — 문장 확정 시 EN을 텍스트 모델+단어집으로 재번역해
# 파이널만 교체한다. 실패/타임아웃 시 3.5 KO를 그대로 둔다(파이널 유실 금지).
DEFAULT_FINAL_TRANSLATION_MODEL = "gemini-2.5-flash-lite"
DEFAULT_FINAL_TRANSLATION_TIMEOUT_MS = 3500
# A caption line is force-finalized past this length even without sentence
# punctuation, so a long rambling clause cannot grow one line unboundedly.
DEFAULT_FORCE_FINAL_CHARS = 90
# ...and is NOT finalized at a sentence boundary until it reaches this length,
# so short sentences merge into one fuller caption line instead of flashing by
# as one-clause morsels ("감질" feedback, 2026-07-02). This gates only the
# sentence-boundary cut: the force/age caps and the idle flush still finalize
# short text, so a speaker pausing after a short sentence is unaffected. Does
# not add latency — text appears via partials; this only moves the line break.
DEFAULT_MIN_FINAL_CHARS = 45
# ...and past this age, so a slow trickle cannot pin one seq forever. Matches
# the gemini_live hard cap so downstream pacing assumptions carry over.
DEFAULT_MAX_UTTERANCE_MS = 12000
# No new KO text for this long (speaker pause / meeting lull) → finalize what
# we have. Driven by the receive-poll timeout, so resolution is RECEIVE_POLL_S.
DEFAULT_IDLE_FINAL_MS = 2000
# Re-publish the growing partial only every N new chars — keeps DB/bus churn
# near the fragment rate (~2/s) without dropping visible progress.
DEFAULT_PARTIAL_MIN_DELTA_CHARS = 4
RECEIVE_POLL_S = 0.5
logger = logging.getLogger(__name__)

# Sentence boundary inside accumulated KO text: terminal punctuation not
# sandwiched between digits ("1.5" must not split). Korean output from the
# model reliably carries .?!… so ending-form detection is unnecessary.
_SENTENCE_END_RE = re.compile(r"(?<![0-9])[.?!…]|(?<=[0-9])[.?!…](?![0-9])")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _last_sentence_end(text: str) -> int:
    """Index just past the last sentence-terminal punctuation, or -1."""
    last = -1
    for match in _SENTENCE_END_RE.finditer(text):
        last = match.end()
    return last


@dataclass
class _EmitState:
    seq: int = 1
    en_buffer: str = ""
    ko_buffer: str = ""
    emitted_ko_len: int = 0
    started_at: datetime | None = None
    started_monotonic: float | None = None
    last_ko_at: float | None = None


class TranscriptAssembler:
    """Fold continuous EN/KO transcription fragments into utterances.

    ``feed``/``poll`` return the utterances to publish, partials first, at most
    one final per call (a final resets the buffer, so at most one boundary is
    consumed per fragment — fragments are a few words, never multi-sentence
    beyond one boundary in practice; any tail stays buffered for the next seq).
    """

    def __init__(
        self,
        provider_segment: int,
        force_final_chars: int | None = None,
        min_final_chars: int | None = None,
        max_utterance_ms: int | None = None,
        idle_final_ms: int | None = None,
        partial_min_delta_chars: int | None = None,
    ) -> None:
        self._segment = provider_segment
        self._force_final_chars = force_final_chars or _int_env(
            FORCE_FINAL_CHARS_ENV, DEFAULT_FORCE_FINAL_CHARS
        )
        self._min_final_chars = (
            min_final_chars
            if min_final_chars is not None
            else _int_env(MIN_FINAL_CHARS_ENV, DEFAULT_MIN_FINAL_CHARS)
        )
        self._max_utterance_s = (
            max_utterance_ms
            if max_utterance_ms is not None
            else _int_env(MAX_UTTERANCE_MS_ENV, DEFAULT_MAX_UTTERANCE_MS)
        ) / 1000
        self._idle_final_s = (
            idle_final_ms
            if idle_final_ms is not None
            else _int_env(IDLE_FINAL_MS_ENV, DEFAULT_IDLE_FINAL_MS)
        ) / 1000
        self._partial_min_delta = (
            partial_min_delta_chars
            if partial_min_delta_chars is not None
            else _int_env(PARTIAL_MIN_DELTA_CHARS_ENV, DEFAULT_PARTIAL_MIN_DELTA_CHARS)
        )
        self._state = _EmitState()

    def feed(
        self,
        en_text: str | None,
        ko_text: str | None,
        now_monotonic: float | None = None,
    ) -> list[TranslatedUtterance]:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        state = self._state
        if en_text:
            state.en_buffer += en_text
        if ko_text:
            if not state.ko_buffer.strip():
                state.started_at = datetime.now(timezone.utc)
                state.started_monotonic = now
            state.ko_buffer += ko_text
            state.last_ko_at = now
        if not state.ko_buffer.strip():
            return []

        boundary = _last_sentence_end(state.ko_buffer)
        aged = (
            state.started_monotonic is not None
            and now - state.started_monotonic >= self._max_utterance_s
        )
        boundary_len = (
            len(state.ko_buffer[:boundary].strip()) if boundary > 0 else 0
        )
        if boundary_len > 1 and boundary_len >= self._min_final_chars:
            return self._finalize(split_at=boundary)
        if len(state.ko_buffer.strip()) >= self._force_final_chars or aged:
            return self._finalize(split_at=len(state.ko_buffer))
        return self._maybe_partial()

    def poll(self, now_monotonic: float | None = None) -> list[TranslatedUtterance]:
        """Timer tick from the receive loop — applies the idle-finalize rule."""
        now = time.monotonic() if now_monotonic is None else now_monotonic
        state = self._state
        if not state.ko_buffer.strip() or state.last_ko_at is None:
            return []
        if now - state.last_ko_at >= self._idle_final_s:
            return self._finalize(split_at=len(state.ko_buffer))
        return []

    def flush(self) -> list[TranslatedUtterance]:
        """Finalize whatever remains (stream ending)."""
        if not self._state.ko_buffer.strip():
            return []
        return self._finalize(split_at=len(self._state.ko_buffer))

    def _maybe_partial(self) -> list[TranslatedUtterance]:
        state = self._state
        if len(state.ko_buffer) - state.emitted_ko_len < self._partial_min_delta:
            return []
        state.emitted_ko_len = len(state.ko_buffer)
        return [self._utterance(state.ko_buffer, state.en_buffer, is_final=False)]

    def _finalize(self, split_at: int) -> list[TranslatedUtterance]:
        state = self._state
        ko_final = state.ko_buffer[:split_at]
        ko_rest = state.ko_buffer[split_at:]
        # EN pairing is approximate (EN fragments lead KO slightly): give this
        # utterance the EN buffer up to ITS last sentence boundary and carry the
        # tail — which usually belongs to the sentence still being spoken —
        # into the next seq.
        en_boundary = _last_sentence_end(state.en_buffer)
        if ko_rest.strip() and en_boundary > 0:
            en_final, en_rest = state.en_buffer[:en_boundary], state.en_buffer[en_boundary:]
        else:
            en_final, en_rest = state.en_buffer, ""
        utterance = self._utterance(ko_final, en_final, is_final=True)
        state.seq += 1
        state.en_buffer = en_rest
        state.ko_buffer = ko_rest
        state.emitted_ko_len = 0
        if ko_rest.strip():
            state.started_at = datetime.now(timezone.utc)
            state.started_monotonic = time.monotonic()
        else:
            state.started_at = None
            state.started_monotonic = None
            state.last_ko_at = None
        return [utterance]

    def _utterance(
        self, ko: str, en: str, *, is_final: bool
    ) -> TranslatedUtterance:
        state = self._state
        return TranslatedUtterance(
            seq=state.seq,
            text_en=en.strip(),
            text_ko=apply_ko_corrections(ko.strip()),
            started_at=state.started_at or datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            is_final=is_final,
            provider_segment=self._segment,
        )


# 직전 발화를 문맥으로 넣을 때의 길이 상한. 발화는 보통 한두 문장이라 넉넉하고,
# 넘치면 앞을 자른다 — 뒤쪽이 지금 문장에 붙어 있는 말이라 정보가 더 많다.
_PREV_CONTEXT_MAX_CHARS = 400


async def _translate_final_text(
    text_client: Any, en: str, prev_en: str = ""
) -> str | None:
    """확정된 EN 문장을 텍스트 모델+단어집으로 번역. 실패/빈 결과는 None —
    호출부가 3.5 KO 원문으로 폴백한다.

    ``prev_en``은 직전 확정 발화의 영어다. 번역 대상이 아니라 문맥으로만 준다 —
    파이널이 KO 문장 경계로 잘리는데 EN 버퍼는 그 자리에서 문장 중간일 수 있어,
    앞 문장의 꼬리가 다음 발화의 첫머리로 넘어간다(_finalize 참조). 그 조각을
    단독 문장으로 번역하면 뜻이 무너진다(실기 2026-08-05 보고서: "spindle" /
    "horse rough perfectly."로 갈려 스튜디오 이름이 "말"이 됐고, 144발화 중
    73건이 문장 중간에서 끊겼다).
    """
    stripped = en.strip()
    if not stripped:
        return None
    prev_context = prev_en.strip()[-_PREV_CONTEXT_MAX_CHARS:]
    from google.genai import types

    try:
        response = await text_client.aio.models.generate_content(
            model=os.environ.get(
                FINAL_TRANSLATION_MODEL_ENV, DEFAULT_FINAL_TRANSLATION_MODEL
            ),
            contents=(
                "Translate this English meeting utterance into natural Korean "
                "subtitle text.\n"
                + glossary_block() + "\n\n"
                # 규칙은 용어집 '뒤'(입력 직전)에 둔다 — 380항목 목록 앞에 두면
                # 입력과 멀어져 준수도가 떨어진다(실기: 온도 규칙이 긴 문장에서
                # 무시되던 케이스가 재배치로 해소).
                #
                # 도메인 힌트 — 3.5 전사가 이 바닥 낱말을 더 흔한 일반어로
                # 잘못 적고, 문장별 호출이라 모델이 그걸 곧이곧대로 옮긴다
                # (실기 2026-08-05 보고서: tweens→"twins"가 6회 중 5회,
                # "an ease has been added"→"anyways..."→"어차피",
                # "back in line"→"online"→"온라인"). 반대로 같은 회의에서
                # "the claim stuff"→"클린업 작업"은 스스로 복구했다 —
                # 도메인을 알려주면 되는 종류의 오류라는 증거다. 지어내는 쪽으로
                # 번지지 않게 마지막 문장으로 못을 박는다.
                "Context: this is a 2D animation production review meeting "
                "(Toon Boom Harmony; rough → cleanup → composite). The English "
                "comes from live speech-to-text, so mishearings are common — "
                "when a word makes no sense in that domain, translate what the "
                "speaker plainly meant ('twins' among inbetweens is 'tweens'; "
                "'moved back online' in a layout note is 'back in line'). "
                "Never add content that is not in the English. "
                # 단위 창작 금지 — 한국어는 수사 뒤 조수사가 문법적으로 필수라
                # 모델이 하나를 지어낸다. 구 규칙의 "with their units"가 그걸
                # 부추겼다(실기 2026-08-04: 화번 305 → 305년/305건/305개, 실행
                # 마다 갈림). 맨숫자는 맨숫자로 두는 편이 항상 안전하다.
                "Rules: Keep numbers as digits, and keep only the unit the "
                "speaker actually said (e.g. 5%, 4~6프레임). If a number has no "
                "unit in the English, write the bare number — never supply a "
                "Korean counter or unit of your own (년·개·건·도·일 …). A bare "
                "'305' stays '305', never '305년' or '305개'. "
                # 화번 규칙 — "bare number는 bare로" 만으로는 'of 305'·'in 305'
                # 같은 전치사 구문에서 모델이 부분표현으로 읽어 개/건을 붙인다
                # (측정: 5/5 잔존). 이 바닥에서 세 자리 수는 화번이므로 뜻을
                # 직접 박아 준다. 단 뒤에 단위어가 붙으면 그건 진짜 수량이다
                # ("305 shots completed and 55 remaining" = 실제 샷 수).
                "A three-digit number like 305 or 402 is an episode number "
                "(season 3 episode 05) — render it '305화', even after 'of' or "
                "'in' ('100% of 305' → '305화의 100%'). Only when an explicit "
                "unit word follows it in the English is it a quantity "
                "('305 shots' → '305 샷'). "
                # 화씨 규칙(PR#67)은 유지하되 '화자가 실제로 degree를 말했을 때'로
                # 게이트. 예시가 쉼표로 이어진 맨숫자 두 개라, 온도와 무관한
                # "it's not 73, it's 49"까지 끌어다 73도/49도를 만들었다.
                "Only when the speaker actually says 'degree(s)' or names a "
                "temperature, treat it as Fahrenheit — convert to Celsius, "
                "rounded, rendered as '약 M도' (e.g. '90, 93 degree weather' → "
                "'약 32도, 34도 날씨'); never leave a Fahrenheit number as a "
                "bare N도. Numbers with no temperature word are never 도. "
                # 서법 보존 — 문장별 호출이라 문맥이 없어 조건문이 지시문으로
                # 뒤집히면 회의록에 없던 약속이 생긴다(실기 2026-08-04:
                # "you think that you can do them by next week" → "다음 주까지
                # 하세요", "if we do not deliver" → "전달하고 있습니다").
                # 호칭·말투 — 발주사↔벤더 회의라 '당신'은 실례이고, 문장별 호출
                # 이라 한 줄만 반말로 튀기도 한다(실기 2026-08-04: '당신의
                # 프로젝션' 등 3곳, '이야기하고 싶었어' 1곳).
                "Always use 존댓말, and address the other party as '그쪽' or by "
                "their team/company name — never '당신'. "
                "Preserve the speaker's mood exactly: a conditional stays "
                "conditional, a statement never becomes a command, and never "
                "add or remove a negation that is not in the English. If the "
                "English is cut off mid-sentence, leave it unfinished rather "
                "than completing it. "
                # 관용구 직역 방지 — 사전 등록 항목(plant the seed)만이 아니라
                # 모든 관용구에 일반화한다(실기 2026-07-28: "씨앗을 심어두고").
                # '대체'를 명시한다 — 직역을 내놓고 괄호 해설을 덧붙이는 반쪽
                # 준수가 실측됨(2026-07-29 보고서: "빙글빙글 돌고 있네요.
                # (정신없이 바쁘네요.)", "고양이 자석인가요? \"고양이들이 잘
                # 따르나요?\"").
                "Render idioms and figures of speech by meaning, never "
                "word-for-word (e.g. 'plant the seed' → '미리 주제로 던져 "
                "두다', not '씨앗을 심다'). Output the meaning-based rendering "
                "directly — never a literal rendering followed by a gloss. "
                # 작품 대사·가사 인용 유지 — 리테이크 노트에서 캐릭터 대사를
                # 소리내 읽는 일이 잦은데, 화자의 말로 번역하면 뜻이 무너진다
                # (실기 2026-07-29: "Cuz I got all the eternity" 3회 →
                # "제가 영원함을 모두 가지고 있으니까요"). 원문 인용이 팀에게
                # 그 대사를 특정할 유일한 단서라 영어로 남긴다.
                "If the speaker recites a line of dialogue or lyrics from the "
                "production (quoting a character's line while giving notes), "
                "keep that quote in its original English inside quotes "
                "instead of translating it. "
                # 메타 주석 금지 — 번역 모델이 "(프로젝트 명칭이라면 그대로
                # Eternity로 표기하는 것이 좋음)" 같은 혼잣말을 자막에 흘린
                # 실측(2026-07-29). 자막에는 번역문만 실려야 한다.
                "Return only the Korean subtitle text — no notes, "
                "alternatives, explanations, or parenthetical commentary "
                "about the translation.\n\n"
                # 직전 발화는 문맥으로만. "번역하지 말라"를 세 가지 방식으로
                # (translate/repeat/continue) 막는다 — 하나만 적으면 이어쓰기가
                # 샌다. 블록을 입력 바로 앞에 두는 것도 같은 이유(07-23 교훈).
                + (
                    "The line below is the PREVIOUS utterance, given only so "
                    "you can resolve words that were cut across the boundary. "
                    "Do not translate it, repeat it, or continue it — output "
                    "the Korean for the 'English:' line only.\n"
                    f"Previous (context only): {prev_context}\n\n"
                    if prev_context
                    else ""
                )
                + f"English: {stripped}"
            ),
            config=types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=320,
            ),
        )
    except Exception:
        logger.warning("Final translation failed — keeping live KO", exc_info=True)
        return None
    text = (getattr(response, "text", None) or "").strip()
    return text or None


class GeminiLiveTranslateProvider:
    """STT+translation provider backed by Gemini 3.5 Live Translate."""

    # 하이브리드(트랙 C) 스위치: True면 확정 파이널의 KO를 텍스트 모델+단어집
    # 번역으로 교체한다. 파셜(3.5 KO 스트림)은 어느 쪽이든 그대로다.
    _final_translate = False

    async def _apply_final_translation(
        self, utterance: TranslatedUtterance, text_client: Any, prev_en: str = ""
    ) -> TranslatedUtterance:
        """확정 파이널의 KO를 단어집 번역으로 교체. 실패·타임아웃·빈 결과는
        3.5 KO 유지 — 어떤 경로로도 파이널을 잃지 않는다.

        ``prev_en``은 직전 확정 발화의 영어(문맥 전용)."""
        if not self._final_translate or not utterance.text_en.strip():
            return utterance
        timeout_s = max(
            1,
            _int_env(
                FINAL_TRANSLATION_TIMEOUT_MS_ENV, DEFAULT_FINAL_TRANSLATION_TIMEOUT_MS
            ),
        ) / 1000
        try:
            ko = await asyncio.wait_for(
                _translate_final_text(text_client, utterance.text_en, prev_en),
                timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Final translation timeout — keeping live KO",
                extra={"gemini_final_translation_timeout_s": timeout_s},
            )
            return utterance
        if not ko:
            return utterance
        return replace(utterance, text_ko=apply_ko_corrections(ko))

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        trace_extra: Mapping[str, object] | None = None,
        client: Any | None = None,
    ) -> None:
        self._api_key: str | None = api_key or os.environ.get("GEMINI_API_KEY")
        self._model: str = model or os.environ.get(MODEL_ENV, DEFAULT_MODEL)
        self._target_language: str = os.environ.get(
            TARGET_LANGUAGE_ENV, DEFAULT_TARGET_LANGUAGE
        )
        self._client = client
        # Cumulative across stream() re-calls (live_session reconnect loop) so
        # AISequenceNormalizer sees each reconnect as a new segment.
        self._segment_index = 0
        self._trace_extra = dict(trace_extra or {})

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        lang_hint: str,
    ) -> AsyncIterator[TranslatedUtterance]:
        if self._client is None and not self._api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is required for GeminiLiveTranslateProvider"
            )

        from google.genai import types

        client = self._client
        if client is None:
            from google import genai

            client = genai.Client(api_key=self._api_key)

        self._segment_index += 1
        # 프로바이더 이름을 trace에 실어 이 스트림의 모든 로그가 들고 다니게 한다.
        # 시작 시 찍히는 "Gemini Live configured model=…"은 gemini_live(3.1)
        # 모듈의 상수라 실제로 어떤 엔진이 회의를 돌렸는지 말해 주지 않는다
        # (실기 2026-08-05: 하이브리드 회의인데 로그엔 3.1로 보였다). 07-22에
        # 기본 프로바이더가 조용히 3.5로 바뀐 걸 오래 못 잡은 것도 같은 사각지대.
        trace = {
            **self._trace_extra,
            "gemini_lt_segment": self._segment_index,
            "gemini_provider": type(self).__name__,
        }
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            translation_config=types.TranslationConfig(
                target_language_code=self._target_language
            ),
            input_audio_transcription={},
            output_audio_transcription={},
        )
        assembler = TranscriptAssembler(provider_segment=self._segment_index)
        # 직전 확정 발화의 EN — 다음 파이널 번역에 문맥으로만 넘긴다.
        # stream() 로컬이라 재접속(새 stream 호출)마다 자연히 비워진다.
        prev_final_en = ""
        connect_started_at = time.monotonic()
        logger.info(
            "Gemini Live Translate connect starting",
            extra={**trace, "gemini_model": self._model},
        )
        async with client.aio.live.connect(model=self._model, config=config) as session:
            logger.info(
                "Gemini Live Translate connected",
                extra={
                    **trace,
                    "gemini_model": self._model,
                    "gemini_connect_latency_ms": round(
                        (time.monotonic() - connect_started_at) * 1000
                    ),
                },
            )

            async def send_audio() -> None:
                async for chunk in audio:
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=chunk,
                            mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                        )
                    )
                await session.send_realtime_input(audio_stream_end=True)

            send_task = asyncio.create_task(send_audio())
            first_caption_yielded = False
            receive_iter = session.receive().__aiter__()
            receive_next: asyncio.Task[Any] = asyncio.create_task(
                receive_iter.__anext__()
            )
            drain_deadline: float | None = None
            try:
                while True:
                    done, _pending = await asyncio.wait(
                        {receive_next}, timeout=RECEIVE_POLL_S
                    )
                    utterances: list[TranslatedUtterance] = []
                    if receive_next in done:
                        try:
                            message = receive_next.result()
                        except StopAsyncIteration:
                            # receive() iterators are per-turn in the SDK; the
                            # translate model is turnless in practice, but if
                            # one ever ends, start the next unless the meeting
                            # audio is already over.
                            if send_task.done():
                                break
                            receive_iter = session.receive().__aiter__()
                            receive_next = asyncio.create_task(receive_iter.__anext__())
                            continue
                        receive_next = asyncio.create_task(receive_iter.__anext__())
                        # The Live API warns with goAway before enforcing its
                        # session-duration cap and then aborts with 1008 ("client
                        # failed to close after GoAway", observed 2026-07-02 at
                        # the ~10min mark). Recycle proactively: end this stream
                        # cleanly — buffered text is flushed below and
                        # live_session redials in DEFAULT_RECONNECT_DELAYS[0] —
                        # instead of losing the buffer to the abort.
                        if getattr(message, "go_away", None) is not None:
                            logger.info(
                                "Gemini Live Translate go_away — recycling session",
                                extra={
                                    **trace,
                                    "gemini_time_left": str(
                                        getattr(message.go_away, "time_left", None)
                                    ),
                                },
                            )
                            break
                        server_content = getattr(message, "server_content", None)
                        en_text = _transcription_text(server_content, "input_transcription")
                        ko_text = _transcription_text(server_content, "output_transcription")
                        utterances = assembler.feed(en_text, ko_text)
                    else:
                        utterances = assembler.poll()
                    for utterance in utterances:
                        if not first_caption_yielded:
                            first_caption_yielded = True
                            logger.info(
                                "Gemini Live Translate first caption",
                                extra={
                                    **trace,
                                    "gemini_connect_to_first_subtitle_ms": round(
                                        (time.monotonic() - connect_started_at) * 1000
                                    ),
                                },
                            )
                        if utterance.is_final:
                            utterance = await self._apply_final_translation(
                                utterance, client, prev_final_en
                            )
                            prev_final_en = utterance.text_en or prev_final_en
                        yield utterance
                    # Once the meeting audio ends, give the model a short
                    # window to deliver the translation tail, then stop.
                    if send_task.done() and not send_task.cancelled():
                        send_error = send_task.exception()
                        if send_error is not None:
                            raise send_error
                        if drain_deadline is None:
                            drain_deadline = time.monotonic() + 3.0
                        elif utterances:
                            drain_deadline = time.monotonic() + 3.0
                        elif time.monotonic() >= drain_deadline:
                            break
            finally:
                send_task.cancel()
                receive_next.cancel()
                for task in (send_task, receive_next):
                    with contextlib.suppress(BaseException):
                        await task
        for utterance in assembler.flush():
            translated = await self._apply_final_translation(
                utterance, client, prev_final_en
            )
            prev_final_en = translated.text_en or prev_final_en
            yield translated
        logger.info("Gemini Live Translate stream ended", extra=trace)


class GeminiHybridTranslateProvider(GeminiLiveTranslateProvider):
    """하이브리드(트랙 C): 3.5 연속 전사·파셜 + 단어집 텍스트 번역 파이널.

    3.5의 강점(빠른 발화에도 안 끊기는 연속 스트림, ~1.5-3s 파셜)은 그대로
    두고, 약점(용어사전 불가 — 실기 shots→발, five percent→다섯 퍼센트)만
    파이널 교체로 보정한다."""

    _final_translate = True


def _transcription_text(server_content: Any, attr: str) -> str | None:
    transcription = getattr(server_content, attr, None)
    text = getattr(transcription, "text", None)
    return text if isinstance(text, str) and text else None
# === ANCHOR: GEMINI_LIVE_TRANSLATE_END ===
