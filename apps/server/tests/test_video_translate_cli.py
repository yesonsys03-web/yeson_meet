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


def test_create_translator_claude_pins_default_model(monkeypatch):
    """모델 미지정 claude는 opus로 고정 — 비워 두면 헤드리스 CLI가 사용자의
    인터랙티브 /model 기본값을 상속해, 대화 세션의 모델 변경이 번역 단가를
    조용히 바꾼다(2026-08-28 Fable 전환 실사고 직전). env·잡 지정이 우선."""
    monkeypatch.setenv(tc.PROVIDER_ENV, "claude")
    monkeypatch.delenv(tc.CLI_MODEL_ENV, raising=False)
    translator = tc.create_translator()
    assert translator._argv == ["claude", "-p", "--model", "opus"]
    monkeypatch.setenv(tc.CLI_MODEL_ENV, "sonnet")
    assert tc.create_translator()._argv == ["claude", "-p", "--model", "sonnet"]
    # agy는 자기 기본 모델 유지(claude 모델명이 통하지 않는다)
    monkeypatch.setenv(tc.PROVIDER_ENV, "agy")
    monkeypatch.delenv(tc.CLI_MODEL_ENV, raising=False)
    assert "--model" not in tc.create_translator()._argv


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
        "qwen", "qwen_lite", "qwen_hifi"}
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
    from apps.server.domain.video_captions import translate_ollama as to
    monkeypatch.setattr(tm, "qwen_mlx_available", lambda mid: False)
    monkeypatch.setattr(to, "qwen_ollama_available", lambda tag: False)
    engines = tc.list_translate_engines()
    qwen = next(e for e in engines if e["value"] == "qwen")
    assert qwen["available"] is False


# create_translator는 런타임을 자동 선택한다: 실리콘맥 + MLX 모델 설치 시 MLX.
# 아래 3건은 MLX 경로(우선순위)를 결정적으로 검증하려고 qwen_mlx_available을 강제한다.
def test_create_translator_qwen(monkeypatch):
    from apps.server.domain.video_captions import translate_mlx as tm
    from apps.server.domain.video_captions.translate_mlx import QwenMlxTranslator
    monkeypatch.setattr(tm, "qwen_mlx_available", lambda mid: True)
    translator = tc.create_translator(provider="qwen")
    assert isinstance(translator, QwenMlxTranslator)
    assert translator._model_id == "mlx-community/Qwen3.5-9B-4bit"


def test_create_translator_qwen_lite(monkeypatch):
    from apps.server.domain.video_captions import translate_mlx as tm
    from apps.server.domain.video_captions.translate_mlx import QwenMlxTranslator
    monkeypatch.setattr(tm, "qwen_mlx_available", lambda mid: True)
    translator = tc.create_translator(provider="qwen_lite")
    assert isinstance(translator, QwenMlxTranslator)
    assert translator._model_id == "mlx-community/Qwen3.5-4B-4bit"


def test_create_translator_qwen_hifi(monkeypatch):
    from apps.server.domain.video_captions import translate_mlx as tm
    from apps.server.domain.video_captions.translate_mlx import QwenMlxTranslator
    monkeypatch.setattr(tm, "qwen_mlx_available", lambda mid: True)
    translator = tc.create_translator(provider="qwen_hifi")
    assert isinstance(translator, QwenMlxTranslator)
    assert translator._model_id == "mlx-community/Qwen3.5-9B-8bit"


def test_engines_include_remote_tier(monkeypatch):
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_cli as tcli

    extra = tc.TranslateModel("qwen_next", "Qwen 12B (로컬)", "a/b", 10, "x:12b", 10)
    monkeypatch.setattr(tc, "get_catalog", lambda: {**tc.BUILTIN, "qwen_next": extra})
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: False)
    values = [e["value"] for e in tcli.list_translate_engines()]
    assert "qwen_next" in values
    assert values.index("gemini") == 0  # 정적 엔진 순서 유지


def test_engines_reason_none_for_supported_but_uninstalled(monkeypatch):
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_cli as tcli

    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: False)
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_ollama.qwen_ollama_available",
        lambda tag: False)
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_mlx.qwen_mlx_available",
        lambda repo: False)
    qwen = next(e for e in tcli.list_translate_engines() if e["value"] == "qwen")
    assert qwen["available"] is False   # 미설치
    assert qwen["reason"] is None       # 그러나 다운로드하면 쓸 수 있다


def test_engines_reason_set_for_unsupported_runtime(monkeypatch):
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_cli as tcli

    mlx_only = tc.TranslateModel("qwen_x", "MLX 전용", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: False)  # 윈도·인텔맥
    entry = next(e for e in tcli.list_translate_engines() if e["value"] == "qwen_x")
    assert entry["reason"] == "실리콘맥 전용"
    assert entry["available"] is False


