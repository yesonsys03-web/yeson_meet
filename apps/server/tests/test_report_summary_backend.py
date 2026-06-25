"""Backend selection for report summary (claude/codex registry + model plumbing).

Run without conftest (no DB required):
    uv run pytest apps/server/tests/test_report_summary_backend.py -v --noconftest
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import apps.server.domain.report_summary as rs
from apps.server.domain.report_summary import find_summary_cli, generate_summary


def _which(*available: str):
    avail = set(available)
    return lambda name: f"/usr/bin/{name}" if name in avail else None


def test_auto_prefers_claude() -> None:
    with patch("apps.server.domain.report_summary.shutil.which", side_effect=_which("claude", "codex")):
        # argv is the resolved absolute path; prompt goes on stdin, not argv.
        assert find_summary_cli("auto") == ("claude", ["/usr/bin/claude", "-p"])


def test_auto_returns_none_when_no_backend() -> None:
    with patch("apps.server.domain.report_summary.shutil.which", side_effect=_which()):
        assert find_summary_cli("auto") is None


def test_explicit_codex_selected_over_claude() -> None:
    with patch("apps.server.domain.report_summary.shutil.which", side_effect=_which("claude", "codex")):
        assert find_summary_cli("codex") == ("codex", ["/usr/bin/codex", "exec"])


def test_explicit_unavailable_returns_none_no_fallback() -> None:
    # codex selected but only claude on PATH → None (no silent fallback to claude)
    with patch("apps.server.domain.report_summary.shutil.which", side_effect=_which("claude")):
        assert find_summary_cli("codex") is None


def test_unknown_backend_returns_none() -> None:
    with patch("apps.server.domain.report_summary.shutil.which", side_effect=_which("claude")):
        assert find_summary_cli("deepseek-xyz") is None


def test_empty_and_none_default_to_auto() -> None:
    with patch("apps.server.domain.report_summary.shutil.which", side_effect=_which("codex")):
        assert find_summary_cli(None) == ("codex", ["/usr/bin/codex", "exec"])
        assert find_summary_cli("") == ("codex", ["/usr/bin/codex", "exec"])


def test_windows_cmd_shim_wrapped_in_cmd_c(monkeypatch: pytest.MonkeyPatch) -> None:
    # npm-global on Windows resolves claude -> ...\claude.cmd, which CreateProcess
    # cannot launch directly, so it must run through cmd.exe.
    monkeypatch.setattr(rs.os, "name", "nt")
    cmd_path = r"C:\Users\u\AppData\Roaming\npm\claude.cmd"
    with patch("apps.server.domain.report_summary.shutil.which", return_value=cmd_path):
        assert find_summary_cli("claude") == ("claude", ["cmd", "/c", cmd_path, "-p"])


def test_windows_exe_not_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rs.os, "name", "nt")
    exe_path = r"C:\Program Files\nodejs\claude.exe"
    with patch("apps.server.domain.report_summary.shutil.which", return_value=exe_path):
        assert find_summary_cli("claude") == ("claude", [exe_path, "-p"])


def test_prompt_delivered_on_stdin_not_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    # The prompt (arbitrary multi-line text) must travel on stdin so Windows .cmd
    # shims need no metacharacter escaping and the cmdline length limit is moot.
    monkeypatch.delenv("YESON_REPORT_SUMMARY", raising=False)
    meeting = SimpleNamespace(title="T", external_id="s1")
    utt = SimpleNamespace(speaker="A", text_ko="안녕하세요 & 100% 진행", text_en="hi")
    fake = SimpleNamespace(returncode=0, stdout="요약\n", stderr="")
    with (
        patch("apps.server.domain.report_summary.find_summary_cli", return_value=("claude", ["claude", "-p"])),
        patch("subprocess.run", return_value=fake) as mock_run,
    ):
        assert generate_summary(meeting, [utt]) == "요약"
    argv = mock_run.call_args.args[0]
    assert argv == ["claude", "-p"]  # prompt is NOT an argv element
    sent = mock_run.call_args.kwargs["input"]
    assert "안녕하세요 & 100% 진행" in sent  # the raw text rides on stdin verbatim
