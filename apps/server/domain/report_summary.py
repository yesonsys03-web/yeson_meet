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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.server.db.models import Session, Utterance

logger = logging.getLogger(__name__)

# Maximum number of characters of transcript text to include in the prompt.
# Keeps the prompt from growing unboundedly for very long meetings.
_MAX_TRANSCRIPT_CHARS = 8000


# === ANCHOR: REPORT_SUMMARY_FIND_CLI_START ===
def find_summary_cli() -> tuple[str, list[str]] | None:
    """Locate the first available local LLM CLI.

    Returns a ``(name, argv_prefix)`` pair ready for use with
    ``subprocess.run([*argv_prefix, prompt], ...)``, or ``None`` if no
    supported CLI is found on ``PATH``.

    Priority: ``claude`` → ``codex``.
    """
    if shutil.which("claude"):
        return ("claude", ["claude", "-p"])
    if shutil.which("codex"):
        return ("codex", ["codex", "exec"])
    return None
# === ANCHOR: REPORT_SUMMARY_FIND_CLI_END ===


# === ANCHOR: REPORT_SUMMARY_GENERATE_START ===
def generate_summary(
    meeting: "Session",
    utterances: list["Utterance"],
    *,
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

    # --- CLI discovery ---
    cli_info = find_summary_cli()
    if cli_info is None:
        logger.warning(
            "generate_summary: no supported LLM CLI (claude/codex) found on PATH — "
            "skipping summary"
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
