# === ANCHOR: TEST_GEMINI_LIVE_START ===
"""Tests for Gemini Live response parsing helpers."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from apps.server.ai.gemini_live import (
    AudioSegmentState,
    ManualVadState,
    _bounded_audio_segment,
    _build_live_config,
    _estimate_usage_cost_usd,
    _has_subtitle_text,
    _pcm16le_rms_dbfs,
    _should_emit_partial_translation,
    _split_into_sentences,
    _stream_session,
    extract_live_text,
    extract_usage_metadata,
)


def test_split_into_sentences_english_terminal_punctuation() -> None:
    assert _split_into_sentences("Hello there. How are you? I'm good!") == [
        "Hello there.",
        "How are you?",
        "I'm good!",
    ]


def test_split_into_sentences_korean_terminal_punctuation() -> None:
    assert _split_into_sentences("안녕하세요. 오늘 회의 시작합니다. 잘 부탁드립니다.") == [
        "안녕하세요.",
        "오늘 회의 시작합니다.",
        "잘 부탁드립니다.",
    ]


def test_split_into_sentences_returns_single_when_no_boundary() -> None:
    # Continuous speech without sentence-ending punctuation stays as one chunk
    # so the caller can fall back to publishing it whole.
    assert _split_into_sentences("이건 그냥 한 덩어리 텍스트입니다") == [
        "이건 그냥 한 덩어리 텍스트입니다"
    ]


def test_split_into_sentences_handles_empty_and_whitespace() -> None:
    assert _split_into_sentences("") == []
    assert _split_into_sentences("   ") == []


def test_split_into_sentences_keeps_decimals_intact() -> None:
    # "v1.5" stays together because the period inside has no following whitespace.
    assert _split_into_sentences("Use v1.5 of the API.") == ["Use v1.5 of the API."]


def test_split_into_sentences_oversplits_english_abbreviations_known_limit() -> None:
    """Documented edge case: 'Mr.' followed by a space gets split mid-name
    because the regex doesn't carry an abbreviation list. Tolerable for our
    Korean meeting use case where this rarely appears; if it becomes painful
    in practice we'll switch to a smarter splitter."""
    assert _split_into_sentences("Mr. Kim agreed.") == ["Mr.", "Kim agreed."]


class FakeLiveTypes:
    class Modality:
        AUDIO = "AUDIO"
        TEXT = "TEXT"

    class StartSensitivity:
        START_SENSITIVITY_HIGH = "START_SENSITIVITY_HIGH"

    class EndSensitivity:
        END_SENSITIVITY_HIGH = "END_SENSITIVITY_HIGH"

    class ActivityHandling:
        NO_INTERRUPTION = "NO_INTERRUPTION"

    class TurnCoverage:
        TURN_INCLUDES_ONLY_ACTIVITY = "TURN_INCLUDES_ONLY_ACTIVITY"

    class Part(SimpleNamespace):
        pass

    class Content(SimpleNamespace):
        pass

    class AudioTranscriptionConfig(SimpleNamespace):
        pass

    class AutomaticActivityDetection(SimpleNamespace):
        pass

    class RealtimeInputConfig(SimpleNamespace):
        pass

    class LiveConnectConfig(SimpleNamespace):
        pass

    class ActivityStart(SimpleNamespace):
        pass

    class ActivityEnd(SimpleNamespace):
        pass


class FakeTextClient:
    def __init__(self) -> None:
        self.aio = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=self.generate_content,
                generate_content_stream=self.generate_content_stream,
            )
        )
        self.calls: list[dict[str, object]] = []

    async def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        contents = str(kwargs["contents"])
        source = contents.rsplit("English: ", 1)[-1]
        return SimpleNamespace(text=f"번역:{source}")

    async def generate_content_stream(self, **kwargs: object) -> object:
        # Backwards-compatible single-chunk stream: production code now uses
        # the streaming API, but existing tests stay correct because we wrap
        # the same generate_content response as one chunk.
        async def _stream():
            response = await self.generate_content(**kwargs)
            yield response
        return _stream()


def test_extract_live_text_reads_input_transcription_and_model_text() -> None:
    message = SimpleNamespace(
        server_content=SimpleNamespace(
            input_transcription=SimpleNamespace(text="Please check the layout."),
            output_transcription=None,
            model_turn=SimpleNamespace(
                parts=[SimpleNamespace(text="layout을 확인해 주세요.")]
            ),
            turn_complete=True,
        )
    )

    extracted = extract_live_text(message)

    assert extracted.input_text == "Please check the layout."
    assert extracted.output_text == "layout을 확인해 주세요."
    assert extracted.turn_complete is True


