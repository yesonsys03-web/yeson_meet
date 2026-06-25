# === ANCHOR: REPORT_SUMMARY_START ===
"""LLM summary generation for meeting reports via local CLI (S6).

Supported CLIs (in priority order):
  1. claude  — invoked with ``["claude", "-p", prompt]`` (print mode)
  2. codex   — invoked with ``["codex", "exec", prompt]``

The feature is opt-out via the ``YESON_REPORT_SUMMARY`` environment variable.
Set it to ``"0"``, ``"false"``, or ``"off"`` (case-insensitive) to skip
summary generation entirely.  Default (variable absent or any other value) is ON.

All failures are logged as warnings and return ``None`` — they must never
propagate to the caller or prevent the report from being written.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.server.db.models import Session, Utterance

logger = logging.getLogger(__name__)

# Maximum number of characters of transcript text to include in the prompt.
# Keeps the prompt from growing unboundedly for very long meetings.
_MAX_TRANSCRIPT_CHARS = 8000


# === ANCHOR: REPORT_SUMMARY_FIND_CLI_START ===
# Registry of supported summary backends: name -> (executable, build_prefix(model)).
# argv is always ``[*prefix, prompt]`` (the prompt is appended by
# ``generate_summary``), so claude/codex ignore the model. To add a backend
# (e.g. antigravity, or opencode with a deepseek model), register it here with
# the model embedded in the prefix — e.g. ``["opencode", "run", "-m", model]`` —
# and it becomes selectable with no other code change.
_BACKENDS: dict[str, tuple[str, Callable[[str], list[str]]]] = {
    "claude": ("claude", lambda _model: ["claude", "-p"]),
    "codex": ("codex", lambda _model: ["codex", "exec"]),
}

# Auto-detect priority when no explicit backend is selected.
_AUTO_ORDER: tuple[str, ...] = ("claude", "codex")


def find_summary_cli(preferred: str | None = None, model: str = "") -> tuple[str, list[str]] | None:
    """Resolve the configured summary backend to an available CLI.

    Returns a ``(name, argv_prefix)`` pair ready for use with
    ``subprocess.run([*argv_prefix, prompt], ...)``, or ``None`` if no usable
    backend is found.

    ``preferred`` is the operator's selection:
      * ``None``/``""``/``"auto"`` — first backend whose executable is on
        ``PATH`` (priority: ``claude`` → ``codex``). This is the default.
      * an explicit backend name — that backend, only if it is registered AND
        on ``PATH`` (no silent fallback: an explicit pick that is unavailable is
        a configuration error, returns ``None``).
    """
    pref = (preferred or "auto").strip().lower()
    if pref in ("", "auto"):
        for name in _AUTO_ORDER:
            exe, build_prefix = _BACKENDS[name]
            if shutil.which(exe):
                return (name, build_prefix(model))
        return None
    entry = _BACKENDS.get(pref)
    if entry is None:
        logger.warning("find_summary_cli: unknown summary backend %r — skipping", pref)
        return None
    exe, build_prefix = entry
    if not shutil.which(exe):
        logger.warning("find_summary_cli: backend '%s' not found on PATH — skipping", exe)
        return None
    return (pref, build_prefix(model))
# === ANCHOR: REPORT_SUMMARY_FIND_CLI_END ===


# === ANCHOR: REPORT_SUMMARY_GENERATE_START ===
def generate_summary(
    meeting: "Session",
    utterances: list["Utterance"],
    *,
    backend: str | None = None,
    model: str | None = None,
    timeout: int = 120,
) -> str | None:
    """Generate a Korean meeting summary using a local LLM CLI.

    Returns the summary string on success, or ``None`` on any failure
    (CLI absent, disabled, empty utterances, subprocess error, timeout).
    Exceptions are never raised to the caller.

    Parameters
    ----------
    meeting:
        The session object (used for context; not currently included in
        the prompt body, but available for future use).
    utterances:
        Ordered list of utterance records.  Empty list → ``None``.
    timeout:
        Seconds to wait for the subprocess before giving up.
    """
    # --- Toggle check ---
    env_val = os.environ.get("YESON_REPORT_SUMMARY", "").strip().lower()
    if env_val in ("0", "false", "off"):
        logger.debug("generate_summary: disabled via YESON_REPORT_SUMMARY=%s", env_val)
        return None

    # --- Empty utterances guard ---
    if not utterances:
        logger.debug("generate_summary: no utterances — skipping summary")
        return None

    # --- Backend selection (Config-panel selection, transported via env) ---
    selected = backend if backend is not None else os.environ.get("YESON_SUMMARY_BACKEND", "auto")
    selected_model = model if model is not None else os.environ.get("YESON_SUMMARY_MODEL", "")

    # --- CLI discovery ---
    cli_info = find_summary_cli(selected, selected_model)
    if cli_info is None:
        logger.warning(
            "generate_summary: no usable summary backend (selected=%r) found on PATH — "
            "skipping summary",
            selected,
        )
        return None

    _cli_name, cli_args = cli_info

    # --- Build transcript snippet ---
    transcript_lines: list[str] = []
    for utt in utterances:
        speaker = utt.speaker or "발화자 미상"
        ko = (utt.text_ko or "").strip()
        en = (utt.text_en or "").strip()
        if ko:
            transcript_lines.append(f"[{speaker}] {ko}")
        elif en:
            transcript_lines.append(f"[{speaker}] {en}")

    transcript = "\n".join(transcript_lines)
    if len(transcript) > _MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:_MAX_TRANSCRIPT_CHARS] + "\n...(이하 생략)"

    # --- Prompt ---
    prompt = (
        "다음 회의 번역 자막을 한국어로 간결히 요약하고, "
        "마지막에 '액션 아이템'을 불릿으로 정리해줘. "
        "군더더기 없이 보고서 상단에 들어갈 형식으로.\n\n"
        f"{transcript}"
    )

    # --- Execute ---
    try:
        result = subprocess.run(
            [*cli_args, prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "generate_summary: CLI '%s' timed out after %ds — skipping summary",
            _cli_name,
            timeout,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_summary: CLI '%s' raised %s — skipping summary",
            _cli_name,
            exc,
        )
        return None

    if result.returncode != 0 or not result.stdout.strip():
        logger.warning(
            "generate_summary: CLI '%s' returned code=%d stdout_empty=%s stderr=%r — skipping",
            _cli_name,
            result.returncode,
            not result.stdout.strip(),
            result.stderr[:200] if result.stderr else "",
        )
        return None

    return result.stdout.strip()
# === ANCHOR: REPORT_SUMMARY_GENERATE_END ===
# === ANCHOR: REPORT_SUMMARY_END ===
