# === ANCHOR: REPORT_HTML_START ===
"""HTML report generation for completed meeting sessions (S2)."""
from __future__ import annotations

import html
from itertools import groupby

from apps.server.db.models import Session, Utterance
from apps.server.domain.reports import _hms, _speaker_label, _to_local

# ---------------------------------------------------------------------------
# Vendored theme CSS
# Vendored from VibeLign vibelign/core/reporting_cli/theme_catalog.py (MIT)
# ---------------------------------------------------------------------------

# Vendored from VibeLign html_renderer.py (MIT) — theme: minimal
_CSS_MINIMAL = """
  :root { --ink:#222; --paper:#fff; --accent:#444; }
  * { box-sizing:border-box; }
  body { font-family:-apple-system,"Pretendard","맑은 고딕","Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif;
         color:var(--ink); background:var(--paper);
         max-width:720px; margin:0 auto; padding:64px 48px; line-height:1.8; letter-spacing:-0.01em; }
  h1 { font-size:28px; font-weight:800; }
  .meta { color:#999; font-size:12px; margin-top:6px; }
  .stats { color:#555; font-size:13px; margin:12px 0 24px; }
  .stats dt { font-weight:700; }
  .stats dd { margin:0 0 4px 0; }
  .speaker-block { margin-bottom:28px; }
  .speaker-header { font-size:14px; font-weight:700; color:var(--accent); text-transform:uppercase;
                    letter-spacing:.08em; margin:0 0 6px; border-top:1px solid #eee; padding-top:8px; }
  .utterance { margin:8px 0 8px 0; }
  .ko { font-size:15px; font-weight:600; color:#111; }
  .en { font-size:12px; color:#888; margin-top:2px; }
  @media print { body { padding:0; max-width:none; } }
"""

# Vendored from VibeLign html_renderer.py (MIT) — theme: executive
_CSS_EXECUTIVE = """
  :root { --ink:#1c2430; --paper:#fff; --accent:#1B3A6B; }
  * { box-sizing:border-box; }
  body { font-family:-apple-system,"Pretendard","맑은 고딕","Malgun Gothic","Apple SD Gothic Neo",sans-serif;
         color:var(--ink); background:var(--paper);
         max-width:800px; margin:0 auto; padding:0 0 48px; line-height:1.7; }
  h1 { font-size:30px; font-weight:900; color:#fff; background:var(--accent); margin:0; padding:36px 40px; }
  .meta { color:#fff; background:var(--accent); margin:0; padding:0 40px 24px; font-size:13px; opacity:.85; }
  .stats { padding:16px 40px; background:#f0f4fa; font-size:13px; color:#333; }
  .stats dt { font-weight:700; display:inline; }
  .stats dd { display:inline; margin:0 20px 0 4px; }
  .speaker-block { padding-left:40px; padding-right:40px; margin-bottom:24px; }
  .speaker-header { font-size:15px; color:var(--accent); border-left:5px solid var(--accent);
                    padding:2px 0 2px 12px; margin:30px 0 8px; font-weight:700; }
  .utterance { margin:8px 0 8px 0; }
  .ko { font-size:15px; font-weight:600; color:#111; }
  .en { font-size:12px; color:#888; margin-top:2px; }
  @media print { body { padding:0; } }
"""

# Vendored from VibeLign html_renderer.py (MIT) — theme: classic
_CSS_CLASSIC = """
  :root { --ink:#1A1A1A; --paper:#F7F7F2; --accent:#9B1B1B; }
  * { box-sizing:border-box; }
  body { font-family:"Noto Serif KR","Apple SD Gothic Neo",-apple-system,"맑은 고딕","Malgun Gothic",serif;
         color:var(--ink); background:var(--paper);
         max-width:760px; margin:0 auto; padding:48px 40px; line-height:1.7; }
  h1 { font-size:26px; border-bottom:3px solid var(--accent); padding-bottom:10px; }
  .meta { color:#666; font-size:13px; margin-top:4px; }
  .stats { font-size:13px; color:#444; margin:12px 0 24px; }
  .stats dt { font-weight:700; display:inline; }
  .stats dd { display:inline; margin:0 16px 0 4px; }
  .speaker-block { margin-bottom:24px; }
  .speaker-header { font-size:16px; color:var(--accent); margin:24px 0 6px; font-weight:700; }
  .utterance { margin:8px 0 8px 0; }
  .ko { font-size:15px; font-weight:600; color:#111; }
  .en { font-size:12px; color:#888; margin-top:2px; }
  @media print { body { background:#fff; max-width:none; padding:0; } }
"""

_THEMES: dict[str, str] = {
    "minimal": _CSS_MINIMAL,
    "executive": _CSS_EXECUTIVE,
    "classic": _CSS_CLASSIC,
}