def test_extract_live_text_accepts_output_transcription_fallback() -> None:
    message = SimpleNamespace(
        server_content=SimpleNamespace(
            input_transcription=None,
            output_transcription=SimpleNamespace(text="안녕하세요."),
            model_turn=None,
            turn_complete=False,
        )
    )

    extracted = extract_live_text(message)

    assert extracted.input_text == ""
    assert extracted.output_text == "안녕하세요."
    assert extracted.turn_complete is False


def test_extract_usage_metadata_reads_python_sdk_fields() -> None:
    message = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=120,
            candidates_token_count=30,
            total_token_count=150,
        )
    )

    usage = extract_usage_metadata(message)

    assert usage is not None
    assert usage.prompt_token_count == 120
    assert usage.candidates_token_count == 30
    assert usage.total_token_count == 150


def test_extract_usage_metadata_reads_api_camel_case_fields() -> None:
    message = SimpleNamespace(
        usageMetadata=SimpleNamespace(
            promptTokenCount=10,
            candidatesTokenCount=5,
            totalTokenCount=15,
        )
    )

    usage = extract_usage_metadata(message)

    assert usage is not None
    assert usage.prompt_token_count == 10
    assert usage.candidates_token_count == 5
    assert usage.total_token_count == 15


def test_estimate_usage_cost_uses_configured_rates(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_INPUT_USD_PER_1M_TOKENS", "1.25")
    monkeypatch.setenv("GEMINI_OUTPUT_USD_PER_1M_TOKENS", "10")
    usage = extract_usage_metadata(
        SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=1_000_000,
                candidates_token_count=100_000,
                total_token_count=1_100_000,
            )
        )
    )

    assert usage is not None
    assert _estimate_usage_cost_usd(usage) == 2.25


