# === ANCHOR: REPORTS_START ===
"""Markdown report generation for completed meeting sessions."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import groupby
from pathlib import Path

from apps.server.db.models import Session, Utterance


# === ANCHOR: REPORTS_MERGE_START ===
@dataclass
class MergedUtterance:
    """A turn whose continuation rows (empty text_en, same speaker) have been merged."""
    speaker: object
    started_at: object
    ended_at: object
    text_en: str
    text_ko: str


def _collapse_ws(text: str) -> str:
    """Collapse runs of whitespace — including the live-subtitle pacing newlines
    embedded in stored text — into single spaces, then strip.

    Live subtitles insert ``\\n``/``\\n\\n`` between sentences for on-screen
    pacing (invisible on the phone since HTML collapses them). In a report those
    newlines become ``<w:br/>`` line breaks in docx/PDF, so one turn's Korean
    rendered as several lines with blank gaps and the single English line below
    looked empty / pushed away. Reports want flowing prose, so normalize here."""
    return re.sub(r"\s+", " ", text).strip()


def merge_continuation_utterances(rows) -> "list[MergedUtterance]":
    """Merge sentence-split continuation rows (empty text_en, same speaker) into
    the previous row's Korean, so each turn renders as [one English original] +
    [full Korean translation]. Internal pacing newlines are collapsed to spaces."""
    merged: list[MergedUtterance] = []
    for r in rows:
        en = _collapse_ws(r.text_en or "")
        ko = _collapse_ws(r.text_ko or "")
        if merged and not en and merged[-1].speaker == r.speaker:
            prev = merged[-1]
            prev.text_ko = (prev.text_ko + " " + ko).strip() if ko else prev.text_ko
            prev.ended_at = r.ended_at
        else:
            merged.append(
                MergedUtterance(
                    speaker=r.speaker,
                    started_at=r.started_at,
                    ended_at=r.ended_at,
                    text_en=en,
                    text_ko=ko,
                )
            )
    return merged
# === ANCHOR: REPORTS_MERGE_END ===


# === ANCHOR: REPORTS_FORMAT_START ===
def _speaker_label(speaker: str | None) -> str:
    """Return display label for a speaker; None → fixed 발화자 미상."""
    return speaker if speaker else "발화자 미상"


def _to_local(dt: datetime) -> datetime:
    """Convert a stored timestamp to the server's local timezone.

    Stored values are UTC but may be *naive* (no tzinfo) depending on the DB
    driver. Calling ``astimezone()`` on a naive datetime would wrongly assume it
    is already local. So treat naive values as UTC first, then convert.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _hms(dt: datetime) -> str:
    """Format a datetime as HH:MM:SS in the server's local timezone (date omitted)."""
    return _to_local(dt).strftime("%H:%M:%S")
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
        f"- Started: {_to_local(meeting.started_at).isoformat()}",
        f"- Ended: {_to_local(meeting.ended_at).isoformat() if meeting.ended_at else 'N/A'}",
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
        # Group consecutive utterances by speaker (None treated as distinct key).
        # Merge continuation rows (empty text_en, same speaker) first so each
        # turn renders as one English line + full merged Korean.
        for speaker_key, group in groupby(merge_continuation_utterances(utterances), key=lambda r: r.speaker):
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


# === ANCHOR: REPORTS_SUMMARY_PATH_START ===
def summary_path(storage_root: str | Path, session_id: str, fmt: str = "md") -> Path:
    """Return the canonical path for the standalone summary file.

    *fmt* selects the file extension (default ``"md"`` for backward compat),
    yielding sibling files ``summary.md``/``summary.html``/``summary.docx``/``summary.pdf``.
    """
    return Path(storage_root) / session_id / f"summary.{fmt}"
# === ANCHOR: REPORTS_SUMMARY_PATH_END ===


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

    from apps.server.domain.report_docx import build_session_report_docx, build_summary_docx
    from apps.server.domain.report_html import build_session_report_html, build_summary_html
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

    # summary.{md,html,docx,pdf} — standalone summary files (best-effort, each
    # format attempted independently; only when a summary is present).
    if summary:
        # summary.md
        try:
            s_path = summary_path(storage_root, session_id, "md")
            header = f"# 요약 — {meeting.title}\n\n"
            s_path.write_text(header + summary + "\n", encoding="utf-8")
            results["summary"] = s_path
        except Exception as exc:  # noqa: BLE001
            logger.warning("write_session_exports: summary.md failed: %s", exc)
            results["summary"] = None

        # summary.html
        try:
            sh_path = summary_path(storage_root, session_id, "html")
            sh_path.write_text(build_summary_html(meeting, summary), encoding="utf-8")
            results["summary_html"] = sh_path
        except Exception as exc:  # noqa: BLE001
            logger.warning("write_session_exports: summary.html failed: %s", exc)
            results["summary_html"] = None

        # summary.docx
        summary_docx_bytes: bytes | None = None
        try:
            sd_path = summary_path(storage_root, session_id, "docx")
            summary_docx_bytes = build_summary_docx(meeting, summary)
            sd_path.write_bytes(summary_docx_bytes)
            results["summary_docx"] = sd_path
        except Exception as exc:  # noqa: BLE001
            logger.warning("write_session_exports: summary.docx failed: %s", exc)
            results["summary_docx"] = None
            summary_docx_bytes = None

        # summary.pdf (requires soffice; skip silently if unavailable)
        try:
            if summary_docx_bytes is not None:
                summary_pdf_bytes = convert_docx_to_pdf(summary_docx_bytes)
                if summary_pdf_bytes is not None:
                    sp_path = summary_path(storage_root, session_id, "pdf")
                    sp_path.write_bytes(summary_pdf_bytes)
                    results["summary_pdf"] = sp_path
                else:
                    results["summary_pdf"] = None
            else:
                results["summary_pdf"] = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("write_session_exports: summary.pdf failed: %s", exc)
            results["summary_pdf"] = None
    else:
        results["summary"] = None
        results["summary_html"] = None
        results["summary_docx"] = None
        results["summary_pdf"] = None

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
        summary = None

    # Always emit the report files here (in a thread — write_session_exports runs
    # blocking work like the LibreOffice PDF conversion). end_session no longer
    # writes them synchronously, so this is the single emission point: with the
    # summary if one was produced, without it otherwise.
    try:
        await asyncio.to_thread(
            write_session_exports, storage_root, meeting, utterances, summary  # type: ignore[arg-type]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("regenerate_report_with_summary: emit failed: %s", exc)

    return summary
# === ANCHOR: REPORTS_REGENERATE_WITH_SUMMARY_END ===
# === ANCHOR: REPORTS_END ===
