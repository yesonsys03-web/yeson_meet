# === ANCHOR: TEST_SUBTITLE_QUALITY_START ===
"""Tests for subtitle coverage heuristics."""
from __future__ import annotations

from apps.server.ai.subtitle_quality import assess_subtitle_quality


def _codes(text_en: str, text_ko: str) -> set[str]:
    return {issue.code for issue in assess_subtitle_quality(text_en, text_ko).issues}


def test_passes_when_numbers_units_and_terms_are_preserved() -> None:
    report = assess_subtitle_quality(
        "Gemini processed 60,000 years of AI audio assets.",
        "Gemini가 6만 년 분량의 AI 오디오 자산을 처리했습니다.",
    )

    assert report.passed is True


def test_flags_missing_number() -> None:
    codes = _codes(
        "Revenue increased by 42 percent after launch.",
        "출시 후 매출이 크게 증가했습니다.",
    )

    assert "missing_number" in codes


def test_flags_changed_numeric_unit() -> None:
    codes = _codes(
        "The archive contains 60,000 years of audio assets.",
        "아카이브에는 6만 시간 분량의 오디오 자산이 있습니다.",
    )

    assert "unit_mismatch" in codes


def test_flags_missing_proper_noun() -> None:
    codes = _codes(
        "Vertex AI will sync the Dolby Atmos pipeline.",
        "파이프라인을 동기화합니다.",
    )

    assert "missing_proper_noun" in codes


def test_flags_empty_translation() -> None:
    codes = _codes("Please review the integration contract.", "")

    assert codes == {"empty_translation"}


def test_flags_suspiciously_short_translation() -> None:
    codes = _codes(
        "Please verify the transparent reporting standard for the enterprise AI rollout.",
        "확인하세요.",
    )

    assert "translation_too_short" in codes
# === ANCHOR: TEST_SUBTITLE_QUALITY_END ===