def test_build_live_config_sets_low_latency_vad(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_RESPONSE_MODALITY", raising=False)
    monkeypatch.delenv("GEMINI_VAD_PREFIX_PADDING_MS", raising=False)
    monkeypatch.delenv("GEMINI_VAD_SILENCE_DURATION_MS", raising=False)
    monkeypatch.setenv("GEMINI_EXPLICIT_VAD_ENABLED", "0")

    config = _build_live_config(FakeLiveTypes)

    vad = config.realtime_input_config.automatic_activity_detection
    assert config.response_modalities == ["AUDIO"]
    assert config.output_audio_transcription is not None
    assert vad.disabled is False
    assert vad.start_of_speech_sensitivity == "START_SENSITIVITY_HIGH"
    assert vad.end_of_speech_sensitivity == "END_SENSITIVITY_HIGH"
    assert vad.prefix_padding_ms == 120
    assert vad.silence_duration_ms == 350
    assert config.realtime_input_config.activity_handling == "NO_INTERRUPTION"
    assert config.realtime_input_config.turn_coverage == "TURN_INCLUDES_ONLY_ACTIVITY"


def test_build_live_config_can_use_text_response_modality(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RESPONSE_MODALITY", "TEXT")

    config = _build_live_config(FakeLiveTypes)

    assert config.response_modalities == ["TEXT"]
    assert config.output_audio_transcription is None


def test_build_live_config_omits_explicit_vad_by_default(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_EXPLICIT_VAD_ENABLED", raising=False)

    config = _build_live_config(FakeLiveTypes)

    assert not hasattr(config, "explicit_vad_signal")
    assert config.realtime_input_config.automatic_activity_detection.disabled is False


def test_build_live_config_can_disable_explicit_vad(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_EXPLICIT_VAD_ENABLED", "0")

    config = _build_live_config(FakeLiveTypes)

    assert not hasattr(config, "explicit_vad_signal")
    assert config.realtime_input_config.automatic_activity_detection.disabled is False


def test_build_live_config_can_enable_explicit_vad(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_EXPLICIT_VAD_ENABLED", "1")
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "1")

    config = _build_live_config(FakeLiveTypes)

    assert config.explicit_vad_signal is True
    assert config.realtime_input_config.automatic_activity_detection.disabled is True


def test_build_live_config_falls_back_when_explicit_vad_unsupported(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_EXPLICIT_VAD_ENABLED", "1")
    monkeypatch.delenv("GOOGLE_GENAI_USE_ENTERPRISE", raising=False)

    config = _build_live_config(FakeLiveTypes)

    assert not hasattr(config, "explicit_vad_signal")
    assert config.realtime_input_config.automatic_activity_detection.disabled is False


def test_manual_vad_emits_activity_boundaries() -> None:
    vad = ManualVadState(threshold_dbfs=-50.0, end_silence_ms=40, max_speech_ms=0)
    speech = (12000).to_bytes(2, "little", signed=True) * 320
    silence = (0).to_bytes(2, "little", signed=True) * 320

    assert _pcm16le_rms_dbfs(speech) > -50.0
    assert vad.observe(speech) == "start"
    assert vad.observe(speech) is None
    assert vad.observe(silence) is None
    assert vad.observe(silence) == "end"
    assert vad.observe(silence) is None


def test_manual_vad_restarts_long_continuous_speech() -> None:
    vad = ManualVadState(threshold_dbfs=-50.0, end_silence_ms=320, max_speech_ms=60)
    speech = (12000).to_bytes(2, "little", signed=True) * 320

    assert vad.observe(speech) == "start"
    assert vad.observe(speech) is None
    assert vad.observe(speech) == "restart"
    assert vad.observe(speech) is None


async def _empty_audio():
    if False:
        yield b""


async def _audio_chunks(count: int):
    for index in range(count):
        yield bytes([index]) * 640


async def test_bounded_audio_segment_caps_one_gemini_turn() -> None:
    source = _audio_chunks(5).__aiter__()
    state = AudioSegmentState()

    first = [chunk async for chunk in _bounded_audio_segment(source, state, 2)]
    second = [chunk async for chunk in _bounded_audio_segment(source, state, 2)]

    assert len(first) == 2
    assert len(second) == 2
    assert state.exhausted is False


async def test_bounded_audio_segment_marks_source_exhausted() -> None:
    source = _audio_chunks(1).__aiter__()
    state = AudioSegmentState()

    chunks = [chunk async for chunk in _bounded_audio_segment(source, state, 3)]

    assert len(chunks) == 1
    assert state.exhausted is True


async def test_bounded_audio_segment_marks_speech_observed() -> None:
    speech = (12000).to_bytes(2, "little", signed=True) * 320
    source = _audio_chunks(1).__aiter__()
    state = AudioSegmentState()

    chunks = [chunk async for chunk in _bounded_audio_segment(source, state, 1)]

    assert len(chunks) == 1
    assert state.speech_observed is False

    state = AudioSegmentState()
    chunks = [chunk async for chunk in _bounded_audio_segment(_single_chunk(speech), state, 1)]

    assert len(chunks) == 1
    assert state.speech_observed is True


async def _single_chunk(chunk: bytes):
    yield chunk


_SILENCE_CHUNK = b"\x00" * 640
_SPEECH_CHUNK = (12000).to_bytes(2, "little", signed=True) * 320


async def _mixed_chunks(*chunks: bytes):
    for chunk in chunks:
        yield chunk


async def test_bounded_audio_segment_silence_aware_waits_for_silence_run() -> None:
    # soft target=3, hard=20, silence run required=2.
    # Stream: 4 speech chunks (passes soft target without silence), then 2 silence chunks.
    # Expect: stays open through the 4 speech chunks, exits after the 2-chunk silence run.
    chunks = (_SPEECH_CHUNK, _SPEECH_CHUNK, _SPEECH_CHUNK, _SPEECH_CHUNK,
              _SILENCE_CHUNK, _SILENCE_CHUNK,
              _SPEECH_CHUNK)  # last not consumed
    source = _mixed_chunks(*chunks).__aiter__()
    state = AudioSegmentState()

    yielded = [
        chunk async for chunk in _bounded_audio_segment(
            source, state, max_chunks=3, hard_max_chunks=20, silence_chunk_run=2,
        )
    ]

    # Should consume 4 speech + 2 silence = 6 chunks, then cycle.
    assert len(yielded) == 6
    assert state.speech_observed is True


async def test_bounded_audio_segment_hard_backstop_cuts_continuous_speech() -> None:
    # Hard backstop=5. All chunks speech, silence run never met.
    chunks = tuple(_SPEECH_CHUNK for _ in range(10))
    source = _mixed_chunks(*chunks).__aiter__()
    state = AudioSegmentState()

    yielded = [
        chunk async for chunk in _bounded_audio_segment(
            source, state, max_chunks=3, hard_max_chunks=5, silence_chunk_run=2,
        )
    ]

    assert len(yielded) == 5
    assert state.speech_observed is True


async def test_bounded_audio_segment_exits_at_soft_target_when_silence_disabled() -> None:
    # silence_chunk_run=0 (disabled) preserves legacy hard-cut behavior at soft target.
    chunks = tuple(_SPEECH_CHUNK for _ in range(10))
    source = _mixed_chunks(*chunks).__aiter__()
    state = AudioSegmentState()

    yielded = [
        chunk async for chunk in _bounded_audio_segment(
            source, state, max_chunks=4, hard_max_chunks=0, silence_chunk_run=0,
        )
    ]

    assert len(yielded) == 4


async def test_stream_session_emits_output_transcription_before_turn_complete() -> None:
    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout."),
                    output_transcription=SimpleNamespace(text="layout 확인 부탁드립니다."),
                    model_turn=None,
                    turn_complete=False,
                )
            )

    utterances = [
        item async for item in _stream_session(FakeSession(), FakeTypes, _empty_audio())
    ]

    assert len(utterances) == 1
    assert utterances[0].seq == 1
    assert utterances[0].text_en == "Please check the layout."
    assert utterances[0].text_ko == "layout 확인 부탁드립니다."
    assert utterances[0].is_final is False


async def test_stream_session_emits_fast_partial_from_input_transcription(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout."),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )

    text_client = FakeTextClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    assert len(utterances) == 1
    assert utterances[0].seq == 1
    assert utterances[0].text_en == "Please check the layout."
    assert utterances[0].text_ko == "번역:Please check the layout."
    assert utterances[0].is_final is False
    assert text_client.calls[0]["model"] == "gemini-2.5-flash-lite"


def test_partial_translation_cadence_advances_without_punctuation(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "24")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "5")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_DELTA_CHARS", "12")

    first = "the speaker keeps talking without punctuation"
    second = f"{first} and adds more useful context"

    assert _should_emit_partial_translation("", first) is True
    assert _should_emit_partial_translation(first, second) is True


def test_has_subtitle_text_rejects_empty_placeholders() -> None:
    assert _has_subtitle_text("layout 확인 부탁드립니다.") is True
    assert _has_subtitle_text("") is False
    assert _has_subtitle_text("(자막 없음)") is False
    assert _has_subtitle_text(" no subtitles ") is False


async def test_stream_session_skips_no_subtitle_placeholder(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")

    class PlaceholderTextClient(FakeTextClient):
        async def generate_content(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            return SimpleNamespace(text="(자막 없음)")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout."),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="(자막 없음)"),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            PlaceholderTextClient(),
        )
    ]

    assert utterances == []


async def test_stream_session_keeps_partial_revisions_on_same_seq(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_DELTA_CHARS", "10")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            first_text = "Please check the layout and confirm"
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text=first_text),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(
                        text="Please check the layout and please confirm the lighting before delivery."
                    ),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="최종 번역입니다."),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    text_client = FakeTextClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    assert [item.seq for item in utterances] == [1, 1, 1]
    assert [item.is_final for item in utterances] == [False, False, True]
    assert utterances[0].text_en == "Please check the layout and confirm"
    assert utterances[1].text_en == "Please check the layout and please confirm the lighting before delivery."
    assert utterances[2].text_ko == "최종 번역입니다."
    assert "lighting before delivery" in str(text_client.calls[1]["contents"])


async def test_stream_session_finalizes_partial_when_turn_complete_has_placeholder(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout."),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="(자막 없음)"),
                    model_turn=None,
                    turn_complete=True,
                )
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(
                        text="Please confirm the lighting before sending the delivery."
                    ),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )

    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            FakeTextClient(),
        )
    ]

    assert [item.seq for item in utterances] == [1, 1, 2]
    assert [item.is_final for item in utterances] == [False, True, False]
    assert utterances[1].text_ko == utterances[0].text_ko


