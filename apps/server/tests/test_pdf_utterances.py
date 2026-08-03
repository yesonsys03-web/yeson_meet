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


def test_multi_panel_document_keeps_each_fragment_on_its_own_page():
    """3단(다단) 문서는 발화를 잇지 않는다 — FL104 납품본 관례(2026-08-03,
    사용자 확인). 같은 페이지에 같은 kind 필드가 2개 이상이면 다단이다.

    1단 실측에서 온 '전문을 걸친 모든 페이지에 반복 기재' 관례를 3단에
    그대로 쓰면 두 페이지가 서로의 대사까지 보여준다(p18↔p20 실물)."""
    blocks = [
        _block(0, "action", "cycle 1/2"),
        _block(0, "action", "cycle 2/2"),   # 같은 페이지 2번째 action = 다단 신호
        _block(0, "dialog", "241 BELLE Wait, no-- no, no, no, no."),
        _block(1, "dialog", "241 BELLE What's happening? Is this--"),
    ]
    groups, _ = group_utterances(blocks)
    assert all(len(g.member_indices) == 1 for g in groups), (
        "3단 문서에서 발화가 병합됐다 — 각 페이지는 자기 조각만 가져야 한다")


def test_single_panel_document_still_chains_across_pages():
    """대조군 — 1단 경로의 기존 관례는 그대로다(회귀 가드)."""
    blocks = [
        _block(0, "action", "cycle 1/2"),
        _block(0, "dialog", "241 BELLE Wait, no-- no, no, no, no."),
        _block(1, "dialog", "241 BELLE What's happening? Is this--"),
    ]
    groups, _ = group_utterances(blocks)
    assert any(len(g.member_indices) == 2 for g in groups)


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


@pytest.mark.parametrize(
    ("first", "second", "expected_merged"),
    [
        # 리뷰 실측 — 슬래시 화자, 자체 few-shot 예시(translate_blocks.py:52)와 동일 형태.
        (
            "3 HANK/EMPLOYEES Propane.",
            "3 HANK/EMPLOYEES (CONT.) and butane.",
            "3 HANK/EMPLOYEES Propane. and butane.",
        ),
        # 리뷰 실측 — 공백 두 토큰 화자.
        (
            "5 BOODA SACK My bad, Hank.",
            "5 BOODA SACK (CONT.) Accidentally opened the valve.",
            "5 BOODA SACK My bad, Hank. Accidentally opened the valve.",
        ),
        # 리뷰 실측 — 화자명 자체가 두 토큰(HANK HILL).
        (
            "97 HANK HILL You know,",
            "97 HANK HILL (CONT.) more words",
            "97 HANK HILL You know, more words",
        ),
        # 리뷰 실측 — 마침표 포함 화자.
        (
            "26 MR. STRICKLAND Well,",
            "26 MR. STRICKLAND (CONT.) text",
            "26 MR. STRICKLAND Well, text",
        ),
        # 리뷰 실측 — 괄호 접미(O.S.) + (CONT.) 이중 주석.
        (
            "66 JIMMY (O.S.) Your pro-nuts suck!",
            "66 JIMMY (O.S.) (Cont.) more",
            "66 JIMMY (O.S.) Your pro-nuts suck! more",
        ),
        # 리뷰 실측 — `#`는 기존 문자 클래스에 아예 없던 문자.
        (
            "45 CLIENT #1 Strained?!",
            "45 CLIENT #1 (Cont.) Ray Roy hit on my mom!",
            "45 CLIENT #1 Strained?! Ray Roy hit on my mom!",
        ),
    ],
)
def test_multitoken_speaker_chain_strips_full_header_no_stray_markers(
    first: str, second: str, expected_merged: str,
) -> None:
    """Important #1 회귀: 다중 토큰(공백·/·.·#) 화자에서 헤더가 부분만
    제거되면 (CONT.)/(Cont.) 마커나 화자명 파편이 merged_text 본문에
    남는다 — 이는 병합 실패 사실을 감추고 모든 멤버 페이지에 노이즈를
    번역·기재하게 만든다."""
    blocks = [_block(0, "dialog", first), _block(1, "dialog", second)]
    groups, texts = group_utterances(blocks)
    assert len(groups) == 1
    merged = groups[0].merged_text
    assert merged == expected_merged
    assert "(CONT" not in merged
    assert "(Cont" not in merged
    assert texts == [expected_merged]


