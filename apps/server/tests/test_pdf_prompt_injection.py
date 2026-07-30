from __future__ import annotations

import json

import pytest

from apps.server.domain.video_captions.translate import GeminiFlashTranslator


@pytest.mark.asyncio
async def test_default_prompt_is_subtitle_prompt(monkeypatch):
    """잠금: prompt_builder 미지정 시 기존 자막 프롬프트 그대로 — 영상 번역 무변경."""
    captured = {}

    async def _spy(self, prompt):
        captured["prompt"] = prompt
        return json.dumps(["안녕"])

    monkeypatch.setattr(GeminiFlashTranslator, "_generate", _spy)
    out = await GeminiFlashTranslator(api_key="x").translate_batch(["Hi"])
    assert out == ["안녕"]
    assert "subtitle line" in captured["prompt"]


@pytest.mark.asyncio
async def test_custom_prompt_builder_is_used(monkeypatch):
    captured = {}

    async def _spy(self, prompt):
        captured["prompt"] = prompt
        return json.dumps(["안녕"])

    monkeypatch.setattr(GeminiFlashTranslator, "_generate", _spy)
    t = GeminiFlashTranslator(api_key="x",
                              prompt_builder=lambda texts: f"CUSTOM {len(texts)}")
    await t.translate_batch(["Hi"])
    assert captured["prompt"] == "CUSTOM 1"


def test_create_translator_passes_builder_to_cli(monkeypatch):
    from apps.server.domain.video_captions import translate_cli as tc
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    builder = lambda texts: "B"
    t = tc.create_translator("claude", prompt_builder=builder)
    assert isinstance(t, tc.CliTranslator)
    assert t._prompt_builder is builder


def test_create_translator_passes_builder_to_custom_cli(monkeypatch):
    """custom provider도 CliTranslator 기반이라 gemini/claude 등과 동일하게
    prompt_builder를 받아야 한다 — 누락 시 later PDF 호출부가 조용히 자막
    프롬프트로 되돌아가는 함정을 막는다."""
    from apps.server.domain.video_captions import translate_cli as tc
    monkeypatch.setenv(tc.CUSTOM_CLI_ENV, "some-custom-cli")
    builder = lambda texts: "B"
    t = tc.create_translator("custom", prompt_builder=builder)
    assert isinstance(t, tc.CliTranslator)
    assert t._prompt_builder is builder
