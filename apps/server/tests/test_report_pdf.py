"""Tests for report_pdf: engine discovery + dispatch (Word-first, soffice fallback).

Run without conftest:
    uv run pytest apps/server/tests/test_report_pdf.py -v --noconftest
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import apps.server.domain.report_pdf as report_pdf
from apps.server.domain.report_pdf import (
    convert_docx_to_pdf,
    find_pdf_engine,
    find_soffice,
    find_word,
)


@pytest.fixture(autouse=True)
def _clear_engine_env(monkeypatch):
    """Default every test to engine='auto' unless it sets YESON_PDF_ENGINE itself."""
    monkeypatch.delenv("YESON_PDF_ENGINE", raising=False)


# ---------------------------------------------------------------------------
# Minimal docx bytes fixture (re-uses report_docx to produce real bytes)
# ---------------------------------------------------------------------------

def _make_docx_bytes() -> bytes:
    from datetime import datetime, timezone
    from apps.server.domain.report_docx import build_session_report_docx

    meeting = SimpleNamespace(
        title="PDF Test Meeting",
        external_id="test-pdf-ext-id",
        status="ended",
        started_at=datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc),
        ended_at=datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc),
        client_label=None,
    )
    utterances = [
        SimpleNamespace(
            speaker="Alice",
            text_ko="안녕하세요",
            text_en="Hello",
            started_at=datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc),
            ended_at=datetime(2024, 1, 1, 9, 1, tzinfo=timezone.utc),
            seq=0,
        )
    ]
    return build_session_report_docx(meeting, utterances)


# ---------------------------------------------------------------------------
# (i) Dispatch gate: no engine available → None, no exception
# ---------------------------------------------------------------------------

def test_no_engine_returns_none(monkeypatch):
    """When find_pdf_engine returns no engines, convert returns None (no raise)."""
    monkeypatch.setattr(report_pdf, "find_pdf_engine", lambda: [])
    assert convert_docx_to_pdf(b"dummy docx bytes") is None


def test_engine_env_none_disables_pdf(monkeypatch):
    """YESON_PDF_ENGINE=none → no engines, even with Word/soffice installed."""
    monkeypatch.setenv("YESON_PDF_ENGINE", "none")
    monkeypatch.setattr(report_pdf, "find_word", lambda: "/Word.app")
    monkeypatch.setattr(report_pdf, "find_soffice", lambda: "/usr/bin/soffice")
    assert find_pdf_engine() == []
    assert convert_docx_to_pdf(b"dummy docx bytes") is None


# ---------------------------------------------------------------------------
# (ii) soffice conversion (engine forced to soffice)
# ---------------------------------------------------------------------------

def test_convert_via_soffice_success(monkeypatch):
    """Mocked soffice: subprocess.run writes report.pdf → function returns those bytes."""
    monkeypatch.setenv("YESON_PDF_ENGINE", "soffice")
    monkeypatch.setattr(report_pdf, "find_soffice", lambda: "/usr/bin/fake_soffice")

    dummy_pdf = b"%PDF-1.4 dummy content"

    def _fake_run(cmd, *, capture_output, timeout):
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / "report.pdf").write_bytes(dummy_pdf)
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert convert_docx_to_pdf(_make_docx_bytes()) == dummy_pdf


def test_convert_soffice_nonzero_returncode(monkeypatch):
    """soffice exits non-zero → None."""
    monkeypatch.setenv("YESON_PDF_ENGINE", "soffice")
    monkeypatch.setattr(report_pdf, "find_soffice", lambda: "/usr/bin/fake_soffice")

    def _fake_run(cmd, *, capture_output, timeout):
        result = MagicMock()
        result.returncode = 1
        result.stderr = b"soffice error"
        return result

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert convert_docx_to_pdf(b"dummy docx bytes") is None


def test_convert_soffice_timeout(monkeypatch):
    """subprocess.run raises TimeoutExpired → None (no raise)."""
    monkeypatch.setenv("YESON_PDF_ENGINE", "soffice")
    monkeypatch.setattr(report_pdf, "find_soffice", lambda: "/usr/bin/fake_soffice")

    def _fake_run(cmd, *, capture_output, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert convert_docx_to_pdf(b"dummy docx bytes") is None


def test_convert_soffice_pdf_not_created(monkeypatch):
    """soffice rc=0 but no .pdf appears → None."""
    monkeypatch.setenv("YESON_PDF_ENGINE", "soffice")
    monkeypatch.setattr(report_pdf, "find_soffice", lambda: "/usr/bin/fake_soffice")

    def _fake_run(cmd, *, capture_output, timeout):
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert convert_docx_to_pdf(b"dummy docx bytes") is None


# ---------------------------------------------------------------------------
# (iii) Word-first dispatch + fallback to soffice
# ---------------------------------------------------------------------------

def test_word_preferred_when_both_available(monkeypatch):
    """auto + Word & soffice present → Word is tried first and its bytes win."""
    monkeypatch.setattr(report_pdf, "find_word", lambda: "/Word.app")
    monkeypatch.setattr(report_pdf, "find_soffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(report_pdf, "_convert_via_word", lambda b: b"%PDF-from-word")
    monkeypatch.setattr(
        report_pdf, "_convert_via_soffice",
        lambda b: pytest.fail("soffice must not be reached when Word succeeds"),
    )
    assert convert_docx_to_pdf(b"docx") == b"%PDF-from-word"


def test_fallback_to_soffice_when_word_fails(monkeypatch):
    """Word available but returns None → dispatch falls back to soffice."""
    monkeypatch.setattr(report_pdf, "find_word", lambda: "/Word.app")
    monkeypatch.setattr(report_pdf, "find_soffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(report_pdf, "_convert_via_word", lambda b: None)
    monkeypatch.setattr(report_pdf, "_convert_via_soffice", lambda b: b"%PDF-from-soffice")
    assert convert_docx_to_pdf(b"docx") == b"%PDF-from-soffice"


# ---------------------------------------------------------------------------
# (iv) find_pdf_engine ordering + env override
# ---------------------------------------------------------------------------

def test_engine_auto_orders_word_then_soffice(monkeypatch):
    monkeypatch.setattr(report_pdf, "find_word", lambda: "/Word.app")
    monkeypatch.setattr(report_pdf, "find_soffice", lambda: "/usr/bin/soffice")
    assert find_pdf_engine() == ["word", "soffice"]


def test_engine_word_only_override(monkeypatch):
    monkeypatch.setenv("YESON_PDF_ENGINE", "word")
    monkeypatch.setattr(report_pdf, "find_word", lambda: "/Word.app")
    monkeypatch.setattr(report_pdf, "find_soffice", lambda: "/usr/bin/soffice")
    assert find_pdf_engine() == ["word"]


def test_engine_soffice_only_override(monkeypatch):
    monkeypatch.setenv("YESON_PDF_ENGINE", "soffice")
    monkeypatch.setattr(report_pdf, "find_word", lambda: "/Word.app")
    monkeypatch.setattr(report_pdf, "find_soffice", lambda: "/usr/bin/soffice")
    assert find_pdf_engine() == ["soffice"]


def test_engine_unknown_value_falls_back_to_auto(monkeypatch):
    monkeypatch.setenv("YESON_PDF_ENGINE", "banana")
    monkeypatch.setattr(report_pdf, "find_word", lambda: "/Word.app")
    monkeypatch.setattr(report_pdf, "find_soffice", lambda: None)
    assert find_pdf_engine() == ["word"]


def test_engine_empty_when_nothing_installed(monkeypatch):
    monkeypatch.setattr(report_pdf, "find_word", lambda: None)
    monkeypatch.setattr(report_pdf, "find_soffice", lambda: None)
    assert find_pdf_engine() == []


# ---------------------------------------------------------------------------
# (v) find_word platform behavior
# ---------------------------------------------------------------------------

def test_find_word_mac_present(monkeypatch, tmp_path):
    word_app = tmp_path / "Microsoft Word.app"
    word_app.mkdir()
    monkeypatch.setattr(report_pdf.sys, "platform", "darwin")
    monkeypatch.setattr(report_pdf, "_WORD_MAC_APP", str(word_app))
    assert find_word() == str(word_app)


def test_find_word_mac_absent(monkeypatch):
    monkeypatch.setattr(report_pdf.sys, "platform", "darwin")
    monkeypatch.setattr(report_pdf, "_WORD_MAC_APP", "/no/such/Word.app")
    assert find_word() is None


def test_find_word_linux_none(monkeypatch):
    monkeypatch.setattr(report_pdf.sys, "platform", "linux")
    assert find_word() is None


# ---------------------------------------------------------------------------
# (vi) Word(mac) AppleScript path (osascript mocked)
# ---------------------------------------------------------------------------

def test_word_mac_success_mock(monkeypatch):
    """Mocked osascript writes the PDF named in the script → returns its bytes."""
    monkeypatch.setattr(report_pdf.sys, "platform", "darwin")

    def _fake_osascript(cmd, *, capture_output, timeout):
        import re
        script = cmd[-1]
        pdf = next(p for p in re.findall(r'"([^"]+)"', script) if p.endswith(".pdf"))
        Path(pdf).write_bytes(b"%PDF-word-mac")
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr(subprocess, "run", _fake_osascript)
    assert report_pdf._convert_via_word_mac(b"docx") == b"%PDF-word-mac"


def test_word_mac_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(report_pdf.sys, "platform", "darwin")

    def _fake_osascript(cmd, *, capture_output, timeout):
        result = MagicMock()
        result.returncode = 1
        result.stderr = b"Word error"
        return result

    monkeypatch.setattr(subprocess, "run", _fake_osascript)
    assert report_pdf._convert_via_word_mac(b"docx") is None


def test_word_mac_timeout(monkeypatch):
    monkeypatch.setattr(report_pdf.sys, "platform", "darwin")

    def _fake_osascript(cmd, *, capture_output, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(subprocess, "run", _fake_osascript)
    assert report_pdf._convert_via_word_mac(b"docx") is None


# ---------------------------------------------------------------------------
# (vii) find_soffice PATH / fallback logic
# ---------------------------------------------------------------------------

def test_find_soffice_uses_which(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/soffice")
    assert find_soffice() == "/usr/local/bin/soffice"


def test_find_soffice_fallback_path(monkeypatch, tmp_path):
    import shutil
    fake_soffice = tmp_path / "soffice"
    fake_soffice.write_text("#!/bin/sh\n")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(report_pdf, "_FALLBACK_PATHS", [str(fake_soffice)])
    assert find_soffice() == str(fake_soffice)


def test_find_soffice_returns_none_when_nowhere(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(report_pdf, "_FALLBACK_PATHS", ["/no/such/path/soffice"])
    assert find_soffice() is None


# ---------------------------------------------------------------------------
# (viii) Real conversions (local verification only; skipped where unavailable)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (sys.platform == "darwin" and Path(report_pdf._WORD_MAC_APP).is_dir()),
    reason="MS Word not installed on this host",
)
def test_word_mac_real_conversion(monkeypatch):
    """End-to-end: real MS Word converts a real docx → a real PDF. Launches Word."""
    monkeypatch.setenv("YESON_PDF_ENGINE", "word")
    result = convert_docx_to_pdf(_make_docx_bytes())
    assert result is not None
    assert result.startswith(b"%PDF")


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed on this host")
def test_soffice_real_conversion(monkeypatch):
    """End-to-end: real LibreOffice converts a real docx → a real PDF."""
    monkeypatch.setenv("YESON_PDF_ENGINE", "soffice")
    result = convert_docx_to_pdf(_make_docx_bytes())
    assert result is not None
    assert result.startswith(b"%PDF")