async def test_stream_session_continues_when_fast_partial_translation_fails(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")

    class FailingTextClient(FakeTextClient):
        async def generate_content(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            raise RuntimeError("partial translation unavailable")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout."),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="layout 확인 부탁드립니다."),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    text_client = FailingTextClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    assert len(utterances) == 1
    assert utterances[0].text_ko == "layout 확인 부탁드립니다."
    assert utterances[0].is_final is True
    assert len(text_client.calls) == 1


async def test_stream_session_retries_partial_translation_on_server_error(monkeypatch) -> None:
    """Gemini 5xx ServerError on partial translation triggers one quick retry."""
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")
    monkeypatch.setenv("GEMINI_PARTIAL_TRANSLATION_RETRY_BACKOFF_MS", "0")

    # Production code prefers isinstance against google.genai.errors.ServerError
    # but falls back to a class-name check so this stand-in still hits retry.
    class ServerError(Exception):
        pass

    class FlakyTextClient(FakeTextClient):
        async def generate_content(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise ServerError("transient 5xx")
            return SimpleNamespace(text="레이아웃 확인")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout."),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="layout 확인 부탁드립니다."),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    text_client = FlakyTextClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    assert len(text_client.calls) == 2
    assert utterances[-1].text_ko == "layout 확인 부탁드립니다."
    assert utterances[-1].is_final is True
    partials = [u for u in utterances if not u.is_final]
    assert any(u.text_ko == "레이아웃 확인" for u in partials)


async def test_stream_session_drops_partial_when_retry_also_fails(monkeypatch) -> None:
    """If retry also fails with ServerError, partial is dropped after exactly
    two attempts and the main loop still delivers the final translation."""
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")
    monkeypatch.setenv("GEMINI_PARTIAL_TRANSLATION_RETRY_BACKOFF_MS", "0")

    class ServerError(Exception):
        pass

    class AlwaysFailingClient(FakeTextClient):
        async def generate_content(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            raise ServerError("persistent 5xx")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout."),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="layout 확인 부탁드립니다."),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    text_client = AlwaysFailingClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    assert len(text_client.calls) == 2
    assert len(utterances) == 1
    assert utterances[0].text_ko == "layout 확인 부탁드립니다."
    assert utterances[0].is_final is True


async def test_stream_session_cancels_stale_partial_for_fresher_text(monkeypatch) -> None:
    """In-flight partial that has been running past the stale threshold is
    cancelled when a sufficiently different text arrives, and the fresher
    text immediately fires a new partial. The cancelled partial's would-be
    output is never published."""
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")
    monkeypatch.setenv("GEMINI_PARTIAL_TRANSLATION_CANCEL_STALE_MS", "20")
    monkeypatch.setenv("GEMINI_PARTIAL_TRANSLATION_TIMEOUT_MS", "5000")

    class SlowTextClient(FakeTextClient):
        async def generate_content(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            contents_str = str(kwargs["contents"])
            await asyncio.sleep(0.05)
            return SimpleNamespace(
                text="조명까지 확인" if "lighting" in contents_str else "레이아웃 확인"
            )

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout"),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            # Let the in-flight partial age past the 20ms stale threshold.
            await asyncio.sleep(0.04)
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(
                        text="Please check the layout and the lighting too"
                    ),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            # Give the fresh follow-up partial time to complete.
            await asyncio.sleep(0.15)
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="layout 확인 부탁드립니다."),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    text_client = SlowTextClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    # Both calls land at text_client (cancelled call still appended on entry).
    assert len(text_client.calls) == 2
    partials = [u for u in utterances if not u.is_final]
    # Cancelled partial's Korean is never published.
    assert all("레이아웃" not in u.text_ko for u in partials)
    # Fresh partial's Korean appears.
    assert any("조명까지" in u.text_ko for u in partials)
    # Final still delivered downstream.
    assert utterances[-1].text_ko == "layout 확인 부탁드립니다."
    assert utterances[-1].is_final is True


async def test_stream_session_does_not_cancel_when_in_flight_is_fresh(monkeypatch) -> None:
    """If in-flight has barely started, don't cancel — queue the new text and
    fire follow-up after in-flight completes (legacy behaviour). This guards
    against cancelling things that would have finished in the next few ms."""
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")
    # 5 seconds stale threshold — in-flight will never be considered stale
    # within the test's runtime, so cancellation must NOT fire.
    monkeypatch.setenv("GEMINI_PARTIAL_TRANSLATION_CANCEL_STALE_MS", "5000")
    monkeypatch.setenv("GEMINI_PARTIAL_TRANSLATION_TIMEOUT_MS", "5000")

    class SlowTextClient(FakeTextClient):
        async def generate_content(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            contents_str = str(kwargs["contents"])
            await asyncio.sleep(0.05)
            return SimpleNamespace(
                text="조명까지 확인" if "lighting" in contents_str else "레이아웃 확인"
            )

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout"),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            await asyncio.sleep(0.01)
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(
                        text="Please check the layout and the lighting too"
                    ),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            await asyncio.sleep(0.2)
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="layout 확인 부탁드립니다."),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    text_client = SlowTextClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    # Both calls still happen (queue → follow-up), but cancelled partial's
    # result was NOT discarded — both partials get published.
    assert len(text_client.calls) == 2
    partials = [u for u in utterances if not u.is_final]
    # In-flight ran to completion; its result is part of the published partials.
    assert any("레이아웃" in u.text_ko for u in partials)
    assert utterances[-1].text_ko == "layout 확인 부탁드립니다."


async def test_stream_session_publishes_each_streamed_chunk(monkeypatch) -> None:
    """Streaming partial translation publishes per chunk: a model emitting
    three deltas should produce three TranslatedUtterance updates whose
    text_ko grows cumulatively (1 char → 4 chars → 9 chars in this fixture)."""
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")

    class MultiChunkTextClient(FakeTextClient):
        async def generate_content_stream(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            async def _stream():
                for delta in ("레이아웃", " 확인", " 부탁드립니다"):
                    yield SimpleNamespace(text=delta)
            return _stream()

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout."),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            # Give the streaming driver time to push all chunks before the
            # turn_complete arrives and tears down the partial.
            await asyncio.sleep(0.05)
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="layout 확인 부탁드립니다."),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    text_client = MultiChunkTextClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    partials = [u for u in utterances if not u.is_final]
    partial_texts = [u.text_ko for u in partials]
    # All three cumulative snapshots reach the consumer.
    assert "레이아웃" in partial_texts
    assert "레이아웃 확인" in partial_texts
    assert "레이아웃 확인 부탁드립니다" in partial_texts
    # Final translation still arrives from the separate Gemini Live output path.
    assert utterances[-1].text_ko == "layout 확인 부탁드립니다."
    assert utterances[-1].is_final is True


async def test_stream_session_continues_when_fast_partial_translation_times_out(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")
    monkeypatch.setenv("GEMINI_PARTIAL_TRANSLATION_TIMEOUT_MS", "5")

    class SlowTextClient(FakeTextClient):
        async def generate_content(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            await asyncio.sleep(0.05)
            return SimpleNamespace(text="느린 partial")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout."),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="layout 확인 부탁드립니다."),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    text_client = SlowTextClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    assert len(utterances) == 1
    assert utterances[0].text_ko == "layout 확인 부탁드립니다."
    assert utterances[0].is_final is True
    assert len(text_client.calls) == 1


def test_partial_translation_cadence_skips_tiny_fragments(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_PARTIAL_MIN_CHARS", raising=False)
    monkeypatch.delenv("GEMINI_PARTIAL_MIN_WORDS", raising=False)

    assert _should_emit_partial_translation("", "hello") is False


def test_partial_translation_cadence_default_emits_short_first_caption(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_PARTIAL_MIN_CHARS", raising=False)
    monkeypatch.delenv("GEMINI_PARTIAL_MIN_WORDS", raising=False)

    assert _should_emit_partial_translation("", "Please check the layout") is True


def test_partial_translation_cadence_emits_meaningful_boundary(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_PARTIAL_MIN_CHARS", raising=False)
    monkeypatch.delenv("GEMINI_PARTIAL_MIN_WORDS", raising=False)

    text = "Please check the layout before sending the final render."

    assert _should_emit_partial_translation("", text) is True


async def test_stream_session_cycles_early_on_empty_segment_tail(monkeypatch) -> None:
    """Once we've published at least one subtitle, if no further utterance
    arrives within GEMINI_SEGMENT_EMPTY_TAIL_CYCLE_MS the segment cycles
    early instead of waiting for the soft/hard cap. This eliminates the
    'one input batch then silence' worst case we saw with Gemini 3.1."""
    monkeypatch.setenv("GEMINI_SEGMENT_EMPTY_TAIL_CYCLE_MS", "50")
    monkeypatch.setenv("GEMINI_RECEIVE_POLL_TIMEOUT_MS", "10")
    monkeypatch.setenv("GEMINI_SEGMENT_STUCK_WATCHDOG_MS", "10000")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="첫 자막"),
                    model_turn=None,
                    turn_complete=False,
                )
            )
            # Simulate Gemini going silent after the first transcription event —
            # this is the 3.1 'empty tail' pattern we want to detect.
            await asyncio.Event().wait()

    utterances = [
        item async for item in _stream_session(FakeSession(), FakeTypes, _empty_audio())
    ]

    assert len(utterances) == 1
    assert utterances[0].text_ko == "첫 자막"
    # If the empty-tail cycle had not fired, _stream_session would still be
    # waiting on FakeSession's hung receive — reaching this line proves it
    # exited.


async def test_stream_session_splits_long_final_translation_into_sentence_subtitles() -> None:
    """A turn_complete dump containing several Korean sentences is published
    as one TranslatedUtterance per sentence (each is_final=True, seq bumped
    per sentence) so the desktop subtitle UI shows readable chunks instead of
    a 100+ word wall the operator can't keep up with."""
    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(
                        text="The speaker is talking for a long time."
                    ),
                    output_transcription=SimpleNamespace(
                        text="첫 번째 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다."
                    ),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    utterances = [
        item async for item in _stream_session(FakeSession(), FakeTypes, _empty_audio())
    ]

    assert [u.text_ko for u in utterances] == [
        "첫 번째 문장입니다.",
        "두 번째 문장입니다.",
        "세 번째 문장입니다.",
    ]
    assert [u.seq for u in utterances] == [1, 2, 3]
    assert all(u.is_final for u in utterances)
    # English is attached only to the first sentence; later sentences carry
    # empty text_en because we don't have per-sentence English alignment.
    assert utterances[0].text_en == "The speaker is talking for a long time."
    assert utterances[1].text_en == ""
    assert utterances[2].text_en == ""


async def test_stream_session_reuses_live_connection_across_receive_turns() -> None:
    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        def __init__(self) -> None:
            self.receive_calls = 0
            self.second_turn_seen = asyncio.Event()

        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            self.receive_calls += 1
            if self.receive_calls > 2:
                return
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text=f"Please check turn {self.receive_calls}."),
                    output_transcription=SimpleNamespace(text=f"turn {self.receive_calls} 확인"),
                    model_turn=None,
                    turn_complete=True,
                )
            )
            if self.receive_calls == 2:
                self.second_turn_seen.set()

    async def audio_until_second_turn(session: FakeSession):
        yield b"\x01" * 640
        await session.second_turn_seen.wait()

    session = FakeSession()
    utterances = [
        item async for item in _stream_session(session, FakeTypes, audio_until_second_turn(session))
    ]

    assert session.receive_calls == 2
    assert [item.seq for item in utterances] == [1, 2]
    assert [item.text_ko for item in utterances] == ["turn 1 확인", "turn 2 확인"]


async def test_stream_session_exits_silent_segment_after_audio_is_sent(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RECEIVE_POLL_TIMEOUT_MS", "5")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class HangingReceive:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()
            raise StopAsyncIteration

    class FakeSession:
        def __init__(self) -> None:
            self.sent_stream_end = False

        async def send_realtime_input(self, **kwargs: object) -> None:
            if kwargs.get("audio_stream_end") is True:
                self.sent_stream_end = True

        def receive(self):
            return HangingReceive()

    session = FakeSession()

    utterances = [
        item async for item in _stream_session(session, FakeTypes, _audio_chunks(1))
    ]

    assert utterances == []
    assert session.sent_stream_end is True


async def test_stream_session_waits_for_speech_segment_after_audio_is_sent(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RECEIVE_POLL_TIMEOUT_MS", "5")
    monkeypatch.setenv("GEMINI_RECEIVE_DRAIN_TIMEOUT_MS", "100")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class DelayedReceive:
        def __init__(self) -> None:
            self.sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.sent:
                raise StopAsyncIteration
            await asyncio.sleep(0.02)
            self.sent = True
            return SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout."),
                    output_transcription=SimpleNamespace(text="layout 확인 부탁드립니다."),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        def receive(self):
            return DelayedReceive()

    speech = (12000).to_bytes(2, "little", signed=True) * 320
    utterances = [
        item async for item in _stream_session(FakeSession(), FakeTypes, _single_chunk(speech))
    ]

    assert len(utterances) == 1
    assert utterances[0].text_ko == "layout 확인 부탁드립니다."


async def test_stream_session_skips_fast_partial_for_short_fragment(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="hello"),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )

    text_client = FakeTextClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    assert utterances == []
    assert text_client.calls == []


async def test_stream_session_does_not_emit_english_only_turn() -> None:
    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="This should not appear as a subtitle."),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=True,
                )
            )

    utterances = [
        item async for item in _stream_session(FakeSession(), FakeTypes, _empty_audio())
    ]

    assert utterances == []


async def test_stream_session_uses_incremental_partial_when_text_extends(monkeypatch) -> None:
    """Q-1: 두 번째 input_text가 이전을 strict extend하면 incremental delta 번역을
    호출하고, 결과를 prev_ko에 이어붙여 최종 text_ko를 구성해야 한다."""
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_DELTA_CHARS", "10")

    class IncrementalAwareTextClient(FakeTextClient):
        async def generate_content(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            contents = str(kwargs["contents"])
            if "New English continuation to translate:" in contents:
                return SimpleNamespace(text="라마")
            return SimpleNamespace(text="가나다")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout"),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(
                        text="Please check the layout before delivery please"
                    ),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )

    text_client = IncrementalAwareTextClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    assert len(utterances) == 2
    assert utterances[0].text_ko == "가나다"
    # 두 번째 호출은 incremental — prev_ko("가나다") + delta 번역("라마")로 합쳐짐.
    assert utterances[1].text_ko == "가나다라마"
    assert "New English continuation to translate:" in str(text_client.calls[1]["contents"])


