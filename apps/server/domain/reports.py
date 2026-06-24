# === ANCHOR: REPORTS_START ===
"""Markdown report generation for completed meeting sessions."""
from __future__ import annotations

import asyncio
from itertools import groupby
from pathlib import Path

from apps.server.db.models import Session, Utterance


# === ANCHOR: REPORTS_FORMAT_START ===
def _speaker_label(speaker: str | None) -> str:
    """Return display label for a speaker; None → fixed 발화자 미상."""
    return speaker if speaker else "발화자 미상"


def _hms(dt: object) -> str:
    """Format a datetime as HH:MM:SS (date omitted)."""
    return dt.strftime("%H:%M:%S")  # type: ignore[union-attr]
# === ANCHOR: REPORTS_FORMAT_END ===


# === ANCHOR: REPORTS_BUILD_SESSION_REPORT_START ===
def build_session_report(
    meeting: Session,
    utterances: list[Utterance],
    summary: str | None = None,
) -> str:
    """Build a chronological Markdown report for one meeting (S1 layout).

    *summary* is an optional LLM-generated Korean summary (S6).  When provided
    it is inserted as a ``## 요약`` section immediately before ``## Utterances``.
    """
    lines = [
        f"# {meeting.title}",
        "",
        f"- Session ID: `{meeting.external_id}`",
        f"- Status: {meeting.status}",
        f"- Started: {meeting.started_at.isoformat()}",
        f"- Ended: {meeting.ended_at.isoformat() if meeting.ended_at else 'N/A'}",
    ]
    if meeting.client_label:
        lines.append(f"- Client: {meeting.client_label}")

    # --- Summary statistics ---
    if utterances:
        speakers = sorted({_speaker_label(r.speaker) for r in utterances if r.speaker})
        duration_note = (
            f"{_hms(utterances[0].started_at)} – {_hms(utterances[-1].ended_at)}"
        )
        lines.extend(
            [
                f"- 총 발화 수: {len(utterances)}",
                f"- 참여 화자: {', '.join(speakers) if speakers else '없음'}",
                f"- 시간 범위: {duration_note}",
            ]
        )

    # --- LLM summary section (S6) ---
    if summary:
        lines.extend(["", "## 요약", "", summary, ""])

    lines.extend(["", "## Utterances", ""])

    if not utterances:
        lines.append("_No utterances recorded._")
    else:
        # Group consecutive utterances by speaker (None treated as distinct key)
        for speaker_key, group in groupby(utterances, key=lambda r: r.speaker):
            group_rows = list(group)
            label = _speaker_label(speaker_key)
            first = group_rows[0]
            lines.extend(
                [
                    f"### {_hms(first.started_at)} {label}",
                    "",
                ]
            )
            for row in group_rows:
                lines.extend(
                    [
                        f"- KO: {row.text_ko}",
                        f"- EN: {row.text_en}",
                        "",
                    ]
                )

    return "\n".join(lines).rstrip() + "\n"
# === ANCHOR: REPORTS_BUILD_SESSION_REPORT_END ===


# === ANCHOR: REPORTS_REPORT_PATH_START ===
def report_path(storage_root: str | Path, session_id: str, fmt: str = "md") -> Path:
    """Return the canonical report path for a session.

    *fmt* selects the file extension (default ``"md"`` for backward compat).
    """
    return Path(storage_root) / session_id / f"report.{fmt}"
# === ANCHOR: REPORTS_REPORT_PATH_END ===


