# === ANCHOR: TEST_REPORT_ASYNC_SUMMARY_START ===
"""Tests for regenerate_report_with_summary (async background helper, S6)."""
from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from docx import Document

from apps.server.domain.reports import regenerate_report_with_summary, write_session_exports


# ---------------------------------------------------------------------------
# Helpers — lightweight stubs (no DB required, same shape as test_report_exports)
# ---------------------------------------------------------------------------

def _make_meeting(
    title: str = "Async Test Meeting",
    external_id: str = "async-session-001",
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
# regenerate_report_with_summary: happy path — re-emits files with summary
# ---------------------------------------------------------------------------

async def test_regenerate_overwrites_files_with_summary(tmp_path: Path) -> None:
    """generate_summary returns text → files are re-emitted containing the summary."""
    meeting = _make_meeting()
    utterances = [_make_utterance()]

    # Write initial files without summary (simulates the fast-path in end_session)
    with patch("apps.server.domain.report_pdf.find_soffice", return_value=None):
        write_session_exports(tmp_path, meeting, utterances, summary=None)

    md_before = (tmp_path / "async-session-001" / "report.md").read_text(encoding="utf-8")
    assert "## 요약" not in md_before

    # Regenerate with a mocked summary (no real CLI call)
    with (
        patch(
            "apps.server.domain.report_summary.generate_summary",
            return_value="비동기요약",
        ),
        patch("apps.server.domain.report_pdf.find_soffice", return_value=None),
    ):
        result = await regenerate_report_with_summary(tmp_path, meeting, utterances)

    assert result == "비동기요약"

    md_after = (tmp_path / "async-session-001" / "report.md").read_text(encoding="utf-8")
    assert "비동기요약" in md_after
    assert "## 요약" in md_after

    html_after = (tmp_path / "async-session-001" / "report.html").read_text(encoding="utf-8")
    assert "비동기요약" in html_after

    docx_bytes = (tmp_path / "async-session-001" / "report.docx").read_bytes()
    doc = Document(io.BytesIO(docx_bytes))
    all_text = "\n".join(p.text for p in doc.paragraphs)
    assert "비동기요약" in all_text


# ---------------------------------------------------------------------------
# regenerate_report_with_summary: generate_summary returns None → no re-emit
# ---------------------------------------------------------------------------

async def test_regenerate_does_not_overwrite_when_summary_is_none(tmp_path: Path) -> None:
    """generate_summary returns None → original files unchanged, no exception."""
    meeting = _make_meeting()
    utterances = [_make_utterance()]

    # Write initial files without summary
    with patch("apps.server.domain.report_pdf.find_soffice", return_value=None):
        write_session_exports(tmp_path, meeting, utterances, summary=None)

    md_before = (tmp_path / "async-session-001" / "report.md").read_text(encoding="utf-8")

    with patch(
        "apps.server.domain.report_summary.generate_summary",
        return_value=None,
    ):
        result = await regenerate_report_with_summary(tmp_path, meeting, utterances)

    assert result is None

    # Files must be unchanged — no summary section added
    md_after = (tmp_path / "async-session-001" / "report.md").read_text(encoding="utf-8")
    assert "## 요약" not in md_after
    assert md_after == md_before


# ---------------------------------------------------------------------------
# regenerate_report_with_summary: generate_summary raises → swallowed, returns None
# ---------------------------------------------------------------------------

async def test_regenerate_swallows_generate_summary_exception(tmp_path: Path) -> None:
    """Exception from generate_summary is caught; function returns None without raising."""
    meeting = _make_meeting()
    utterances = [_make_utterance()]

    with patch(
        "apps.server.domain.report_summary.generate_summary",
        side_effect=RuntimeError("CLI exploded"),
    ):
        result = await regenerate_report_with_summary(tmp_path, meeting, utterances)

    assert result is None
# === ANCHOR: TEST_REPORT_ASYNC_SUMMARY_END ===
