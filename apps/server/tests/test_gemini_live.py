# === ANCHOR: TEST_GEMINI_LIVE_START ===
"""Tests for Gemini Live response parsing helpers."""
from __future__ import annotations

from types import SimpleNamespace

from apps.server.ai.gemini_live import (
    ManualVadState,
    _build_live_config,
    _estimate_usage_cost_usd,
    _has_subtitle_text,
    _pcm16le_rms_dbfs,
    _should_emit_partial_translation,
    _stream_session,
    extract_live_text,
    extract_usage_metadata,
)


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
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self.generate_content))
        self.calls: list[dict[str, object]] = []

    async def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        contents = str(kwargs["contents"])
        source = contents.rsplit("English: ", 1)[-1]
        return SimpleNamespace(text=f"번역:{source}")


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

    config = _build_live_config(FakeLiveTypes)

    assert config.explicit_vad_signal is True
    assert config.realtime_input_config.automatic_activity_detection.disabled is True


def test_manual_vad_emits_activity_boundaries() -> None:
    vad = ManualVadState(threshold_dbfs=-50.0, end_silence_ms=40)
    speech = (12000).to_bytes(2, "little", signed=True) * 320
    silence = (0).to_bytes(2, "little", signed=True) * 320

    assert _pcm16le_rms_dbfs(speech) > -50.0
    assert vad.observe(speech) == "start"
    assert vad.observe(speech) is None
    assert vad.observe(silence) is None
    assert vad.observe(silence) == "end"
    assert vad.observe(silence) is None


async def _empty_audio():
    if False:
        yield b""


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


def test_partial_translation_cadence_skips_tiny_fragments(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_PARTIAL_MIN_CHARS", raising=False)
    monkeypatch.delenv("GEMINI_PARTIAL_MIN_WORDS", raising=False)

    assert _should_emit_partial_translation("", "Please check") is False


def test_partial_translation_cadence_default_emits_short_first_caption(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_PARTIAL_MIN_CHARS", raising=False)
    monkeypatch.delenv("GEMINI_PARTIAL_MIN_WORDS", raising=False)

    assert _should_emit_partial_translation("", "Please check the layout") is True


def test_partial_translation_cadence_emits_meaningful_boundary(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_PARTIAL_MIN_CHARS", raising=False)
    monkeypatch.delenv("GEMINI_PARTIAL_MIN_WORDS", raising=False)

    text = "Please check the layout before sending the final render."

    assert _should_emit_partial_translation("", text) is True


async def test_stream_session_keeps_long_final_translation_on_same_seq() -> None:
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

    assert len(utterances) == 1
    assert utterances[0].seq == 1
    assert utterances[0].is_final is True
    assert utterances[0].text_ko == "첫 번째 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다."


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
                    input_transcription=SimpleNamespace(text="Please check"),
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
# === ANCHOR: TEST_GEMINI_LIVE_END ===