def test_reprinted_dialogue_tail_is_not_duplicated():
    """Important #2 회귀: 소스가 다음 패널에 누적 대사의 꼬리를 그대로
    재인쇄하면(DONNA 케이스, 화자는 단일 토큰이라 Important #1과 무관),
    무조건 이어붙이기는 이미 누적된 문장을 중복 기재한다."""
    blocks = [
        _block(0, "dialog", "9 DONNA Ray Roy sure did a lot of damage."),
        _block(1, "dialog", "9 DONNA (CONT.) But look at all these clients we still have!"),
        _block(2, "dialog", "9 DONNA (CONT.) look at all these clients we still have!"),
    ]
    groups, texts = group_utterances(blocks)
    assert len(groups) == 1
    merged = groups[0].merged_text
    assert groups[0].member_indices == [0, 1, 2]
    assert merged == (
        "9 DONNA Ray Roy sure did a lot of damage. "
        "But look at all these clients we still have!"
    )
    assert merged.count("look at all these clients we still have") == 1
    assert texts == [merged]


def test_reprinted_dialogue_tail_with_ellipsis_spacing_variant_is_not_duplicated():
    """Important #2 회귀 — 실물 코퍼스 그대로(GABE01_A1 p85-93 DONNA
    체인, 헤더-only (CONT.) 조각 포함): 재인쇄된 꼬리가 원 발화에서는
    `But... look`(말줄임표 뒤 공백)로, 재인쇄 조각에서는 `...look`(공백
    없음)으로 나타난다 — 순수 문자열 포함 비교라면 이 공백 차이 때문에
    중복을 놓친다."""
    blocks = [
        _block(0, "dialog", "9 DONNA Ray Roy sure did a lot of damage."),
        _block(1, "dialog", "9 DONNA (CONT.)"),
        _block(2, "dialog", "9 DONNA (CONT.) But... look at all these clients we still have!"),
        _block(3, "dialog", "9 DONNA (CONT.)"),
        _block(4, "dialog", "9 DONNA (CONT.) ...look at all these clients we still have!"),
    ]
    groups, texts = group_utterances(blocks)
    assert len(groups) == 1
    merged = groups[0].merged_text
    assert groups[0].member_indices == [0, 1, 2, 3, 4]
    assert merged == (
        "9 DONNA Ray Roy sure did a lot of damage. "
        "But... look at all these clients we still have!"
    )
    assert merged.count("look at all these clients we still have") == 1
    assert texts == [merged]


