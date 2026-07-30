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


# ── CLI 경로 행위 잠금 (전브랜치 리뷰 M-8) ────────────────────────────────
# 위 두 배선 테스트는 `t._prompt_builder is builder`(private 속성 동일성)만
# 본다 — CliTranslator.translate_batch의 프롬프트 선택
# (`(self._prompt_builder or build_translation_prompt)(texts)`)은 **한 번도
# 실행되지 않았다.** 아픈 이유: 이 리포의 자막메이커 기본 번역 엔진이
# Claude CLI이고 이 브랜치의 실기 E2E도 claude CLI로 돌았다 — 즉 행위 잠금이
# 있던 쪽(gemini)이 부수 경로이고, 없던 쪽(CLI)이 주력 경로다. 저 한 줄이
# 잘못 바뀌면 자막 번역 품질이 조용히 격하되는데 아무 테스트도 안 깨졌다.
# "라이브 자막 변경 금지"는 이 리포의 하드 제약이라 이 비대칭을 메운다.

def _spy_cli_prompt(monkeypatch) -> dict:
    """CliTranslator가 실제로 CLI에 넘긴 프롬프트를 포착 — 위 gemini 테스트의
    `_generate` 스파이(:15-17)와 같은 자리에 놓은 CLI판 스파이."""
    from apps.server.domain.video_captions import translate_cli as tc
    captured: dict[str, str] = {}

    def _spy(self, prompt):
        captured["prompt"] = prompt
        return json.dumps(["안녕"])

    # _ensure_binary가 실제 CLI 설치를 요구하므로(없으면 TranslationError)
    # 해석기만 가짜로 돌린다 — 검증 대상은 프롬프트 선택이지 PATH가 아니다.
    monkeypatch.setattr(tc, "resolve_cli", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(tc.CliTranslator, "_run_cli", _spy)
    return captured


@pytest.mark.asyncio
async def test_cli_default_prompt_is_subtitle_prompt(monkeypatch):
    """잠금: CLI provider도 prompt_builder 미지정 시 기존 자막 프롬프트를
    그대로 쓴다 — translate_cli.py의 `or build_translation_prompt` 폴백이
    실제로 발동하는지 행위로 확인한다(자막 무변경 계약의 주력 경로)."""
    from apps.server.domain.video_captions import translate_cli as tc
    captured = _spy_cli_prompt(monkeypatch)
    out = await tc.create_translator("claude").translate_batch(["Hi"])
    assert out == ["안녕"]  # 파싱까지 정상 왕복(스파이만 맞고 끝나지 않음)
    assert "subtitle line" in captured["prompt"]


@pytest.mark.asyncio
async def test_cli_custom_prompt_builder_is_actually_sent(monkeypatch):
    """잠금: 주입한 builder가 배선만 되는 게 아니라 **실제로 CLI에 그 프롬프트가
    간다** — PDF 도메인이 기대는 성질이 여기서 처음 행위로 검증된다."""
    from apps.server.domain.video_captions import translate_cli as tc
    captured = _spy_cli_prompt(monkeypatch)
    t = tc.create_translator("claude",
                             prompt_builder=lambda texts: f"CUSTOM {len(texts)}")
    await t.translate_batch(["Hi"])
    assert captured["prompt"] == "CUSTOM 1"


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