_DEFAULT_THEME = "minimal"


# === ANCHOR: REPORT_HTML_BUILD_START ===
def build_session_report_html(
    meeting: Session,
    utterances: list[Utterance],
    theme: str = _DEFAULT_THEME,
    summary: str | None = None,
) -> str:
    """Build a complete HTML document report for one meeting session (S2 layout).

    Applies the same speaker-grouping and KO-first rules as S1 (build_session_report).
    Returns a fully self-contained HTML string (inline CSS, no external deps).

    *summary* is an optional LLM-generated Korean summary (S6).  When provided
    it is inserted as a ``<section class="summary">`` block before the utterances.
    """
    css = _THEMES.get(theme, _CSS_MINIMAL)
    title_escaped = html.escape(meeting.title)

    # --- <head> ---
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="ko">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{title_escaped}</title>",
        f"<style>{css}</style>",
        "</head>",
        "<body>",
        f"<h1>{title_escaped}</h1>",
    ]

    # --- meta line ---
    started = _to_local(meeting.started_at).isoformat() if meeting.started_at else "N/A"
    ended = _to_local(meeting.ended_at).isoformat() if meeting.ended_at else "N/A"
    meta_parts = [
        f"Session: {html.escape(str(meeting.external_id))}",
        f"Status: {html.escape(str(meeting.status))}",
        f"Started: {html.escape(started)}",
        f"Ended: {html.escape(ended)}",
    ]
    if meeting.client_label:
        meta_parts.append(f"Client: {html.escape(meeting.client_label)}")
    parts.append(f'<p class="meta">{" &nbsp;|&nbsp; ".join(meta_parts)}</p>')

    # --- summary stats ---
    if utterances:
        speakers = sorted({_speaker_label(r.speaker) for r in utterances if r.speaker})
        duration = (
            f"{_hms(utterances[0].started_at)} – {_hms(utterances[-1].ended_at)}"
        )
        parts.append('<dl class="stats">')
        parts.append(
            f"<dt>참여 화자</dt><dd>{html.escape(', '.join(speakers)) if speakers else '없음'}</dd>"
        )
        parts.append(f"<dt>시간 범위</dt><dd>{html.escape(duration)}</dd>")
        parts.append("</dl>")

    # --- LLM summary block (S6) ---
    if summary:
        parts.append('<section class="summary">')
        parts.append("<h2>요약</h2>")
        # Render each line as a paragraph; blank lines produce visual spacing.
        for line in summary.splitlines():
            escaped = html.escape(line)
            if escaped.strip():
                parts.append(f"<p>{escaped}</p>")
        parts.append("</section>")

    # --- utterance blocks ---
    if not utterances:
        parts.append("<p><em>No utterances recorded.</em></p>")
    else:
        for speaker_key, group in groupby(utterances, key=lambda r: r.speaker):
            group_rows = list(group)
            label = _speaker_label(speaker_key)
            first = group_rows[0]
            parts.append('<div class="speaker-block">')
            parts.append(
                f'<h2 class="speaker-header">'
                f"{html.escape(_hms(first.started_at))} {html.escape(label)}"
                f"</h2>"
            )
            for row in group_rows:
                ko_text = html.escape(row.text_ko or "")
                en_text = html.escape(row.text_en or "")
                parts.append('<div class="utterance">')
                parts.append(f'<div class="ko">{ko_text}</div>')
                parts.append(f'<div class="en">{en_text}</div>')
                parts.append("</div>")
            parts.append("</div>")

    parts.extend(["</body>", "</html>"])
    return "\n".join(parts)
# === ANCHOR: REPORT_HTML_BUILD_END ===


# === ANCHOR: REPORT_HTML_SUMMARY_BUILD_START ===
def build_summary_html(
    meeting: Session,
    summary: str,
    theme: str = _DEFAULT_THEME,
) -> str:
    """Build a minimal self-contained HTML document for a standalone summary.

    Reuses the report theme CSS and HTML-escaping. Contains only the meeting
    title and the summary body (each non-blank line as a paragraph).
    """
    css = _THEMES.get(theme, _CSS_MINIMAL)
    title_escaped = html.escape(f"요약 — {meeting.title}")

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="ko">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{title_escaped}</title>",
        f"<style>{css}</style>",
        "</head>",
        "<body>",
        f"<h1>{title_escaped}</h1>",
        '<section class="summary">',
    ]
    for line in summary.splitlines():
        escaped = html.escape(line)
        if escaped.strip():
            parts.append(f"<p>{escaped}</p>")
    parts.append("</section>")
    parts.extend(["</body>", "</html>"])
    return "\n".join(parts)
# === ANCHOR: REPORT_HTML_SUMMARY_BUILD_END ===
# === ANCHOR: REPORT_HTML_END ===
