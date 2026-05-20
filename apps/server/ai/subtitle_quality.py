# === ANCHOR: SUBTITLE_QUALITY_START ===
"""Heuristic checks for English-to-Korean subtitle coverage."""
from __future__ import annotations

from dataclasses import dataclass, field
import re


_NUMBER_RE = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?(?:\s*(?:billion|million|thousand))?\s*(?:%|percent|x|times)?\b",
    re.IGNORECASE,
)
_KOREAN_NUMBER_RE = re.compile(r"\d[\d,]*\s*억|\d[\d,]*\s*만|\d[\d,]*(?:\.\d+)?")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9&+-]*")
_PROPER_NOUN_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9&+-]*|[A-Z]{2,})(?:\s+(?:[A-Z][A-Za-z0-9&+-]*|[A-Z]{2,}))*\b"
)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

_COMMON_CAPITALIZED = {
    "A",
    "An",
    "And",
    "As",
    "But",
    "For",
    "Great",
    "I",
    "It",
    "Our",
    "On",
    "See",
    "That",
    "The",
    "This",
    "To",
    "Using",
    "We",
    "We're",
    "You",
}
_DOMAIN_TERMS = {
    "AI",
    "Atmos",
    "DeepMind",
    "Dolby",
    "Gemini",
    "Google",
    "Omni",
    "Photos",
    "Pixel",
    "Vertex",
}
_TERM_TRANSLATIONS = {
    "Eleven Labs": {"11 Labs", "일레븐랩스", "일레븐 랩스"},
    "Gemini": {"제미나이"},
    "Google": {"구글"},
    "Google Photos": {"구글 포토", "Google 포토"},
    "Google AI Studio": {"Google AI", "구글 AI", "구글 AI 스튜디오"},
    "Flash": {"플래시"},
    "Anti-Gravity": {"Antigravity", "anti gravity", "반중력"},
}

_UNIT_ALIASES = {
    "year": {"year", "years", "년", "연"},
    "month": {"month", "months", "개월", "월"},
    "week": {"week", "weeks", "주"},
    "day": {"day", "days", "일"},
    "hour": {"hour", "hours", "hr", "hrs", "시간"},
    "minute": {"minute", "minutes", "min", "mins", "분"},
    "second": {"second", "seconds", "sec", "secs", "초"},
    "percent": {"%", "percent", "percentage", "퍼센트", "프로"},
    "dollar": {"dollar", "dollars", "usd", "$", "달러"},
}
_EN_UNIT_TO_KIND = {
    alias: kind for kind, aliases in _UNIT_ALIASES.items() for alias in aliases if alias.isascii()
}
_KO_UNIT_TO_KIND = {
    alias.casefold(): kind
    for kind, aliases in _UNIT_ALIASES.items()
    for alias in aliases
    if not alias.isascii() or alias == "%"
}


# === ANCHOR: SUBTITLE_QUALITY_MODELS_START ===
@dataclass(frozen=True)
class SubtitleQualityIssue:
    """One suspected subtitle coverage problem."""

    code: str
    severity: str
    message: str
    evidence: str


