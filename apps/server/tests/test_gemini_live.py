# === ANCHOR: TEST_GEMINI_LIVE_START ===
"""Tests for Gemini Live response parsing helpers."""
from __future__ import annotations

from types import SimpleNamespace

from apps.server.ai.gemini_live import extract_live_text


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
# === ANCHOR: TEST_GEMINI_LIVE_END ===
