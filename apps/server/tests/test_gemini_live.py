# === ANCHOR: TEST_GEMINI_LIVE_START ===
"""Tests for Gemini Live response parsing helpers."""
from __future__ import annotations

from types import SimpleNamespace

from apps.server.ai.gemini_live import (
    _estimate_usage_cost_usd,
    _stream_session,
    extract_live_text,
    extract_usage_metadata,
)


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
# === ANCHOR: TEST_GEMINI_LIVE_END ===
