# === ANCHOR: TEST_REPORTS_START ===
"""Unit tests for S1: build_session_report() readability layout improvements."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from apps.server.domain.reports import _hms, build_session_report, merge_continuation_utterances

# ---------------------------------------------------------------------------
# Helpers shared with merge tests
# ---------------------------------------------------------------------------

def _utt_ns(
    seq: int,
    speaker: str | None,
    text_en: str,
    text_ko: str,
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
# Helpers — lightweight stubs (no DB required)
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
# (i) Empty utterances → _No utterances recorded._
# ---------------------------------------------------------------------------

def test_empty_utterances_shows_no_utterances_placeholder() -> None:
    meeting = _make_meeting()
    result = build_session_report(meeting, [])
    assert "_No utterances recorded._" in result


# ---------------------------------------------------------------------------
# (ii) speaker=None → 발화자 미상, no crash
# ---------------------------------------------------------------------------

def test_none_speaker_renders_unknown_label_and_no_crash() -> None:
    meeting = _make_meeting()
    utt = _make_utterance(speaker=None)
    result = build_session_report(meeting, [utt])
    assert "발화자 미상" in result
    # Must not contain bare empty parentheses like " ()"
    assert " ()" not in result


# ---------------------------------------------------------------------------
# (iii) Consecutive same-speaker utterances → 1 merged block (one ### header)
# ---------------------------------------------------------------------------

def test_consecutive_same_speaker_merged_into_one_block() -> None:
    meeting = _make_meeting()
    t1 = datetime(2026, 6, 24, 9, 5, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 24, 9, 6, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 6, 24, 9, 7, 0, tzinfo=timezone.utc)
    utterances = [
        _make_utterance(seq=1, speaker="Alice", text_en="First.", text_ko="첫 번째.", started_at=t1, ended_at=t1),
        _make_utterance(seq=2, speaker="Alice", text_en="Second.", text_ko="두 번째.", started_at=t2, ended_at=t2),
        _make_utterance(seq=3, speaker="Alice", text_en="Third.", text_ko="세 번째.", started_at=t3, ended_at=t3),
    ]
    result = build_session_report(meeting, utterances)
    # Only one ### block header for Alice (consecutive → merged)
    alice_headers = [line for line in result.splitlines() if line.startswith("###") and "Alice" in line]
    assert len(alice_headers) == 1, f"Expected 1 Alice block header, got {len(alice_headers)}: {alice_headers}"


# ---------------------------------------------------------------------------
# (iv) KO text appears before EN text in the output
# ---------------------------------------------------------------------------

def test_ko_text_appears_before_en_text() -> None:
    meeting = _make_meeting()
    utt = _make_utterance(text_en="Hello world.", text_ko="안녕하세요.")
    result = build_session_report(meeting, [utt])
    ko_idx = result.index("안녕하세요.")
    en_idx = result.index("Hello world.")
    assert ko_idx < en_idx, "KO text must appear before EN text in the report"


# ---------------------------------------------------------------------------
# (v) Speaker change → new block created
# ---------------------------------------------------------------------------

def test_speaker_change_creates_new_block() -> None:
    meeting = _make_meeting()
    t1 = datetime(2026, 6, 24, 9, 5, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 24, 9, 6, 0, tzinfo=timezone.utc)
    utterances = [
        _make_utterance(seq=1, speaker="Alice", text_en="Alice speaks.", text_ko="앨리스 발화.", started_at=t1, ended_at=t1),
        _make_utterance(seq=2, speaker="Bob", text_en="Bob speaks.", text_ko="밥 발화.", started_at=t2, ended_at=t2),
    ]
    result = build_session_report(meeting, utterances)
    headers = [line for line in result.splitlines() if line.startswith("###")]
    # Alice and Bob must each have their own block header
    alice_blocks = [h for h in headers if "Alice" in h]
    bob_blocks = [h for h in headers if "Bob" in h]
    assert len(alice_blocks) == 1, f"Expected 1 Alice block, got {alice_blocks}"
    assert len(bob_blocks) == 1, f"Expected 1 Bob block, got {bob_blocks}"
    assert len(headers) == 2, f"Expected exactly 2 block headers, got {headers}"


# ---------------------------------------------------------------------------
# (vi) Summary stats present in header
# ---------------------------------------------------------------------------

def test_summary_stats_header_present() -> None:
    meeting = _make_meeting()
    t1 = datetime(2026, 6, 24, 9, 5, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 24, 9, 6, 0, tzinfo=timezone.utc)
    utterances = [
        _make_utterance(seq=1, speaker="Alice", started_at=t1, ended_at=t1),
        _make_utterance(seq=2, speaker="Bob", started_at=t2, ended_at=t2),
    ]
    result = build_session_report(meeting, utterances)
    # "총 발화 수" line was removed per user request — it must not appear.
    assert "총 발화 수" not in result
    # Both speaker names should appear in a stats section before the utterances block
    stats_section = result.split("## Utterances")[0] if "## Utterances" in result else result
    assert "Alice" in stats_section
    assert "Bob" in stats_section


# ---------------------------------------------------------------------------
# (vii) _hms converts tz-aware UTC datetime to server local timezone
# ---------------------------------------------------------------------------

def test_hms_converts_utc_to_local_timezone() -> None:
    """_hms() on a tz-aware UTC datetime must match astimezone() output."""
    from datetime import datetime, timezone
    dt_utc = datetime(2026, 6, 24, 5, 8, 0, tzinfo=timezone.utc)
    expected = dt_utc.astimezone().strftime("%H:%M:%S")
    assert _hms(dt_utc) == expected


# ---------------------------------------------------------------------------
# FIX 2: merge_continuation_utterances unit tests
# ---------------------------------------------------------------------------

def test_merge_continuation_unit_basic():
    """Continuation rows (empty en, same speaker) merge into the previous turn."""
    rows = [
        _utt_ns(1, "A", "Hello there.", "안녕하세요."),
        _utt_ns(2, "A", "", "잘 지내요."),
        _utt_ns(3, "A", "", "반갑습니다."),
    ]
    result = merge_continuation_utterances(rows)
    assert len(result) == 1
    assert result[0].text_en == "Hello there."
    assert result[0].text_ko == "안녕하세요. 잘 지내요. 반갑습니다."
    assert result[0].speaker == "A"


def test_merge_continuation_unit_speaker_change_no_merge():
    """A speaker change must NOT merge rows even when text_en is empty."""
    rows = [
        _utt_ns(1, "A", "Hello.", "안녕."),
        _utt_ns(2, "B", "", "잘 지내요."),
    ]
    result = merge_continuation_utterances(rows)
    assert len(result) == 2
    assert result[0].speaker == "A"
    assert result[1].speaker == "B"
    assert result[1].text_ko == "잘 지내요."


def test_merge_continuation_unit_own_en_starts_new_turn():
    """A row that has its own text_en must always start a new turn."""
    rows = [
        _utt_ns(1, "A", "First.", "첫째."),
        _utt_ns(2, "A", "Second.", "둘째."),
    ]
    result = merge_continuation_utterances(rows)
    assert len(result) == 2
    assert result[0].text_en == "First."
    assert result[1].text_en == "Second."


# ---------------------------------------------------------------------------
# FIX 2: build_session_report body uses merged rows
# ---------------------------------------------------------------------------

def test_build_session_report_merges_continuation_rows():
    """Report body must merge continuation rows into one EN + merged KO turn."""
    meeting = _make_meeting()
    utterances = [
        _utt_ns(1, "A", "Hello there.", "안녕하세요."),
        _utt_ns(2, "A", "", "잘 지내요."),
        _utt_ns(3, "A", "", "반갑습니다."),
    ]
    result = build_session_report(meeting, utterances)
    # Merged Korean must appear in one combined line.
    assert "안녕하세요. 잘 지내요. 반갑습니다." in result
    # English must appear once.
    assert "Hello there." in result
    # No empty-English lines for seq2/seq3 should exist as separate KO entries.
    ko_lines = [l for l in result.splitlines() if l.startswith("- KO:")]
    assert len(ko_lines) == 1, f"Expected 1 KO line, got {len(ko_lines)}: {ko_lines}"
    en_lines = [l for l in result.splitlines() if l.startswith("- EN:") and l.strip() != "- EN:"]
    assert len(en_lines) == 1, f"Expected 1 non-empty EN line, got {en_lines}"
# === ANCHOR: TEST_REPORTS_END ===
