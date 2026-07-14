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
    # argv[0]은 resolve_cli가 해석한 절대경로로 치환된다
    assert cmd == ["/usr/bin/claude", "-p"]
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
    assert cmd[:4] == ["/usr/bin/opencode", "run", "-m", "deepseek/deepseek-chat"]
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
    # 실제 개발 머신에 claude/codex 등이 표준 경로에 설치돼 있을 수 있으므로
    # 폴백 디렉터리를 비워 결정적으로 "못 찾음"을 재현한다.
    monkeypatch.setattr(tc, "_FALLBACK_BIN_DIRS", ())
    translator = tc.CliTranslator(["claude", "-p"])
    with pytest.raises(tl.TranslationError, match="claude"):
        await translator.translate_batch(["hi"])


# ---- resolve_cli / 경로 폴백 ----

def test_resolve_cli_uses_which_first(monkeypatch):
    monkeypatch.setattr(tc.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    assert tc.resolve_cli("claude") == "/usr/bin/claude"


def test_resolve_cli_falls_back_to_standard_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(tc.shutil, "which", lambda exe: None)
    binfile = tmp_path / "claude"
    binfile.write_text("#!/bin/sh\necho hi\n")
    binfile.chmod(0o755)
    monkeypatch.setattr(tc, "_FALLBACK_BIN_DIRS", (tmp_path,))
    assert tc.resolve_cli("claude") == str(binfile)


def test_resolve_cli_none_when_not_found_anywhere(monkeypatch, tmp_path):
    monkeypatch.setattr(tc.shutil, "which", lambda exe: None)
    monkeypatch.setattr(tc, "_FALLBACK_BIN_DIRS", (tmp_path,))
    assert tc.resolve_cli("nonexistent-cli") is None


def test_resolve_cli_skips_non_executable_fallback_file(monkeypatch, tmp_path):
    monkeypatch.setattr(tc.shutil, "which", lambda exe: None)
    binfile = tmp_path / "claude"
    binfile.write_text("not executable")
    monkeypatch.setattr(tc, "_FALLBACK_BIN_DIRS", (tmp_path,))
    assert tc.resolve_cli("claude") is None


def test_resolve_cli_windows_tries_extension_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(tc.shutil, "which", lambda exe: None)
    monkeypatch.setattr(tc.os, "name", "nt")
    monkeypatch.setattr(tc, "_windows_fallback_dirs", lambda: (tmp_path,))
    binfile = tmp_path / "claude.cmd"
    binfile.write_text("@echo off\n")
    binfile.chmod(0o755)
    assert tc.resolve_cli("claude") == str(binfile)


# ---- _candidate_names ----

def test_candidate_names_non_windows_returns_bare_name():
    assert tc._candidate_names("claude", windows=False) == ["claude"]


def test_candidate_names_windows_adds_extensions():
    assert tc._candidate_names("claude", windows=True) == [
        "claude", "claude.exe", "claude.cmd", "claude.bat"]


def test_candidate_names_windows_keeps_existing_extension():
    assert tc._candidate_names("claude.exe", windows=True) == ["claude.exe"]


# ---- _fallback_dirs / _windows_fallback_dirs ----

def test_fallback_dirs_smoke_current_platform():
    dirs = tc._fallback_dirs()
    assert isinstance(dirs, tuple)
    assert all(isinstance(d, tc.Path) for d in dirs)


def test_windows_fallback_dirs_skips_empty_env(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    dirs = tc._windows_fallback_dirs()
    # scoop/shims is always included (home-relative); APPDATA/LOCALAPPDATA dirs
    # are skipped when the env var is unset/blank
    assert any(d.name == "shims" for d in dirs)
    assert not any("npm" == d.name for d in dirs)
    assert not any("Programs" == d.name for d in dirs)


def test_windows_fallback_dirs_includes_appdata_when_set(monkeypatch):
    monkeypatch.setenv("APPDATA", "/tmp/appdata")
    monkeypatch.setenv("LOCALAPPDATA", "/tmp/localappdata")
    dirs = tc._windows_fallback_dirs()
    assert tc.Path("/tmp/appdata") / "npm" in dirs
    assert tc.Path("/tmp/localappdata") / "Programs" in dirs


# ---- _SUBPROCESS_FLAGS ----

def test_subprocess_flags_empty_on_non_windows():
    if tc.os.name != "nt":
        assert tc._SUBPROCESS_FLAGS == {}


# ---- list_translate_engines ----

def test_list_translate_engines_reports_availability(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(tc, "resolve_cli", lambda name: (
        f"/usr/bin/{name}" if name in ("claude", "opencode") else None))

    engines = tc.list_translate_engines()
    by_value = {e["value"]: e for e in engines}

    assert set(by_value) == {
        "gemini", "claude", "codex", "agy", "opencode", "apple", "apple_hifi",
        "qwen", "qwen_lite"}
    assert by_value["gemini"]["available"] is True
    assert by_value["claude"]["available"] is True
    assert by_value["opencode"]["available"] is True
    assert by_value["codex"]["available"] is False
    assert by_value["agy"]["available"] is False


def test_list_translate_engines_gemini_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(tc, "resolve_cli", lambda name: None)
    engines = tc.list_translate_engines()
    gemini = next(e for e in engines if e["value"] == "gemini")
    assert gemini["available"] is False


def test_list_engines_includes_qwen(monkeypatch):
    from apps.server.domain.video_captions import translate_mlx as tm
    monkeypatch.setattr(tm, "qwen_mlx_available", lambda mid: True)
    engines = tc.list_translate_engines()
    values = {e["value"] for e in engines}
    assert "qwen" in values
    assert "qwen_lite" in values
    qwen = next(e for e in engines if e["value"] == "qwen")
    assert qwen["available"] is True


def test_list_engines_qwen_unavailable(monkeypatch):
    from apps.server.domain.video_captions import translate_mlx as tm
    monkeypatch.setattr(tm, "qwen_mlx_available", lambda mid: False)
    engines = tc.list_translate_engines()
    qwen = next(e for e in engines if e["value"] == "qwen")
    assert qwen["available"] is False


def test_create_translator_qwen():
    from apps.server.domain.video_captions.translate_mlx import QwenMlxTranslator
    translator = tc.create_translator(provider="qwen")
    assert isinstance(translator, QwenMlxTranslator)
    assert translator._model_id == "mlx-community/Qwen3.5-9B-4bit"


def test_create_translator_qwen_lite():
    from apps.server.domain.video_captions.translate_mlx import QwenMlxTranslator
    translator = tc.create_translator(provider="qwen_lite")
    assert isinstance(translator, QwenMlxTranslator)
    assert translator._model_id == "mlx-community/Qwen3.5-4B-4bit"
