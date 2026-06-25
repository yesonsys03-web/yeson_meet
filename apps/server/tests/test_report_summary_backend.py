"""Backend selection for report summary (claude/codex registry + model plumbing).

Run without conftest (no DB required):
    uv run pytest apps/server/tests/test_report_summary_backend.py -v --noconftest
"""
from __future__ import annotations

from unittest.mock import patch

from apps.server.domain.report_summary import find_summary_cli


def _which(*available: str):
    avail = set(available)
    return lambda name: f"/usr/bin/{name}" if name in avail else None


def test_auto_prefers_claude() -> None:
    with patch("apps.server.domain.report_summary.shutil.which", side_effect=_which("claude", "codex")):
        assert find_summary_cli("auto") == ("claude", ["claude", "-p"])


def test_auto_returns_none_when_no_backend() -> None:
    with patch("apps.server.domain.report_summary.shutil.which", side_effect=_which()):
        assert find_summary_cli("auto") is None


def test_explicit_codex_selected_over_claude() -> None:
    with patch("apps.server.domain.report_summary.shutil.which", side_effect=_which("claude", "codex")):
        assert find_summary_cli("codex") == ("codex", ["codex", "exec"])


def test_explicit_unavailable_returns_none_no_fallback() -> None:
    # codex selected but only claude on PATH → None (no silent fallback to claude)
    with patch("apps.server.domain.report_summary.shutil.which", side_effect=_which("claude")):
        assert find_summary_cli("codex") is None


def test_unknown_backend_returns_none() -> None:
    with patch("apps.server.domain.report_summary.shutil.which", side_effect=_which("claude")):
        assert find_summary_cli("deepseek-xyz") is None


def test_empty_and_none_default_to_auto() -> None:
    with patch("apps.server.domain.report_summary.shutil.which", side_effect=_which("codex")):
        assert find_summary_cli(None) == ("codex", ["codex", "exec"])
        assert find_summary_cli("") == ("codex", ["codex", "exec"])