async def test_stream_session_queues_followup_when_partial_in_flight(monkeypatch) -> None:
    """Q-2': in-flight partial이 끝나기 전에 새 input_text가 도착하면 따로 다시
    fire하지 않고 pending_partial_text에 기억해뒀다가, 완료 직후 자동으로
    follow-up partial을 발사해야 한다 (cost 누수 0)."""
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_DELTA_CHARS", "10")

    release_first = asyncio.Event()
    finished_first = asyncio.Event()

    class GatedTextClient(FakeTextClient):
        async def generate_content(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                # 첫 호출은 release_first가 set될 때까지 대기 — in-flight 시간 확보.
                await release_first.wait()
                finished_first.set()
                return SimpleNamespace(text="번역 A")
            return SimpleNamespace(text="번역 B")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    second_yielded = asyncio.Event()

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="First sentence please now"),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(
                        text="Different revised sentence completely now"
                    ),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            second_yielded.set()
            # 두 번째 yield가 _stream_session에 들어간 시점에서 첫 호출 release.
            await asyncio.sleep(0.05)
            release_first.set()
            # follow-up partial이 완료될 때까지 잠시 기다린 후 종료.
            await asyncio.sleep(0.05)

    text_client = GatedTextClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    # 두 input_text 모두 partial로 emit돼야 함 (첫째: 번역 A, 둘째 follow-up: 번역 B).
    text_kos = [u.text_ko for u in utterances]
    assert "번역 A" in text_kos
    assert "번역 B" in text_kos
    # text_client는 정확히 두 번 호출됨 — 동시 호출 없음.
    assert len(text_client.calls) == 2


