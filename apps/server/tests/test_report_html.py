# === ANCHOR: TEST_REPORT_HTML_START ===
"""Unit tests for S2: build_session_report_html() HTML export."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from apps.server.domain.report_html import _THEMES, build_session_report_html


# ---------------------------------------------------------------------------
# Helpers — same lightweight stubs as test_reports.py
# ---------------------------------------------------------------------------

def _make_meeting(
    title: str = "Test Meeting",
    external_id: str = "abc-123",
    status: str = "ended",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    client_label: str | None = None,
) -> SimpleNamespace:
    base = datetime(2026, 6, 24, 9, 0, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        title=title,
        external_id=external_id,
        status=status,
        started_at=started_at or base,
        ended_at=ended_at or datetime(2026, 6, 24, 9, 30, 0, tzinfo=timezone.utc),
        client_label=client_label,
    )


def _make_utterance(
    seq: int = 1,
    speaker: str | None = "Alice",
    text_en: str = "Hello world.",
    text_ko: str = "안녕하세요.",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> SimpleNamespace:
    base = datetime(2026, 6, 24, 9, 5, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        seq=seq,
        speaker=speaker,
        text_en=text_en,
        text_ko=text_ko,
        started_at=started_at or base,
        ended_at=ended_at or base,
    )


# ---------------------------------------------------------------------------
# (i) Return value is a complete HTML document
# ---------------------------------------------------------------------------

def test_returns_complete_html_document() -> None:
    meeting = _make_meeting()
    result = build_session_report_html(meeting, [])
    assert result.strip().startswith("<!DOCTYPE html>")
    assert "<html" in result
    assert "</html>" in result


# ---------------------------------------------------------------------------
# (ii) KO text appears before EN text in the output
# ---------------------------------------------------------------------------

def test_ko_text_appears_before_en_text() -> None:
    meeting = _make_meeting()
    utt = _make_utterance(text_ko="안녕하세요.", text_en="Hello world.")
    result = build_session_report_html(meeting, [utt])
    ko_idx = result.index("안녕하세요.")
    en_idx = result.index("Hello world.")
    assert ko_idx < en_idx, "KO text must appear before EN text"


# ---------------------------------------------------------------------------
# (iii) speaker=None → 발화자 미상
# ---------------------------------------------------------------------------

def test_none_speaker_renders_unknown_label() -> None:
    meeting = _make_meeting()
    utt = _make_utterance(speaker=None)
    result = build_session_report_html(meeting, [utt])
    assert "발화자 미상" in result


# ---------------------------------------------------------------------------
# (iv) Consecutive same-speaker utterances → single speaker-block header
# ---------------------------------------------------------------------------

def test_consecutive_same_speaker_merged_into_one_block() -> None:
    meeting = _make_meeting()
    t1 = datetime(2026, 6, 24, 9, 5, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 24, 9, 6, 0, tzinfo=timezone.utc)
    utterances = [
        _make_utterance(seq=1, speaker="Alice", text_ko="첫 번째.", started_at=t1, ended_at=t1),
        _make_utterance(seq=2, speaker="Alice", text_ko="두 번째.", started_at=t2, ended_at=t2),
    ]
    result = build_session_report_html(meeting, utterances)
    # speaker-header appears once for consecutive Alice blocks
    alice_headers = [
        line for line in result.splitlines()
        if 'class="speaker-header"' in line and "Alice" in line
    ]
    assert len(alice_headers) == 1, f"Expected 1 Alice speaker-header, got {len(alice_headers)}"


# ---------------------------------------------------------------------------
# (v) Special HTML chars in subtitle text are escaped (XSS guard)
# ---------------------------------------------------------------------------

def test_html_special_chars_are_escaped() -> None:
    meeting = _make_meeting()
    utt = _make_utterance(
        text_ko="<script>alert('xss')</script>",
        text_en="AT&T & <b>bold</b>",
    )
    result = build_session_report_html(meeting, [utt])
    # Raw tags must not appear
    assert "<script>" not in result
    assert "<b>" not in result
    # Escaped forms must be present
    assert "&lt;script&gt;" in result
    assert "&amp;" in result


# ---------------------------------------------------------------------------
# (vi) Vendoring attribution comment present in the module source
# ---------------------------------------------------------------------------

def test_vendoring_attribution_comment_present() -> None:
    import inspect
    import apps.server.domain.report_html as mod
    src = inspect.getsource(mod)
    assert "Vendored from VibeLign" in src, "Vendored attribution comment must be in source"


# ---------------------------------------------------------------------------
# (vii) Theme switching works — all bundled themes render valid HTML
# ---------------------------------------------------------------------------

def test_all_bundled_themes_render_valid_html() -> None:
    meeting = _make_meeting()
    utt = _make_utterance()
    for theme_name in _THEMES:
        result = build_session_report_html(meeting, [utt], theme=theme_name)
        assert "<!DOCTYPE html>" in result, f"Theme {theme_name!r} did not produce valid HTML"
        assert "</html>" in result
# === ANCHOR: TEST_REPORT_HTML_END ===
