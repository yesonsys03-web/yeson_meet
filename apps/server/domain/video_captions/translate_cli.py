"""Subscription-CLI translation providers (claude/codex/agy/opencode).

운영자가 이미 구독 중인 코딩 CLI를 번역 엔진으로 활용해 API 과금을 피한다.
배치 파이프라인이라 CLI 기동 오버헤드(호출당 수 초)는 허용 범위.
서버 머신에 해당 CLI가 설치·로그인되어 있어야 한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .translate import (
    GeminiFlashTranslator,
    TranslationError,
    TranslationProvider,
    build_translation_prompt,
)

logger = logging.getLogger("yeson.video.translate_cli")

PROVIDER_ENV = "YESON_VIDEO_TRANSLATE_PROVIDER"
CLI_MODEL_ENV = "YESON_TRANSLATE_CLI_MODEL"
CUSTOM_CLI_ENV = "YESON_TRANSLATE_CLI"
CLI_TIMEOUT_ENV = "YESON_TRANSLATE_CLI_TIMEOUT"
DEFAULT_CLI_TIMEOUT = 300.0

_PROMPT_PLACEHOLDER = "{prompt}"

# Windows에서 콘솔 창이 번쩍이지 않도록 — CREATE_NO_WINDOW는 Windows 전용 상수라
# os.name == "nt"가 아닌 분기에서는 절대 참조하지 않는다 (mac/Linux AttributeError 방지).
_SUBPROCESS_FLAGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
)

_FALLBACK_BIN_DIRS = (
    Path.home() / ".local" / "bin",
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
    Path.home() / ".bun" / "bin",
    Path.home() / ".npm-global" / "bin",
    Path.home() / "bin",
)


def _candidate_names(name: str, windows: bool) -> list[str]:
    """폴백 디렉터리 직접 탐색용 후보 파일명 — shutil.which는 PATHEXT를 자동 처리하지만
    폴백 경로는 수동으로 확장자 후보를 붙여야 한다."""
    if not windows:
        return [name]
    if name.lower().endswith((".exe", ".cmd", ".bat")):
        return [name]
    return [name, f"{name}.exe", f"{name}.cmd", f"{name}.bat"]


def _windows_fallback_dirs() -> tuple[Path, ...]:
    dirs: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(Path(appdata) / "npm")  # npm 글로벌 .cmd 심
    dirs.append(Path.home() / "scoop" / "shims")
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        dirs.append(Path(localappdata) / "Programs")
    return tuple(dirs)


def _fallback_dirs() -> tuple[Path, ...]:
    return _windows_fallback_dirs() if os.name == "nt" else _FALLBACK_BIN_DIRS


def resolve_cli(name: str) -> str | None:
    """PATH 우선, 실패 시 표준 설치 경로 폴백 (GUI 앱은 PATH가 최소인 경우 대비)."""
    found = shutil.which(name)
    if found:
        return found
    windows = os.name == "nt"
    for directory in _fallback_dirs():
        for candidate_name in _candidate_names(name, windows):
            candidate = directory / candidate_name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def list_translate_engines() -> list[dict]:
    """클라 드롭다운용 — 서버에서 사용 가능한 번역 엔진과 설치 여부."""
    return [
        {"value": "gemini", "label": "Gemini (기본)",
         "available": bool(os.environ.get("GEMINI_API_KEY"))},
        {"value": "claude", "label": "Claude 구독",
         "available": resolve_cli("claude") is not None},
        {"value": "codex", "label": "Codex 구독",
         "available": resolve_cli("codex") is not None},
        {"value": "agy", "label": "Antigravity",
         "available": resolve_cli("agy") is not None},
        {"value": "opencode", "label": "OpenCode (딥시크 등)",
         "available": resolve_cli("opencode") is not None},
    ]


@dataclass(frozen=True)
class _Backend:
    argv: list[str]
    prompt_via: str = "stdin"  # "stdin" | "argv"
    model_flag: str | None = None


_BACKENDS: dict[str, _Backend] = {
    "claude": _Backend(argv=["claude", "-p"], prompt_via="stdin", model_flag="--model"),
    "codex": _Backend(argv=["codex", "exec"], prompt_via="stdin", model_flag="-m"),
    "agy": _Backend(argv=["agy", "-p"], prompt_via="argv", model_flag="--model"),
    "opencode": _Backend(argv=["opencode", "run"], prompt_via="argv", model_flag="-m"),
}


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        # drop the opening fence line (```json or ```)
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1:]
        else:
            stripped = stripped[3:]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -3]
    return stripped.strip()


def _extract_json_array(stdout: str, expected_len: int) -> list[str] | None:
    text = _strip_fences(stdout)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    candidate = text[start:end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list) or len(parsed) != expected_len:
        return None
    return [str(t) for t in parsed]


class CliTranslator:
    """TranslationProvider backed by a subscription coding CLI."""

    def __init__(self, argv: list[str], *, prompt_via: str = "stdin", timeout: float = DEFAULT_CLI_TIMEOUT):
        self._argv = list(argv)
        self._prompt_via = prompt_via
        self._timeout = timeout
        self._checked_bin = False

    def _ensure_binary(self) -> None:
        if self._checked_bin:
            return
        exe = self._argv[0]
        resolved = resolve_cli(exe)
        if resolved is None:
            raise TranslationError(
                f"'{exe}' CLI를 찾을 수 없습니다. 서버 머신에 설치/로그인하고 PATH를 확인하세요 "
                "(GUI로 띄운 서버는 PATH가 좁을 수 있음 — PATH와 표준 설치 경로(~/.local/bin 등)를 "
                "모두 확인했습니다 — YESON_TRANSLATE_CLI로 절대경로 지정 가능)"
            )
        self._argv[0] = resolved
        self._checked_bin = True

    def _run_cli(self, prompt: str) -> str:
        if self._prompt_via == "argv":
            if _PROMPT_PLACEHOLDER in self._argv:
                cmd = [prompt if part == _PROMPT_PLACEHOLDER else part for part in self._argv]
            else:
                cmd = self._argv + [prompt]
            stdin_input = None
        else:
            cmd = self._argv
            stdin_input = prompt

        try:
            result = subprocess.run(
                cmd,
                input=stdin_input,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                **_SUBPROCESS_FLAGS,
            )
        except subprocess.TimeoutExpired as exc:
            raise TranslationError(f"CLI 번역 시간 초과({self._timeout}s)") from exc

        if result.returncode != 0:
            stderr_tail = (result.stderr or "")[-300:]
            raise TranslationError(
                f"CLI 번역 실패 (returncode={result.returncode}): {stderr_tail}"
            )
        return result.stdout or ""

    async def translate_batch(self, texts: list[str]) -> list[str]:
        self._ensure_binary()
        prompt = build_translation_prompt(texts)

        stdout = await asyncio.to_thread(self._run_cli, prompt)
        out = _extract_json_array(stdout, len(texts))
        if out is not None:
            return out

        # CLI 비결정성 대응: 1회 재시도
        logger.warning("CliTranslator: first attempt produced unparseable output — retrying once")
        stdout = await asyncio.to_thread(self._run_cli, prompt)
        out = _extract_json_array(stdout, len(texts))
        if out is not None:
            return out

        raise TranslationError(
            f"CLI 번역 출력 파싱 실패 (재시도 후에도 실패): {stdout[:200]!r}"
        )


def _timeout_from_env() -> float:
    raw = os.environ.get(CLI_TIMEOUT_ENV)
    if not raw:
        return DEFAULT_CLI_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_CLI_TIMEOUT


def create_translator(
    provider: str | None = None, cli_model: str | None = None,
) -> TranslationProvider:
    """Select a TranslationProvider.

    ``provider``/``cli_model`` (e.g. a per-job override) take priority over the
    ``YESON_VIDEO_TRANSLATE_PROVIDER``/``YESON_TRANSLATE_CLI_MODEL`` env vars,
    which remain the fallback when the argument is absent or blank — existing
    call sites that omit the arguments keep their current env-driven behavior.
    """
    provider = (provider or "").strip().lower() or os.environ.get(PROVIDER_ENV, "gemini").strip().lower()
    timeout = _timeout_from_env()

    if provider in ("", "gemini"):
        return GeminiFlashTranslator()

    if provider in _BACKENDS:
        backend = _BACKENDS[provider]
        argv = list(backend.argv)
        model = (cli_model or "").strip() or os.environ.get(CLI_MODEL_ENV)
        if model and backend.model_flag:
            argv = argv + [backend.model_flag, model]
        return CliTranslator(argv, prompt_via=backend.prompt_via, timeout=timeout)

    if provider == "custom":
        custom = os.environ.get(CUSTOM_CLI_ENV)
        if not custom:
            raise TranslationError(
                f"{PROVIDER_ENV}=custom 이지만 {CUSTOM_CLI_ENV} 환경변수가 설정되지 않았습니다."
            )
        parts = shlex.split(custom)
        if _PROMPT_PLACEHOLDER in parts:
            return CliTranslator(parts, prompt_via="argv", timeout=timeout)
        return CliTranslator(parts, prompt_via="stdin", timeout=timeout)

    raise TranslationError(f"알 수 없는 번역 provider '{provider}' ({PROVIDER_ENV})")