@dataclass(frozen=True)
class SubtitleQualityReport:
    """Result of comparing one English source utterance with one Korean subtitle."""

    issues: tuple[SubtitleQualityIssue, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.issues
# === ANCHOR: SUBTITLE_QUALITY_MODELS_END ===


# === ANCHOR: SUBTITLE_QUALITY_ASSESS_START ===
def assess_subtitle_quality(text_en: str, text_ko: str) -> SubtitleQualityReport:
    """Flag likely omissions or meaning drift in a translated subtitle."""

    source = text_en.strip()
    translated = text_ko.strip()
    issues: list[SubtitleQualityIssue] = []

    if not source:
        return SubtitleQualityReport()
    if not translated:
        return SubtitleQualityReport(
            (
                SubtitleQualityIssue(
                    code="empty_translation",
                    severity="error",
                    message="Korean subtitle is empty even though English source exists.",
                    evidence=source,
                ),
            )
        )

    if _looks_too_short(source, translated):
        issues.append(
            SubtitleQualityIssue(
                code="translation_too_short",
                severity="warning",
                message="Korean subtitle is much shorter than the English source.",
                evidence=f"en_chars={len(source)}, ko_chars={len(translated)}",
            )
        )

    issues.extend(_missing_numbers(source, translated))
    issues.extend(_unit_mismatches(source, translated))
    issues.extend(_missing_proper_nouns(source, translated))
    issues.extend(_low_keyword_coverage(source, translated))

    return SubtitleQualityReport(tuple(issues))
# === ANCHOR: SUBTITLE_QUALITY_ASSESS_END ===


# === ANCHOR: SUBTITLE_QUALITY_HEURISTICS_START ===
def _looks_too_short(source: str, translated: str) -> bool:
    source_words = _content_words(source)
    return len(source_words) >= 8 and len(translated) < max(12, len(source) * 0.25)


def _missing_numbers(source: str, translated: str) -> list[SubtitleQualityIssue]:
    translated_numbers = {_normalize_number(item.group(0)) for item in _KOREAN_NUMBER_RE.finditer(translated)}
    issues: list[SubtitleQualityIssue] = []
    for match in _NUMBER_RE.finditer(source):
        number = match.group(0).strip()
        normalized = _normalize_number(number)
        if normalized and normalized not in translated_numbers:
            issues.append(
                SubtitleQualityIssue(
                    code="missing_number",
                    severity="error",
                    message="A number from the English source is missing in the Korean subtitle.",
                    evidence=number,
                )
            )
    return issues


def _unit_mismatches(source: str, translated: str) -> list[SubtitleQualityIssue]:
    source_pairs = _number_unit_pairs(source, _EN_UNIT_TO_KIND)
    translated_pairs = _number_unit_pairs(translated, _KO_UNIT_TO_KIND)
    issues: list[SubtitleQualityIssue] = []
    for source_number, source_unit in source_pairs.items():
        translated_unit = translated_pairs.get(source_number)
        if translated_unit and translated_unit != source_unit:
            issues.append(
                SubtitleQualityIssue(
                    code="unit_mismatch",
                    severity="error",
                    message="A numeric unit appears to have changed during translation.",
                    evidence=f"{source_number}: {source_unit} -> {translated_unit}",
                )
            )
    return issues


def _missing_proper_nouns(source: str, translated: str) -> list[SubtitleQualityIssue]:
    issues: list[SubtitleQualityIssue] = []
    for term in _proper_nouns(source):
        if not _term_present(term, translated):
            issues.append(
                SubtitleQualityIssue(
                    code="missing_proper_noun",
                    severity="warning",
                    message="A proper noun or product term may be missing from the Korean subtitle.",
                    evidence=term,
                )
            )
    return issues


def _low_keyword_coverage(source: str, translated: str) -> list[SubtitleQualityIssue]:
    source_keywords = _technical_keywords(source)
    if len(source_keywords) < 3:
        return []
    translated_folded = translated.casefold()
    retained = [word for word in source_keywords if word.casefold() in translated_folded]
    if retained or len(translated) >= len(source) * 0.35:
        return []
    return [
        SubtitleQualityIssue(
            code="low_keyword_coverage",
            severity="warning",
            message="Technical English keywords are not visible in a very short Korean subtitle.",
            evidence=", ".join(source_keywords[:5]),
        )
    ]


def _proper_nouns(source: str) -> list[str]:
    terms: list[str] = []
    sentence_starts = _sentence_start_offsets(source)
    for match in _PROPER_NOUN_RE.finditer(source):
        words = match.group(0).strip().split()
        while len(words) > 1 and words[0] in _COMMON_CAPITALIZED:
            words = words[1:]
        term = " ".join(words)
        if _YEAR_RE.fullmatch(term):
            continue
        if all(word in _COMMON_CAPITALIZED for word in words):
            continue
        has_domain_term = any(word in _DOMAIN_TERMS for word in words)
        has_acronym = any(word.isupper() and len(word) > 1 for word in words)
        has_inner_upper = any(any(char.isupper() for char in word[1:]) for word in words)
        starts_sentence = match.start() in sentence_starts
        if starts_sentence and not has_domain_term and not has_acronym:
            continue
        if len(words) == 1 and not (has_domain_term or has_acronym or has_inner_upper):
            continue
        terms.append(term)
    return list(dict.fromkeys(terms))


def _term_present(term: str, translated: str) -> bool:
    translated_folded = translated.casefold()
    compact_translated = _compact_term(translated)
    variants = {term, *_TERM_TRANSLATIONS.get(term, set())}
    return any(
        variant.casefold() in translated_folded
        or _compact_term(variant) in compact_translated
        for variant in variants
    )


def _compact_term(term: str) -> str:
    return re.sub(r"[\s_-]+", "", term.casefold())


def _sentence_start_offsets(source: str) -> set[int]:
    offsets = {0}
    for match in re.finditer(r"[.!?]\s+", source):
        offsets.add(match.end())
    return offsets


def _technical_keywords(source: str) -> list[str]:
    words = _content_words(source)
    return list(dict.fromkeys(word for word in words if len(word) >= 5))


def _content_words(source: str) -> list[str]:
    stop_words = {
        "about",
        "after",
        "again",
        "before",
        "could",
        "every",
        "from",
        "great",
        "have",
        "into",
        "more",
        "should",
        "that",
        "their",
        "there",
        "this",
        "with",
        "would",
    }
    words = [match.group(0).casefold() for match in _TOKEN_RE.finditer(source)]
    return [word for word in words if word not in stop_words]


def _number_unit_pairs(text: str, unit_map: dict[str, str]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    pattern = re.compile(
        r"(?P<number>\d[\d,]*\s*억|\d[\d,]*\s*만|\d[\d,]*(?:\.\d+)?)\s*(?P<unit>[A-Za-z%가-힣]+)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        unit = unit_map.get(match.group("unit").casefold())
        number = _normalize_number(match.group("number"))
        if number and unit:
            pairs[number] = unit
    return pairs


def _normalize_number(value: str) -> str:
    cleaned = value.casefold().replace(",", "").replace("percent", "").replace("times", "")
    cleaned = cleaned.replace("x", "").replace("%", "").strip()
    korean_large = re.fullmatch(r"([\d,]+)\s*([억만])", value.strip())
    if korean_large:
        multiplier = 100_000_000 if korean_large.group(2) == "억" else 10_000
        return str(int(korean_large.group(1).replace(",", "")) * multiplier)
    english_large = re.fullmatch(r"([\d,]+(?:\.\d+)?)\s*(billion|million|thousand)", cleaned)
    if english_large:
        multiplier = {"billion": 1_000_000_000, "million": 1_000_000, "thousand": 1_000}[
            english_large.group(2)
        ]
        return str(int(float(english_large.group(1)) * multiplier))
    return cleaned
# === ANCHOR: SUBTITLE_QUALITY_HEURISTICS_END ===
# === ANCHOR: SUBTITLE_QUALITY_END ===
