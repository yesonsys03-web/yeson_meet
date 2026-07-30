from __future__ import annotations

import os
from pathlib import Path

import pytest

from apps.server.domain.pdf_translate.backend import open_pdf
from apps.server.domain.pdf_translate.profiles import detect_profile
from apps.server.domain.pdf_translate.profiles.base import PdfBlock
from apps.server.domain.pdf_translate.utterances import group_utterances

_BBOX = (0.0, 0.0, 10.0, 10.0)


def _block(page: int, kind: str, text: str) -> PdfBlock:
    return PdfBlock(page=page, kind=kind, text=text, bbox=_BBOX)


def test_three_page_chain_merges_into_one_group():
    blocks = [
        _block(0, "dialog", "97 JOSEPH You know,"),
        _block(1, "dialog", "97 JOSEPH (Cont.) I was thinking"),
        _block(2, "dialog", "97 JOSEPH (Cont.)"),
    ]
    groups, texts = group_utterances(blocks)
    assert len(groups) == 1
    assert groups[0].member_indices == [0, 1, 2]
    assert groups[0].merged_text == "97 JOSEPH You know, I was thinking"
    assert texts == ["97 JOSEPH You know, I was thinking"]


def test_different_cue_number_breaks_chain_even_with_same_speaker():
    # 화자(JOSEPH)는 같지만 큐번호가 97→98로 바뀌면 별개 발화다.
    blocks = [
        _block(0, "dialog", "97 JOSEPH You know,"),
        _block(1, "dialog", "98 JOSEPH (CONT.) Something else."),
    ]
    groups, texts = group_utterances(blocks)
    assert len(groups) == 2
    assert groups[0].member_indices == [0]
    assert groups[0].merged_text == "97 JOSEPH You know,"
    assert groups[1].member_indices == [1]
    assert groups[1].merged_text == "98 JOSEPH (CONT.) Something else."
    assert texts == [groups[0].merged_text, groups[1].merged_text]


def test_no_cue_pattern_and_non_dialog_kinds_are_singleton_groups():
    blocks = [
        _block(0, "dialog", "HANK HUMS"),
        _block(0, "action", "Hank walks to the door."),
        _block(0, "panel_label", "HANK'S TRUCK"),
    ]
    groups, texts = group_utterances(blocks)
    assert len(groups) == 3
    assert [g.member_indices for g in groups] == [[0], [1], [2]]
    assert texts == ["HANK HUMS", "Hank walks to the door.", "HANK'S TRUCK"]


def test_action_block_between_chain_members_does_not_break_chain():
    blocks = [
        _block(0, "dialog", "97 JOSEPH You know,"),
        _block(0, "action", "Joseph pauses."),
        _block(1, "dialog", "97 JOSEPH (Cont.) I was thinking"),
    ]
    groups, texts = group_utterances(blocks)
    assert len(groups) == 2
    chain = next(g for g in groups if g.member_indices == [0, 2])
    action = next(g for g in groups if g.member_indices == [1])
    assert chain.merged_text == "97 JOSEPH You know, I was thinking"
    assert action.merged_text == "Joseph pauses."
    assert set(texts) == {chain.merged_text, action.merged_text}


SAMPLES = os.environ.get("YESON_PDF_SAMPLES")


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
def test_real_storyboard_utterance_grouping():
    """실물 검증(로컬 전용, 2026-07-30): GABE01_A1(1037페이지) 전체 추출 →
    발화 단위 병합. 실측(이 환경, 1회 런): blocks=1305 → groups=725(브리프
    추정 "1027블록 → ~650±100그룹"과 대체로 부합 — 실제 블록 수는 1305로
    더 많았지만 그룹 수는 예상 범위 안에 들었다). p119~123(0-based, 다섯
    페이지 전부) DONNA 체인이 5블록짜리 한 그룹으로 묶이고, merged_text에
    (CONT.)-헤더 조각뿐 아니라 실제 대사 본문이 담겨야 한다 — 조각째
    번역이면 이 중 다수 페이지가 "도나 (계속)"류로만 남았을 것이다.

    구조적 사실만 단언한다(브리프 리뷰 후속 지시, 2026-07-30): 전체 대사
    문단을 그대로 문자열 비교하면 향후 OCR/추출 미세 변동에 깨지기 쉬우니,
    체인 존재·멤버 수·화자명·확인된 본문 일부(부분 문자열)만 확인한다."""
    path = Path(SAMPLES) / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
    doc = open_pdf(path)
    try:
        profile = detect_profile(doc)
        assert profile is not None and profile.name == "storyboard"
        blocks = profile.extract(doc)
    finally:
        doc.close()

    groups, _texts = group_utterances(blocks)
    print(f"pdf-translate utterances (real sample): "
          f"blocks={len(blocks)} groups={len(groups)}")

    # 병합으로 그룹 수가 블록 수보다 뚜렷이 줄어야 한다(그렇지 않으면
    # 체이닝이 사실상 아무것도 안 묶은 것).
    assert len(groups) < len(blocks)
    assert 500 <= len(groups) <= 800  # 브리프 추정(~650±100)과 부합하는 범위

    donna_chain = next(
        (g for g in groups
         if len(g.member_indices) >= 5
         and {blocks[i].page for i in g.member_indices}
         == {119, 120, 121, 122, 123}),
        None)
    assert donna_chain is not None, "p119~123 DONNA 체인이 한 그룹으로 묶이지 않음"
    assert "DONNA" in donna_chain.merged_text.upper()
    # 실측 확인된 본문 일부(전문 문자열 비교는 하지 않는다 — 위 docstring 참고)
    assert "accounting was a mess" in donna_chain.merged_text
