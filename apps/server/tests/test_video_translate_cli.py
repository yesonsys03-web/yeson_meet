from __future__ import annotations

import subprocess

import pytest

from apps.server.domain.video_captions import translate as tl
from apps.server.domain.video_captions import translate_cli as tc


class FakeCompletedProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_build_translation_prompt_contains_json_and_glossary():
    prompt = tl.build_translation_prompt(["hello", "world"])
    assert '"hello"' in prompt
    assert '"world"' in prompt
    assert "glossary" in prompt.lower() or "용어" in prompt
    assert "JSON array" in prompt


async def test_stdin_backend_claude(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCompletedProcess(stdout='["안녕","잘 가"]')

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tc.shutil, "which", lambda exe: f"/usr/bin/{exe}")

    translator = tc.CliTranslator(["claude", "-p"], prompt_via="stdin")
    out = await translator.translate_batch(["hello", "goodbye"])

    assert out == ["안녕", "잘 가"]
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd == ["claude", "-p"]
    assert kwargs["input"] is not None
    assert "glossary" in kwargs["input"].lower() or "용어" in kwargs["input"]
    assert kwargs["encoding"] == "utf-8"


async def test_argv_backend_opencode_with_model(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCompletedProcess(stdout='["가"]')

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tc.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    monkeypatch.setenv(tc.PROVIDER_ENV, "opencode")
    monkeypatch.setenv(tc.CLI_MODEL_ENV, "deepseek/deepseek-chat")

    translator = tc.create_translator()
    out = await translator.translate_batch(["hi"])

    assert out == ["가"]
    cmd, kwargs = calls[0]
    assert cmd[:4] == ["opencode", "run", "-m", "deepseek/deepseek-chat"]
    assert cmd[-1] != "-m"  # prompt appended as last arg
    assert kwargs.get("input") is None


async def test_markdown_fence_stripped(monkeypatch):
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(stdout='```json\n["가"]\n```\nSome extra prose.')

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tc.shutil, "which", lambda exe: f"/usr/bin/{exe}")

    translator = tc.CliTranslator(["claude", "-p"])
    out = await translator.translate_batch(["hi"])
    assert out == ["가"]


async def test_retry_once_then_succeeds(monkeypatch):
    responses = iter(["not json at all", '["ok"]'])

    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(stdout=next(responses))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tc.shutil, "which", lambda exe: f"/usr/bin/{exe}")

    translator = tc.CliTranslator(["claude", "-p"])
    out = await translator.translate_batch(["hi"])
    assert out == ["ok"]


async def test_retry_both_fail_raises(monkeypatch):
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(stdout="still not json")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tc.shutil, "which", lambda exe: f"/usr/bin/{exe}")

    translator = tc.CliTranslator(["claude", "-p"])
    with pytest.raises(tl.TranslationError):
        await translator.translate_batch(["hi"])


async def test_nonzero_returncode_raises_with_stderr(monkeypatch):
    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess(stdout="", stderr="boom failure detail", returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tc.shutil, "which", lambda exe: f"/usr/bin/{exe}")

    translator = tc.CliTranslator(["claude", "-p"])
    with pytest.raises(tl.TranslationError, match="boom failure detail"):
        await translator.translate_batch(["hi"])


async def test_timeout_raises(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 300))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tc.shutil, "which", lambda exe: f"/usr/bin/{exe}")

    translator = tc.CliTranslator(["claude", "-p"], timeout=1.0)
    with pytest.raises(tl.TranslationError, match="시간 초과"):
        await translator.translate_batch(["hi"])


def test_create_translator_default_is_gemini(monkeypatch):
    monkeypatch.delenv(tc.PROVIDER_ENV, raising=False)
    translator = tc.create_translator()
    assert isinstance(translator, tl.GeminiFlashTranslator)


def test_create_translator_opencode_argv(monkeypatch):
    monkeypatch.setenv(tc.PROVIDER_ENV, "opencode")
    monkeypatch.setenv(tc.CLI_MODEL_ENV, "deepseek/deepseek-chat")
    translator = tc.create_translator()
    assert isinstance(translator, tc.CliTranslator)
    assert translator._argv == ["opencode", "run", "-m", "deepseek/deepseek-chat"]
    assert translator._prompt_via == "argv"


def test_create_translator_custom(monkeypatch):
    monkeypatch.setenv(tc.PROVIDER_ENV, "custom")
    monkeypatch.setenv(tc.CUSTOM_CLI_ENV, "mycli --x {prompt}")
    translator = tc.create_translator()
    assert isinstance(translator, tc.CliTranslator)
    assert translator._argv == ["mycli", "--x", "{prompt}"]
    assert translator._prompt_via == "argv"


def test_create_translator_custom_missing_env_raises(monkeypatch):
    monkeypatch.setenv(tc.PROVIDER_ENV, "custom")
    monkeypatch.delenv(tc.CUSTOM_CLI_ENV, raising=False)
    with pytest.raises(tl.TranslationError):
        tc.create_translator()


def test_create_translator_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv(tc.PROVIDER_ENV, "not-a-real-provider")
    with pytest.raises(tl.TranslationError):
        tc.create_translator()


async def test_missing_binary_raises(monkeypatch):
    monkeypatch.setattr(tc.shutil, "which", lambda exe: None)
    translator = tc.CliTranslator(["claude", "-p"])
    with pytest.raises(tl.TranslationError, match="claude"):
        await translator.translate_batch(["hi"])
