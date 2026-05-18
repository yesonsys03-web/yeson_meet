# === ANCHOR: REPORTS_START ===
"""Markdown report generation for completed meeting sessions."""
from __future__ import annotations

from pathlib import Path

from apps.server.db.models import Session, Utterance


# === ANCHOR: REPORTS_BUILD_SESSION_REPORT_START ===
def build_session_report(meeting: Session, utterances: list[Utterance]) -> str:
    """Build a simple chronological Markdown report for one meeting."""
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
    lines.extend(["", "## Utterances", ""])

    if not utterances:
        lines.append("_No utterances recorded._")
    for row in utterances:
        speaker = f" ({row.speaker})" if row.speaker else ""
        lines.extend(
            [
                f"### {row.seq}. {row.started_at.isoformat()} – {row.ended_at.isoformat()}{speaker}",
                "",
                f"- EN: {row.text_en}",
                f"- KO: {row.text_ko}",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"
# === ANCHOR: REPORTS_BUILD_SESSION_REPORT_END ===


# === ANCHOR: REPORTS_REPORT_PATH_START ===
def report_path(storage_root: str | Path, session_id: str) -> Path:
    """Return the canonical report path for a session."""
    return Path(storage_root) / session_id / "report.md"
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
# === ANCHOR: REPORTS_END ===
