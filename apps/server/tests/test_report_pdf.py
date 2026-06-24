"""Tests for report_pdf (S4): find_soffice + convert_docx_to_pdf.

Run without conftest:
    uv run pytest apps/server/tests/test_report_pdf.py -v --noconftest
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.server.domain.report_pdf import convert_docx_to_pdf, find_soffice


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
# (i) soffice absent → convert_docx_to_pdf returns None, no exception
# ---------------------------------------------------------------------------

def test_soffice_absent_returns_none(monkeypatch):
    """When find_soffice returns None, convert_docx_to_pdf must return None (no raise)."""
    monkeypatch.setattr(
        "apps.server.domain.report_pdf.find_soffice",
        lambda: None,
    )
    result = convert_docx_to_pdf(b"dummy docx bytes")
    assert result is None


def test_soffice_absent_natural(monkeypatch):
    """Natural re-run on this machine (no soffice installed): also returns None."""
    # Patch shutil.which to return None AND make all fallback paths non-existent
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    # Path.is_file() for fallback candidates — patch at the module level
    monkeypatch.setattr(
        "apps.server.domain.report_pdf._FALLBACK_PATHS",
        ["/nonexistent/soffice/path/that/does/not/exist"],
    )
    result = find_soffice()
    assert result is None


# ---------------------------------------------------------------------------
# (ii) Success mock: subprocess writes a PDF, convert returns its bytes
# ---------------------------------------------------------------------------

def test_convert_success_mock(monkeypatch, tmp_path):
    """Mocked soffice: subprocess.run writes a dummy .pdf → function returns those bytes."""
    fake_soffice = "/usr/bin/fake_soffice"
    monkeypatch.setattr(
        "apps.server.domain.report_pdf.find_soffice",
        lambda: fake_soffice,
    )

    dummy_pdf_content = b"%PDF-1.4 dummy content"

    def _fake_subprocess_run(cmd, *, capture_output, timeout):
        # Locate the --outdir argument and write report.pdf there.
        outdir_idx = cmd.index("--outdir")
        outdir = Path(cmd[outdir_idx + 1])
        (outdir / "report.pdf").write_bytes(dummy_pdf_content)
        mock_result = MagicMock()
        mock_result.returncode = 0
        return mock_result

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

    docx_bytes = _make_docx_bytes()
    result = convert_docx_to_pdf(docx_bytes)
    assert result == dummy_pdf_content


# ---------------------------------------------------------------------------
# (iii) Conversion failure mock: returncode != 0 → None
# ---------------------------------------------------------------------------

def test_convert_failure_nonzero_returncode(monkeypatch):
    """When soffice exits with non-zero returncode, convert returns None."""
    monkeypatch.setattr(
        "apps.server.domain.report_pdf.find_soffice",
        lambda: "/usr/bin/fake_soffice",
    )

    def _fake_subprocess_run(cmd, *, capture_output, timeout):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"soffice error"
        return mock_result

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

    result = convert_docx_to_pdf(b"dummy docx bytes")
    assert result is None


def test_convert_failure_timeout(monkeypatch):
    """When subprocess.run raises TimeoutExpired, convert returns None (no raise)."""
    monkeypatch.setattr(
        "apps.server.domain.report_pdf.find_soffice",
        lambda: "/usr/bin/fake_soffice",
    )

    def _fake_subprocess_run(cmd, *, capture_output, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

    result = convert_docx_to_pdf(b"dummy docx bytes")
    assert result is None


def test_convert_failure_pdf_not_created(monkeypatch):
    """When soffice rc=0 but no .pdf file appears, convert returns None."""
    monkeypatch.setattr(
        "apps.server.domain.report_pdf.find_soffice",
        lambda: "/usr/bin/fake_soffice",
    )

    def _fake_subprocess_run(cmd, *, capture_output, timeout):
        # Does NOT write any .pdf file.
        mock_result = MagicMock()
        mock_result.returncode = 0
        return mock_result

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

    result = convert_docx_to_pdf(b"dummy docx bytes")
    assert result is None


# ---------------------------------------------------------------------------
# (iv) find_soffice PATH / fallback logic
# ---------------------------------------------------------------------------

def test_find_soffice_uses_which(monkeypatch):
    """find_soffice returns PATH result when shutil.which finds soffice."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/soffice")
    result = find_soffice()
    assert result == "/usr/local/bin/soffice"


def test_find_soffice_fallback_path(monkeypatch, tmp_path):
    """find_soffice falls back to well-known path when PATH misses but file exists."""
    import shutil
    fake_soffice = tmp_path / "soffice"
    fake_soffice.write_text("#!/bin/sh\n")

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        "apps.server.domain.report_pdf._FALLBACK_PATHS",
        [str(fake_soffice)],
    )
    result = find_soffice()
    assert result == str(fake_soffice)


def test_find_soffice_returns_none_when_nowhere(monkeypatch):
    """find_soffice returns None when neither PATH nor fallback paths have soffice."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        "apps.server.domain.report_pdf._FALLBACK_PATHS",
        ["/no/such/path/soffice"],
    )
    result = find_soffice()
    assert result is None