async def test_stream_session_watchdog_breaks_when_no_transcription(monkeypatch) -> None:
    """Speech가 들어갔는데도 Gemini가 input/output을 안 내보내면 watchdog가
    force-cycle하여 깔끔하게 종료해야 한다."""
    monkeypatch.setenv("GEMINI_SEGMENT_STUCK_WATCHDOG_MS", "150")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class StuckSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            # Gemini가 stuck 상태처럼 아무것도 안 보냄.
            event = asyncio.Event()
            await event.wait()
            yield  # pragma: no cover — never reached

    speech = (12000).to_bytes(2, "little", signed=True) * 320
    utterances = [
        item async for item in _stream_session(StuckSession(), FakeTypes, _single_chunk(speech))
    ]

    # watchdog에 의해 깔끔하게 break돼 utterance 없이 종료.
    assert utterances == []


async def test_stream_session_does_not_resplit_final_when_partial_shown(monkeypatch) -> None:
    """Regression: when a partial (is_final=False) was already shown this turn,
    a multi-sentence turn_complete final must overwrite that partial IN PLACE as
    a single is_final=True utterance at the partial's seq — NOT be re-split into
    new seqs. Re-splitting made already-read sentences reappear as new bottom
    rows in the operator console."""
    monkeypatch.setenv("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_CHARS", "10")
    monkeypatch.setenv("GEMINI_PARTIAL_MIN_WORDS", "3")

    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(text="Please check the layout."),
                    output_transcription=None,
                    model_turn=None,
                    turn_complete=False,
                )
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=None,
                    output_transcription=SimpleNamespace(
                        text="첫 번째 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다."
                    ),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    text_client = FakeTextClient()
    utterances = [
        item
        async for item in _stream_session(
            FakeSession(),
            FakeTypes,
            _empty_audio(),
            text_client,
        )
    ]

    finals = [u for u in utterances if u.is_final]
    # Exactly ONE final, carrying the full multi-sentence Korean, at the
    # partial's seq (1) — no new seqs minted for sentences 2..k.
    assert len(finals) == 1
    assert finals[0].seq == 1
    assert finals[0].text_ko == "첫 번째 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다."
    # Every utterance this turn stays on seq 1 (partial revisions + the final).
    assert {u.seq for u in utterances} == {1}
    # The partial(s) shown before the final are still is_final=False.
    assert any(not u.is_final for u in utterances)