def test_real_bill_dale_boomhauer_chain_no_annotation_fragment_and_partial_overlap():
    """재리뷰(라운드 1) 잔존 회귀 — GABE01_A1 p625-633 실물 그대로(9블록).
    두 번째 조각(p626)이 괄호 주석 하나 없이 곧장 다중 토큰 화자
    "BILL/DALE/BOOMHAUER" 뒤에 새 본문을 잇는 형태라, 화자 런 경계를 이
    조각 자신만으로 판단해야 한다(예전엔 이 경우 옛 lazy 경계로 대체
    동작해 "/DALE/BOOMHAUER" 파편이 남았다). 동시에 그 조각 자체가 이미
    누적된 문장을 그대로 반복한 뒤 새 내용을 잇는 부분 중첩이라(완전
    포함이 아니므로 예전 방식은 스킵하지 못하고 통째로 붙였다), 겹치는
    접두를 잘라내는 처리도 함께 검증한다. 이후 헤더-only 조각과 부분
    재인쇄 조각(p631·632)이 더 섞여 있다."""
    blocks = [
        _block(625, "dialog", "75 BILL/DALE/BOOMHAUER Oh my goodness./ No!/ Yeah man,"),
        _block(626, "dialog",
               "75 BILL/DALE/BOOMHAUER Oh my goodness./ No!/ Yeah man, you talkin’ "
               "‘bout dang ol’ (IMITATES HANK AGAIN, SHUDDERING) Thatherton."),
        _block(627, "dialog", "75 BILL/DALE/BOOMHAUER (Cont.)"),
        _block(628, "dialog", "75 BILL/DALE/BOOMHAUER (Cont.)"),
        _block(629, "dialog", "75 BILL/DALE/BOOMHAUER (Cont.)"),
        _block(630, "dialog", "75 BILL/DALE/BOOMHAUER (Cont.)"),
        _block(631, "dialog", "75 BILL/DALE/BOOMHAUER (Cont.) you talkin’ ‘bout dang ol’"),
        _block(632, "dialog",
               "75 BILL/DALE/BOOMHAUER (Cont.) (IMITATES HANK AGAIN, SHUDDERING) Thatherton."),
        _block(633, "dialog", "75 BILL/DALE/BOOMHAUER (Cont.)"),
    ]
    groups, texts = group_utterances(blocks)
    assert len(groups) == 1
    merged = groups[0].merged_text
    assert merged == (
        "75 BILL/DALE/BOOMHAUER Oh my goodness./ No!/ Yeah man, you talkin’ "
        "‘bout dang ol’ (IMITATES HANK AGAIN, SHUDDERING) Thatherton."
    )
    assert merged.count("/DALE/BOOMHAUER") == 1
    assert merged.count("Oh my goodness") == 1
    assert "(CONT" not in merged
    assert "(Cont" not in merged
    assert texts == [merged]


def test_real_bobby_chain_preserves_offscreen_annotation_and_leading_pronoun():
    """재리뷰(라운드 1) Minor 회귀 + 라운드 2에서 새로 드러난 결함 —
    GABE01_A1 p330-339 실물 그대로. 화자 런 뒤 곧바로 대문자 단독 단어
    "I"가 오는 조각("37 BOBBY I am...")에서 "I"를 화자명 일부로 오인해
    삼키면 안 되고("am..."만 남으면 안 됨), 그룹 첫 멤버에 없던 "(O.S.)"
    주석이 나중 조각에서 처음 등장하면 그 조각 고유의 정보이므로 보존해야
    한다(무차별적으로 모든 괄호 주석을 지우면 사라진다)."""
    blocks = [
        _block(330, "dialog", "37 BOBBY I need to say something."),
        _block(331, "dialog", "37 BOBBY (CONT.)"),
        _block(332, "dialog", "37 BOBBY (CONT.)"),
        _block(333, "dialog", "37 BOBBY (CONT.)"),
        _block(335, "dialog", "37 BOBBY I am..."),
        _block(336, "dialog", "37 BOBBY (CONT.) ...so..."),
        _block(337, "dialog", "37 BOBBY (CONT.) ...happy right now!"),
        _block(342, "dialog", "37 BOBBY (O.S.) This living arrangement..."),
    ]
    groups, texts = group_utterances(blocks)
    assert len(groups) == 1
    merged = groups[0].merged_text
    assert "I need to say something." in merged
    assert "I am..." in merged
    assert "(O.S.) This living arrangement..." in merged
    assert "(CONT" not in merged
    assert "(Cont" not in merged
    assert texts == [merged]