# === ANCHOR: REPORTS_WRITE_SESSION_REPORT_START ===
def write_session_report(
    storage_root: str | Path,
    meeting: Session,
    utterances: list[Utterance],
# === ANCHOR: REPORTS_WRITE_SESSION_REPORT_END ===
) -> Path:
    """Write a completed session report and return its path."""
    path = report_path(storage_root, str(meeting.external_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_session_report(meeting, utterances), encoding="utf-8")
    return path


# === ANCHOR: REPORTS_WRITE_EXPORTS_START ===
def write_session_exports(
    storage_root: str | Path,
    meeting: Session,
    utterances: list[Utterance],
    summary: str | None = None,
) -> "dict[str, Path | None]":
    """Write all export formats for a session and return a dict of format→path.

    Each format is attempted independently (best-effort).  A format that fails
    yields ``None`` in the returned dict; the failure is logged as a warning and
    never propagates to the caller.

    Returns keys: ``"md"``, ``"html"``, ``"docx"``, ``"pdf"``.
    PDF requires LibreOffice (soffice); if unavailable the value is ``None``.

    *summary* is an optional pre-generated Korean summary (S6).  When ``None``
    (default) reports are written without a summary section — no LLM call is
    made here.  Summary generation is the responsibility of the caller (e.g.
    via ``regenerate_report_with_summary`` in the background).
    """
    import logging

    from apps.server.domain.report_docx import build_session_report_docx
    from apps.server.domain.report_html import build_session_report_html
    from apps.server.domain.report_pdf import convert_docx_to_pdf

    logger = logging.getLogger(__name__)
    session_id = str(meeting.external_id)
    base_dir = Path(storage_root) / session_id
    base_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Path | None] = {}

    # md
    try:
        md_path = report_path(storage_root, session_id, "md")
        md_path.write_text(build_session_report(meeting, utterances, summary), encoding="utf-8")
        results["md"] = md_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("write_session_exports: md failed: %s", exc)
        results["md"] = None

    # html
    try:
        html_path = report_path(storage_root, session_id, "html")
        html_path.write_text(build_session_report_html(meeting, utterances, summary=summary), encoding="utf-8")
        results["html"] = html_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("write_session_exports: html failed: %s", exc)
        results["html"] = None

    # docx
    docx_bytes: bytes | None = None
    try:
        docx_path = report_path(storage_root, session_id, "docx")
        docx_bytes = build_session_report_docx(meeting, utterances, summary=summary)
        docx_path.write_bytes(docx_bytes)
        results["docx"] = docx_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("write_session_exports: docx failed: %s", exc)
        results["docx"] = None
        docx_bytes = None

    # pdf (requires soffice; skip silently if unavailable)
    try:
        if docx_bytes is not None:
            pdf_bytes = convert_docx_to_pdf(docx_bytes)
            if pdf_bytes is not None:
                pdf_path = report_path(storage_root, session_id, "pdf")
                pdf_path.write_bytes(pdf_bytes)
                results["pdf"] = pdf_path
            else:
                results["pdf"] = None
        else:
            results["pdf"] = None
    except Exception as exc:  # noqa: BLE001
        logger.warning("write_session_exports: pdf failed: %s", exc)
        results["pdf"] = None

    return results
# === ANCHOR: REPORTS_WRITE_EXPORTS_END ===


# === ANCHOR: REPORTS_REGENERATE_WITH_SUMMARY_START ===
async def regenerate_report_with_summary(
    storage_root: str | Path,
    meeting: object,
    utterances: list[object],
) -> str | None:
    """Generate an LLM summary in a thread pool and re-emit all report files.

    This is intended to run as a FastAPI BackgroundTask after ``end_session``
    has already written the initial (summary-less) exports and returned a
    response to the client.

    Steps:
    1. Run blocking ``generate_summary`` in a thread pool (asyncio.to_thread)
       so the event loop is never blocked.
    2. If a summary is produced, call ``write_session_exports`` with
       ``summary=summary`` to overwrite the files with the enriched version.
    3. If summary is None, or any exception occurs, nothing is overwritten.

    All exceptions are caught and logged as warnings — this is best-effort.
    Returns the summary string (or None) for testing convenience.
    """
    import logging

    from apps.server.domain.report_summary import generate_summary

    logger = logging.getLogger(__name__)
    try:
        summary: str | None = await asyncio.to_thread(
            generate_summary, meeting, utterances
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("regenerate_report_with_summary: generate_summary raised %s", exc)
        return None

    if summary:
        try:
            write_session_exports(storage_root, meeting, utterances, summary=summary)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            logger.warning("regenerate_report_with_summary: re-emit failed: %s", exc)

    return summary
# === ANCHOR: REPORTS_REGENERATE_WITH_SUMMARY_END ===
# === ANCHOR: REPORTS_END ===
