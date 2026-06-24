# === ANCHOR: TEST_REPORT_EXPORTS_START ===
"""Tests for report_path(fmt=...) and write_session_exports() (S5 Part B)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.server.domain.reports import report_path, write_session_exports


@pytest.fixture(autouse=True)
def _disable_real_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard against accidental real LLM CLI calls.

    ``write_session_exports`` no longer calls ``generate_summary`` internally,
    so this fixture is now effectively a no-op for most tests. It is retained
    for safety: if ``generate_summary`` is somehow invoked (e.g. via
    ``regenerate_report_with_summary`` in an integration path), the toggle
    prevents a real subprocess call.
    """
    monkeypatch.setenv("YESON_REPORT_SUMMARY", "0")


# ---------------------------------------------------------------------------
# Helpers — lightweight stubs (no DB required)
# ---------------------------------------------------------------------------

def _make_meeting(
    title: str = "Test Meeting",
    external_id: str = "test-session-001",
    status: str = "ended",
    client_label: str | None = None,
) -> SimpleNamespace:
    base = datetime(2026, 6, 24, 9, 0, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        title=title,
        external_id=external_id,
        status=status,
        started_at=base,
        ended_at=datetime(2026, 6, 24, 9, 30, 0, tzinfo=timezone.utc),
        client_label=client_label,
    )


def _make_utterance(
    seq: int = 1,
    speaker: str | None = "Alice",
    text_en: str = "Hello world.",
    text_ko: str = "안녕하세요.",
) -> SimpleNamespace:
    base = datetime(2026, 6, 24, 9, 5, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        seq=seq,
        speaker=speaker,
        text_en=text_en,
        text_ko=text_ko,
        started_at=base,
        ended_at=base,
    )


# ---------------------------------------------------------------------------
# report_path: fmt parameter
# ---------------------------------------------------------------------------

def test_report_path_default_is_md() -> None:
    p = report_path("/storage", "abc-123")
    assert p == Path("/storage/abc-123/report.md")


def test_report_path_html() -> None:
    p = report_path("/storage", "abc-123", fmt="html")
    assert p == Path("/storage/abc-123/report.html")


def test_report_path_docx() -> None:
    p = report_path("/storage", "abc-123", fmt="docx")
    assert p == Path("/storage/abc-123/report.docx")


def test_report_path_pdf() -> None:
    p = report_path("/storage", "abc-123", fmt="pdf")
    assert p == Path("/storage/abc-123/report.pdf")


def test_report_path_pathlib_storage_root(tmp_path: Path) -> None:
    p = report_path(tmp_path, "session-x", fmt="md")
    assert p == tmp_path / "session-x" / "report.md"


# ---------------------------------------------------------------------------
# write_session_exports: normal — md, html, docx written; pdf skipped (no soffice)
# ---------------------------------------------------------------------------

def test_write_session_exports_creates_md_html_docx(tmp_path: Path) -> None:
    meeting = _make_meeting()
    utterances = [_make_utterance()]

    # Stub convert_docx_to_pdf → None (simulates no soffice)
    with patch("apps.server.domain.report_pdf.find_soffice", return_value=None):
        result = write_session_exports(tmp_path, meeting, utterances)

    assert result["md"] is not None
    assert result["html"] is not None
    assert result["docx"] is not None
    assert result["pdf"] is None  # soffice absent → None, not an error

    assert result["md"].exists()
    assert result["html"].exists()
    assert result["docx"].exists()

    # Sanity-check file contents
    md_content = result["md"].read_text(encoding="utf-8")
    assert "Test Meeting" in md_content
    html_content = result["html"].read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html_content


# ---------------------------------------------------------------------------
# write_session_exports: one format fails → others still succeed, no exception
# ---------------------------------------------------------------------------

def test_write_session_exports_partial_failure_does_not_propagate(tmp_path: Path) -> None:
    meeting = _make_meeting()
    utterances = [_make_utterance()]

    def _boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("forced html failure")

    with (
        patch("apps.server.domain.report_html.build_session_report_html", side_effect=_boom),
        patch("apps.server.domain.report_pdf.find_soffice", return_value=None),
    ):
        result = write_session_exports(tmp_path, meeting, utterances)

    # html failed → None, but no exception raised
    assert result["html"] is None
    # md and docx still produced
    assert result["md"] is not None and result["md"].exists()
    assert result["docx"] is not None and result["docx"].exists()


# ---------------------------------------------------------------------------
# write_session_exports: failure is logged (warning level)
# ---------------------------------------------------------------------------

def test_write_session_exports_logs_warning_on_failure(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    meeting = _make_meeting()
    utterances = [_make_utterance()]

    def _boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("forced docx failure")

    with (
        caplog.at_level(logging.WARNING, logger="apps.server.domain.reports"),
        patch("apps.server.domain.report_docx.build_session_report_docx", side_effect=_boom),
        patch("apps.server.domain.report_pdf.find_soffice", return_value=None),
    ):
        result = write_session_exports(tmp_path, meeting, utterances)

    assert result["docx"] is None
    assert any("docx" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# S6: write_session_exports with summary= parameter → summary injected
# ---------------------------------------------------------------------------

def test_write_session_exports_injects_summary_into_md_html_docx(tmp_path: "Path") -> None:
    """summary passed directly → md/html/docx all contain it."""
    meeting = _make_meeting()
    utterances = [_make_utterance()]

    with patch("apps.server.domain.report_pdf.find_soffice", return_value=None):
        result = write_session_exports(tmp_path, meeting, utterances, summary="고정 요약 텍스트")

    assert result["md"] is not None and result["md"].exists()
    assert result["html"] is not None and result["html"].exists()
    assert result["docx"] is not None and result["docx"].exists()

    md_content = result["md"].read_text(encoding="utf-8")
    assert "고정 요약 텍스트" in md_content
    assert "## 요약" in md_content

    html_content = result["html"].read_text(encoding="utf-8")
    assert "고정 요약 텍스트" in html_content

    import io
    from docx import Document
    docx_content = Document(io.BytesIO(result["docx"].read_bytes()))
    all_text = "\n".join(p.text for p in docx_content.paragraphs)
    assert "고정 요약 텍스트" in all_text


def test_write_session_exports_no_summary_still_produces_reports(tmp_path: "Path") -> None:
    """summary=None (default) → reports written without summary section."""
    meeting = _make_meeting()
    utterances = [_make_utterance()]

    with patch("apps.server.domain.report_pdf.find_soffice", return_value=None):
        result = write_session_exports(tmp_path, meeting, utterances, summary=None)

    assert result["md"] is not None and result["md"].exists()
    assert result["html"] is not None and result["html"].exists()
    assert result["docx"] is not None and result["docx"].exists()

    md_content = result["md"].read_text(encoding="utf-8")
    # No summary section when summary=None
    assert "## 요약" not in md_content
    assert "Test Meeting" in md_content
# === ANCHOR: TEST_REPORT_EXPORTS_END ===