def test_no_annotation_continuation_preserves_leading_capitalized_word():
    """재리뷰 라운드 2 Minor #1 회귀 가드: (CONT.) 표시가 없는 조각에서
    화자 런 뒤 곧바로 오는 대문자 단독 단어(길이 2 이상)를 화자명 일부로
    오인해 삼키면 안 된다. `1cc55a9`(라운드 1)에서는 "12 HANK NO. I mean
    it."의 "NO."가 정상 보존됐는데, 라운드 2의 무조건 소비 재설계가 이를
    회귀시켰다 — 이 조각 자신에게도, 체인의 다른 멤버에게도 "NO." 뒤에
    괄호 주석이 없으므로 화자 런에 넣을 근거가 없다."""
    blocks = [
        _block(0, "dialog", "12 HANK Well, listen."),
        _block(1, "dialog", "12 HANK NO. I mean it."),
    ]
    groups, texts = group_utterances(blocks)
    assert len(groups) == 1
    merged = groups[0].merged_text
    assert merged == "12 HANK Well, listen. NO. I mean it."
    assert texts == [merged]


SAMPLES = os.environ.get("YESON_PDF_SAMPLES")


@pytest.fixture(scope="module")
def real_blocks():
    """GABE01_A1(1037페이지) 전체 추출 — 실물 테스트끼리 공유한다.

    추출은 전 페이지 순회 + 깨진 페이지의 OCR 복구(Task 20)까지 포함해
    수 분이 걸린다. 모듈 스코프로 한 번만 돌린다."""
    path = Path(SAMPLES) / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
    doc = open_pdf(path)
    try:
        profile = detect_profile(doc)
        assert profile is not None and profile.name == "storyboard"
        return profile.extract(doc)
    finally:
        doc.close()


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
def test_real_storyboard_utterance_grouping(real_blocks):
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
    blocks = real_blocks
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


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
@pytest.mark.parametrize("broken_page,orphan_pages,speaker,body", [
    # 0-based. 브리프의 p483/p542(1-based)에서 큐 헤더가 깨져 있었다:
    # `9= HANK 7Cont.8` / `=@ THATHERTON 7Cont.8` — 큐 파싱을 통과하지
    # 못해 체인이 끊기고, 뒤따르는 페이지가 헤더-only 그룹으로 남아
    # 재검증 산출물에서 `행크:(계속)` / `대더튼:(계속)`을 낳았다.
    (482, (483, 484), "HANK", "What are you doing here?"),
    (541, (542,), "THATHERTON", "I can’t hear you over the party bus"),
])
def test_real_corrupted_cue_no_longer_orphans_following_pages(
        real_blocks, broken_page, orphan_pages, speaker, body):
    """★Task 20의 연쇄 효과 — 깨진 큐 헤더를 복구하면 끊겼던 발화 체인이
    이어지고, 뒤따르던 헤더-only 그룹(`화자:(계속)`)이 사라진다.

    재검증에 남아 있던 진짜 결함 3건이 전부 이 형태였다. 여기서는 (1)
    깨진 페이지가 뒤 페이지들과 한 그룹으로 묶이고 (2) 그 그룹의 번역
    입력에 실제 대사 본문이 담기는지를 확인한다 — 헤더-only 그룹이었다면
    본문이 없었을 것이다."""
    dialog_pages = {b.page for b in real_blocks if b.kind == "dialog"}
    assert dialog_pages >= {broken_page, *orphan_pages}

    groups, _texts = group_utterances(real_blocks)
    owner = next(
        g for g in groups
        if any(real_blocks[i].page == broken_page
               and real_blocks[i].kind == "dialog" for i in g.member_indices))
    pages = {real_blocks[i].page for i in owner.member_indices}
    assert pages >= {broken_page, *orphan_pages}
    assert speaker in owner.merged_text.upper()
    assert body in owner.merged_text

    # 고아 페이지가 자기만의 헤더-only 그룹으로 따로 남아 있지 않아야 한다.
    for page in orphan_pages:
        solo = [g for g in groups
                if {real_blocks[i].page for i in g.member_indices} == {page}
                and real_blocks[g.member_indices[0]].kind == "dialog"]
        assert not solo, f"p{page}가 여전히 단독 dialog 그룹으로 남아 있다"