def test_engines_reason_set_for_ollama_only_tier_on_silicon(monkeypatch):
    # list_translate_engines의 available 삼항식은 reason이 있으면
    # _qwen_available을 아예 호출하지 않는다 — 실리콘맥에서 Ollama 전용 티어를
    # 만나도 mlx_repo=None 가드(_qwen_available 내부)까지 도달하지 않는다.
    # 이 테스트는 크래시 회피가 아니라 reason이 정확히 채워지는지만 검증한다.
    # 그 가드가 실제로 방어하는 크래시 경로는 create_translator에 있고,
    # 그건 test_create_translator_no_crash_for_ollama_only_tier_on_silicon이 커버한다.
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_cli as tcli

    ollama_only = tc.TranslateModel("qwen_y", "Ollama 전용", None, 0, "y:1b", 10)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_y": ollama_only})
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: True)
    entry = next(e for e in tcli.list_translate_engines() if e["value"] == "qwen_y")
    assert entry["reason"] == "Ollama 전용"


def test_create_translator_routes_remote_tier_to_ollama(monkeypatch):
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_cli as tcli
    from apps.server.domain.video_captions.translate_ollama import OllamaTranslator

    extra = tc.TranslateModel("qwen_next", "Qwen 12B", "a/b", 10, "x:12b", 10)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_next": extra})
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_mlx.qwen_mlx_available",
        lambda repo: False)
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_ollama.qwen_ollama_available",
        lambda tag: True)
    t = tcli.create_translator("qwen_next")
    assert isinstance(t, OllamaTranslator)


def test_create_translator_no_crash_for_ollama_only_tier_on_silicon(monkeypatch):
    # create_translator에는 list_translate_engines와 달리 reason 게이트가 없다 —
    # 실리콘맥에서 mlx_repo=None인 티어를 고르면 가드가 없을 때 qwen_mlx_available(None)
    # → mlx_model_installed(None)이 None.replace()로 터진다. 이 크래시는 위의
    # engines 테스트가 잡지 못한다(reason이 _qwen_available 호출을 단락시키므로).
    from apps.server.domain.video_captions import translate_catalog as tcat
    from apps.server.domain.video_captions import translate_cli as tcli
    from apps.server.domain.video_captions import translate_mlx as tm
    from apps.server.domain.video_captions.translate_ollama import OllamaTranslator

    ollama_only = tcat.TranslateModel("qwen_y", "Ollama 전용", None, 0, "y:1b", 10)
    monkeypatch.setattr(tcat, "get_catalog", lambda: {"qwen_y": ollama_only})
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: True)  # 실리콘맥
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_ollama.qwen_ollama_available",
        lambda tag: True)
    t = tcli.create_translator("qwen_y")
    assert isinstance(t, OllamaTranslator)


async def test_create_translator_claude_runs_with_lean_flags(monkeypatch):
    """claude 번역 세션은 린 플래그로 뜬다 — 기본 시스템 프롬프트(코딩 에이전트)
    +도구 스키마+플러그인·CLAUDE.md 주입이 청크당 31K 토큰·사고 9.6K를 먹고
    있었다(실측 2026-09-03: 컨텍스트 36,419→5,303·$0.52→$0.10). `_argv`에는
    안 넣는다(모델 고정 단정·로그 그대로) — 실행 시에만 붙는다."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeCompletedProcess(stdout='["가"]')

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tc.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    monkeypatch.setenv(tc.PROVIDER_ENV, "claude")
    monkeypatch.delenv(tc.CLI_MODEL_ENV, raising=False)
    translator = tc.create_translator()
    assert translator._argv == ["claude", "-p", "--model", "opus"]
    await translator.translate_batch(["hi"])
    cmd = calls[0]
    assert cmd[:4] == ["/usr/bin/claude", "-p", "--model", "opus"]
    for flag in ("--tools", "--setting-sources", "--no-session-persistence",
                 "--system-prompt"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    # 다른 CLI에는 claude 전용 플래그를 넘기지 않는다(인자 오류로 즉사)
    monkeypatch.setenv(tc.PROVIDER_ENV, "codex")
    calls.clear()
    await tc.create_translator().translate_batch(["hi"])
    assert "--setting-sources" not in calls[0]
    # 환경변수 0이면 claude도 옛 세션 그대로(비교·회귀 확인용 스위치)
    monkeypatch.setenv(tc.PROVIDER_ENV, "claude")
    monkeypatch.setenv(tc.CLI_LEAN_ENV, "0")
    calls.clear()
    await tc.create_translator().translate_batch(["hi"])
    assert "--setting-sources" not in calls[0]


async def test_lean_flags_dropped_after_not_logged_in(monkeypatch):
    """`--setting-sources ""`가 설정 기반 인증(apiKeyHelper)까지 끊는 환경이면
    미로그인 응답이 온다 — 그 인스턴스는 설정을 적재하는 모드로 재시도한다."""
    responses = [FakeCompletedProcess(stdout="Not logged in · Please run /login",
                                      returncode=1),
                 FakeCompletedProcess(stdout='["가"]')]
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return responses.pop(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tc.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    monkeypatch.setenv(tc.PROVIDER_ENV, "claude")
    translator = tc.create_translator()
    assert await translator.translate_batch(["hi"]) == ["가"]
    assert len(calls) == 2
    assert "--setting-sources" in calls[0]
    assert "--setting-sources" not in calls[1]
    assert translator._lean_args == ()