async def test_stream_session_still_splits_multi_sentence_final_without_partial() -> None:
    """Counterpart guard: when NO partial was shown this turn, the per-sentence
    split MUST still happen (one is_final=True per sentence, seq bumped per
    sentence). This is the unchanged behaviour for partial-free turns."""
    class FakeTypes:
        class Blob:
            def __init__(self, data: bytes, mime_type: str) -> None:
                self.data = data
                self.mime_type = mime_type

    class FakeSession:
        async def send_realtime_input(self, **_kwargs: object) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    input_transcription=SimpleNamespace(
                        text="The speaker is talking for a long time."
                    ),
                    output_transcription=SimpleNamespace(
                        text="첫 번째 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다."
                    ),
                    model_turn=None,
                    turn_complete=True,
                )
            )

    # No text_client → fast-partial path never runs → no partial shown.
    utterances = [
        item async for item in _stream_session(FakeSession(), FakeTypes, _empty_audio())
    ]

    assert [u.text_ko for u in utterances] == [
        "첫 번째 문장입니다.",
        "두 번째 문장입니다.",
        "세 번째 문장입니다.",
    ]
    assert [u.seq for u in utterances] == [1, 2, 3]
    assert all(u.is_final for u in utterances)
# === ANCHOR: TEST_GEMINI_LIVE_END ===
