from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from apps.server.domain.pdf_translate.backend import open_pdf
from apps.server.domain.pdf_translate.profiles import detect_profile
from apps.server.domain.pdf_translate.profiles.base import (
    PdfBlock,
    has_hangul,
    normalize_ws,
)
from apps.server.domain.pdf_translate.profiles.storyboard import StoryboardProfile


def _rects_intersect(a: tuple[float, float, float, float],
                     b: tuple[float, float, float, float]) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def _make_storyboard_pdf(tmp_path: Path, *, korean_dialog: bool = False) -> Path:
    """Storyboard Pro export를 흉내 낸 합성 페이지 — 가로 1008x612,
    'Dialog'/'Action Notes' 라벨 아래에 내용 블록."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((680, 460), "Dialog", fontsize=8)
    dialog = "이미 번역된 대사" if korean_dialog else "If\tyou\twanna\tgo, then go."
    # 한글은 기본 폰트(helv)에 글리프가 없어 언더파인드 플레이스홀더로
    # 치환되어 추출 시 실제 한글이 아니게 된다 — 내장 CJK 폰트로 렌더.
    if korean_dialog:
        page.insert_text((680, 478), dialog, fontsize=10, fontname="korea")
    else:
        page.insert_text((680, 478), dialog, fontsize=10)
    page.insert_text((72, 560), "Action Notes", fontsize=8)
    page.insert_text((72, 578), "HANK walks to the door.", fontsize=10)
    path = tmp_path / ("sb_ko.pdf" if korean_dialog else "sb.pdf")
    doc.save(path)
    doc.close()
    return path


def _make_storyboard_pdf_empty_dialog(tmp_path: Path) -> Path:
    """실물(GABE01) 패턴 회귀 가드: Dialog·Action Notes가 같은 x열에 쌓여
    있고(실물처럼 x0 동일) Dialog 필드가 비어 있을 때(라벨만, 내용 블록
    없음) — Bug A(라벨 오인식)·Bug B(dialog==action 중복) 재발 방지.
    브리프 원본 합성 fixture는 Dialog(x=680)와 Action Notes(x=72)를 서로
    다른 열에 둬서 x허용폭(60pt) 안에서 절대 후보가 되지 않는다 — 그래서
    실물에서 실제로 터진 버그를 전혀 검증하지 못한다. 이 fixture는 실물처럼
    같은 x열(72)에 쌓아 진짜로 후보 경합이 생기게 한다."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((72, 460), "Dialog", fontsize=8)
    # Dialog 내용 없음(의도) — 실물에서 빈 필드는 플레이스홀더 블록 자체가 생략됨
    page.insert_text((72, 560), "Action Notes", fontsize=8)
    page.insert_text((72, 578), "HANK walks to the door.", fontsize=10)
    path = tmp_path / "sb_empty_dialog.pdf"
    doc.save(path)
    doc.close()
    return path


def _make_storyboard_pdf_with_field_boxes(tmp_path: Path) -> Path:
    """실물(GABE01) 템플릿을 흉내 낸 합성 페이지 — 필드 박스 사각형까지
    그린다(좌표도 실물과 동일). 기존 _make_storyboard_pdf는 도형이 없어
    (get_drawings() == []) limit_y가 라벨 폴백으로만 정해진다.

    페이지 전체를 감싸는 테두리 사각형도 함께 그린다(리뷰 후속, Important
    1(a)) — `_field_box`의 "가장 작은 사각형이 이긴다" 규칙을 잠그는
    유일한 장치다. `page_rects`는 도형을 (y0, x0) 오름차순으로 정렬해
    돌려주므로(그리는 순서와 무관), y0가 가장 작은(=10.0) 이 테두리가
    항상 리스트 맨 앞에 온다 — "가장 작은 것이 이긴다"가 "첫 번째가
    이긴다"로 퇴화하면 이 테두리가 먼저 매치돼 상한이 잘못 나온다.
    Action Notes 라벨은 박스 안에서 Dialog 박스 하단(522.7)과 충분히
    떨어뜨려(y=550) 뒀다 — 붙여두면 "다음 라벨 y0 - GAP" 폴백값이 우연히
    522.7 근처로 나와(리뷰 시 뮤테이션 테스트로 발견: 원래 y=540에서는
    차이가 0.4pt뿐이라 관용 오차 안에 들어와 이 회귀를 못 잡았다)
    Important 1(b)의 min() 결합과 뒤섞여 이 테스트의 판별력을 가린다."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.draw_rect(fitz.Rect(10.0, 10.0, 998.0, 602.0),
                   color=(0, 0, 0), width=1)   # 페이지 테두리(가장 큼)
    page.draw_rect(fitz.Rect(24.0, 460.3, 985.1, 522.7),
                   color=(0, 0, 0), width=1)   # Dialog 박스
    page.draw_rect(fitz.Rect(24.0, 525.7, 985.1, 588.0),
                   color=(0, 0, 0), width=1)   # Action Notes 박스
    page.insert_text((27, 474), "Dialog", fontsize=12)
    page.insert_text((27, 492), "HANK walks in.", fontsize=10)
    page.insert_text((27, 550), "Action Notes", fontsize=12)
    page.insert_text((27, 568), "Bobby does the Salute.", fontsize=10)
    path = tmp_path / "sb_boxes.pdf"
    doc.save(path)
    doc.close()
    return path


def _make_storyboard_pdf_empty_dialog_merged_action(tmp_path: Path) -> Path:
    """리뷰어 실측 재현: Dialog 필드가 비어 있고 Action Notes가 라벨+내용이
    한 블록으로 붙어 나오는 변형(같은 x열) — Bug A 재발 가드."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((72, 460), "Dialog", fontsize=8)
    # Dialog 내용 없음(의도) + Action Notes 라벨·내용이 한 블록으로 병합
    page.insert_text((72, 560), "Action Notes: HANK walks to the door.", fontsize=10)
    path = tmp_path / "sb_empty_dialog_merged_action.pdf"
    doc.save(path)
    doc.close()
    return path


def test_extract_empty_dialog_field_produces_no_dialog_block(tmp_path):
    """빈 Dialog 필드 회귀 가드(Bug A/B) — 라벨만 있고 내용 블록이 없으면
    dialog kind 블록이 전혀 나오면 안 된다(라벨 오인식도, action 중복도 금지)."""
    doc = open_pdf(_make_storyboard_pdf_empty_dialog(tmp_path))
    try:
        blocks = StoryboardProfile().extract(doc)
        dialog_blocks = [b for b in blocks if b.kind == "dialog"]
        assert dialog_blocks == []  # 빈 필드 → dialog 블록 없음
        action = next(b for b in blocks if b.kind == "action")
        assert action.text == "HANK walks to the door."
        # (a)·(b)는 `blocks` 전체를 본다 — 2026-07-30 테스트 품질 스윕 전에는
        # 이미 빈 것으로 단언된 dialog_blocks를 순회해(`not any(<빈 리스트>)`)
        # 무조건 참이었다. 전체를 보면 실제로 깨질 수 있는 단언이 된다.
        # (a) 어떤 블록도 필드 라벨을 텍스트로 갖지 않는다
        assert not any(b.text.startswith("Action Notes") for b in blocks)
        # (b) action 텍스트가 어느 kind로도 중복 추출되지 않는다
        assert sum(1 for b in blocks if b.text == action.text) == 1
    finally:
        doc.close()


def test_extract_empty_dialog_with_merged_action_label_produces_no_dialog_block(
    tmp_path,
):
    """복합 재현(리뷰어 실측): Dialog 필드가 비어 있고 Action Notes가
    라벨+내용 한 블록으로 붙어 나오는 실물 변형 — dialog 오인식 없이
    action만 올바르게 추출되어야 한다."""
    doc = open_pdf(_make_storyboard_pdf_empty_dialog_merged_action(tmp_path))
    try:
        blocks = StoryboardProfile().extract(doc)
        dialog_blocks = [b for b in blocks if b.kind == "dialog"]
        assert dialog_blocks == []
        action = next(b for b in blocks if b.kind == "action")
        assert action.text == "HANK walks to the door."
    finally:
        doc.close()


def _make_storyboard_pdf_multi_block_fields(tmp_path: Path) -> Path:
    """다중 블록 필드 병합 회귀 가드(2026-07-30 GABE01_A1 실기 E2E 후속):
    Dialog 754페이지 중 45%가 화자 줄(`1 HANK`)과 대사가 별개 raw 블록으로
    나뉜다 — 최근접 1블록만 집어오면 실제 대사가 통째로 누락된다."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((72, 460), "Dialog", fontsize=8)
    page.insert_text((72, 478), "1 HANK", fontsize=10)
    page.insert_text((72, 496), "(SINGING) If you wanna cook out...", fontsize=10)
    page.insert_text((72, 560), "Action Notes", fontsize=8)
    page.insert_text((72, 578), "The rest join in.", fontsize=10)
    page.insert_text((72, 596), "CAM ADJUST", fontsize=10)
    path = tmp_path / "sb_multi_block.pdf"
    doc.save(path)
    doc.close()
    return path


def _make_storyboard_pdf_merged_label_with_extra_block(tmp_path: Path) -> Path:
    """라벨+내용이 한 블록으로 붙어 나오는 변형에서 그 아래 추가 후보
    블록까지 이어 병합해야 하는 실물 패턴(2026-07-30 E2E: Action Notes
    7%가 다중 블록이고, 첫 블록 아래 KO 주석이 두 번째 원문 블록과
    겹치는 사고 재현 — CAM ADJUST가 별개 블록으로 딸려 나온다)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((72, 460), "Dialog", fontsize=8)
    page.insert_text((72, 478), "If you wanna go, then go.", fontsize=10)
    page.insert_text((72, 560), "Action Notes: HANK walks to the door.",
                     fontsize=10)
    page.insert_text((72, 580), "CAM ADJUST", fontsize=10)
    path = tmp_path / "sb_merged_label_extra.pdf"
    doc.save(path)
    doc.close()
    return path


def test_extract_merges_all_candidate_blocks_in_field_window(tmp_path):
    """실기 E2E(2026-07-30, GABE01_A1) 후속 — 필드 창 안의 모든 후보
    블록을 y좌표 순으로 병합해야 한다(화자 줄+대사, 액션+CAM 지시)."""
    path = _make_storyboard_pdf_multi_block_fields(tmp_path)
    doc = open_pdf(path)
    try:
        raws = doc.raw_blocks(0)
        second_dialog_block = next(
            b for b in raws
            if normalize_ws(b.text) == "(SINGING) If you wanna cook out...")
        blocks = StoryboardProfile().extract(doc)
        dialog = next(b for b in blocks if b.kind == "dialog")
        action = next(b for b in blocks if b.kind == "action")
        assert dialog.text == "1 HANK (SINGING) If you wanna cook out..."
        assert action.text == "The rest join in. CAM ADJUST"
        # bbox는 병합된 원본 블록들의 union — y1이 두 번째 블록의 y1 이상
        assert dialog.bbox[3] >= second_dialog_block.bbox[3]
    finally:
        doc.close()


def test_extract_merged_label_block_continues_merging_lower_candidates(tmp_path):
    """라벨+내용 병합 블록 분기도 그 아래 창 안의 추가 후보를 이어
    병합해야 한다(현재는 remainder만 반환하던 회귀).

    크로스필드 누수 회귀 가드(리뷰어 실측 재현, 2026-07-30): 다음 필드
    라벨("Action Notes")이 내용과 한 블록으로 붙어 나오면 정확 일치 탐색
    만으로는 upper_bound를 못 찾아 Dialog 필드의 창이 무한정 열린다 —
    그러면 Action Notes 아래 "CAM ADJUST"까지 Dialog로 새어 들어가
    중복·오염된다. Dialog는 원래 내용 그대로여야 하고, Action은 여전히
    CAM ADJUST까지 병합돼야 한다."""
    doc = open_pdf(_make_storyboard_pdf_merged_label_with_extra_block(tmp_path))
    try:
        blocks = StoryboardProfile().extract(doc)
        dialog = next(b for b in blocks if b.kind == "dialog")
        action = next(b for b in blocks if b.kind == "action")
        assert dialog.text == "If you wanna go, then go."  # 누수·중복 없음
        assert action.text == "HANK walks to the door. CAM ADJUST"
    finally:
        doc.close()


def _make_storyboard_pdf_action_slugline(tmp_path: Path) -> Path:
    """실물 패턴(Task 19, 사람 납품본 실측 — pairs_all.jsonl page=2 action:
    human_ko가 슬러그라인 뒤를 "\\r"로 분리해 별도 줄 취급): action 필드의
    첫 조각이 슬러그라인(INT./EXT.)이고 뒤에 추가 조각이 있는 다중 블록
    페이지."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((72, 460), "Dialog", fontsize=8)
    page.insert_text((72, 478), "If you wanna go, then go.", fontsize=10)
    page.insert_text((72, 560), "Action Notes", fontsize=8)
    page.insert_text((72, 578),
                     "INT. STRICKLAND PROPANE - SALES FLOOR - MORNING",
                     fontsize=10)
    page.insert_text((72, 596), "Hank walks to the door.", fontsize=10)
    path = tmp_path / "sb_action_slugline.pdf"
    doc.save(path)
    doc.close()
    return path


def test_extract_joins_slugline_first_piece_with_newline_for_action(tmp_path):
    """Task 19: action 필드 병합 시 첫 조각이 슬러그라인(INT./EXT.)이면
    뒤 조각과 "\\n"으로 잇는다(사람 납품본 관례 — pairs_all.jsonl page=2
    action의 human_ko가 "\\r"로 슬러그라인 뒤를 분리). dialog 필드는
    영향받지 않는다(is_action 한정)."""
    doc = open_pdf(_make_storyboard_pdf_action_slugline(tmp_path))
    try:
        blocks = StoryboardProfile().extract(doc)
        action = next(b for b in blocks if b.kind == "action")
        assert action.text == (
            "INT. STRICKLAND PROPANE - SALES FLOOR - MORNING\n"
            "Hank walks to the door.")
        dialog = next(b for b in blocks if b.kind == "dialog")
        assert "\n" not in dialog.text
    finally:
        doc.close()


def test_extract_non_slugline_first_piece_still_joins_with_space(tmp_path):
    """회귀 가드: 첫 조각이 슬러그라인이 아니면 기존처럼 공백으로 이어
    붙여야 한다(슬러그라인 분기 추가가 일반 다중 블록 병합을 깨지 않았는지
    명시적으로 잠근다)."""
    path = _make_storyboard_pdf_multi_block_fields(tmp_path)
    doc = open_pdf(path)
    try:
        blocks = StoryboardProfile().extract(doc)
        action = next(b for b in blocks if b.kind == "action")
        assert action.text == "The rest join in. CAM ADJUST"
        assert "\n" not in action.text
    finally:
        doc.close()


def test_looks_like_slugline_recognizes_both_forms():
    """슬러그라인 두 형태(INT./EXT. 접두, 또는 접두 없이 전부 대문자+
    하이픈) 모두 인식하고, 일반 액션/지시문은 아니라고 판정해야 한다."""
    from apps.server.domain.pdf_translate.profiles.storyboard import (
        _looks_like_slugline,
    )
    assert _looks_like_slugline("INT. STRICKLAND PROPANE - SALES FLOOR - MORNING")
    assert _looks_like_slugline("STRICKLAND PROPANE - SALES FLOOR")
    assert not _looks_like_slugline("Hank walks to the door.")
    assert not _looks_like_slugline("CAM ADJUST")  # 하이픈 없음


def test_helpers():
    assert has_hangul("씬 내내") is True
    assert has_hangul("If you wanna") is False
    assert normalize_ws("If\tyou\twanna  go\n") == "If you wanna go"


def test_detect_and_extract_storyboard(tmp_path):
    doc = open_pdf(_make_storyboard_pdf(tmp_path))
    try:
        profile = detect_profile(doc)
        assert profile is not None and profile.name == "storyboard"
        blocks = profile.extract(doc)
        kinds = {b.kind for b in blocks}
        assert kinds == {"dialog", "action"}
        dialog = next(b for b in blocks if b.kind == "dialog")
        assert dialog.text == "If you wanna go, then go."  # 탭 정규화
        assert dialog.page == 0
    finally:
        doc.close()


def test_extract_skips_hangul_blocks(tmp_path):
    doc = open_pdf(_make_storyboard_pdf(tmp_path, korean_dialog=True))
    try:
        blocks = StoryboardProfile().extract(doc)
        assert all(b.kind != "dialog" for b in blocks)  # 한글 대사는 제외
    finally:
        doc.close()


def test_place_returns_rect_within_page_and_not_intersecting_source(tmp_path):
    """이 fixture의 Dialog 블록은 다음 라벨(Action Notes)이 상한이 되어
    아래 여유가 충분하므로 2026-07-31 배치 규칙상 **아래** 배치가 선택된다.
    경로와 무관한 불변식만 검증한다(원문 비교차 + 페이지 안 + 12pt)."""
    doc = open_pdf(_make_storyboard_pdf(tmp_path))
    try:
        profile = StoryboardProfile()
        block = next(b for b in profile.extract(doc) if b.kind == "dialog")
        ov = profile.place(block, "가고 싶다면 가세요", doc.page_size(0))
        _x0, _y0, x1, y1 = ov.rect
        assert not _rects_intersect(ov.rect, block.bbox)
        assert y1 <= 612 and x1 <= 1008     # 페이지 안
        assert ov.page == 0 and ov.fontsize == 12.0
    finally:
        doc.close()


def test_place_prefers_right_side_when_room_available():
    """오른쪽 여유폭이 충분(>= 180pt)하면 원문 옆(오른쪽)에 y 정렬로 배치한다
    (2026-07-30 실기 피드백: 하단 시프트가 원문을 덮던 문제의 대안 배치)."""
    block = PdfBlock(page=0, kind="dialog", text="If you wanna go, then go.",
                     bbox=(72.0, 400.0, 300.0, 420.0))
    ov = StoryboardProfile().place(block, "가고 싶다면 가세요", (1008.0, 612.0))
    assert ov.rect[0] >= block.bbox[2]       # 원문 오른쪽
    assert ov.rect[1] == block.bbox[1]       # 원문 첫 줄과 y 정렬
    assert not _rects_intersect(ov.rect, block.bbox)
    assert ov.fontsize == 12.0


def test_place_reserves_extra_height_for_embedded_newline():
    """리뷰 후속(Task 19 round 1, Important 1): `_estimate_height`가 개행을
    그냥 1글자로 세어 실제 렌더 줄 수를 과소 추정하던 결함 — place()가
    실제로 만드는 rect 높이가 개행 있는 텍스트에서 더 커야 한다(같은 총
    글자 수, 폰트 크기도 동일하게 유지되는 기하에서 개행 유무만 다름).
    이전에는 코드베이스 전체에 place()가 개행 텍스트를 받은 적이 없었다
    (슬러그라인 테스트는 extract() 출력만, 문서 전체 배치 불변식 루프는
    "가"*n만 사용) — 이 테스트가 그 경로를 처음으로 실행한다."""
    block = PdfBlock(page=0, kind="action", text="a" * 40,
                     bbox=(72.0, 100.0, 130.0, 120.0))
    page_size = (1008.0, 612.0)
    flat_ko = "가" * 50
    newline_ko = ("가" * 25) + "\n" + ("가" * 25)  # 같은 총 글자 수, 개행 1개
    profile = StoryboardProfile()
    ov_flat = profile.place(block, flat_ko, page_size)
    ov_newline = profile.place(block, newline_ko, page_size)
    assert ov_flat.fontsize == ov_newline.fontsize == 12.0  # 폰트 축소 미개입
    flat_height = ov_flat.rect[3] - ov_flat.rect[1]
    newline_height = ov_newline.rect[3] - ov_newline.rect[1]
    assert newline_height > flat_height
    assert not _rects_intersect(ov_newline.rect, block.bbox)


def test_place_falls_back_below_when_right_side_too_narrow():
    """블록이 페이지 오른쪽 끝까지 거의 차지해 오른쪽 여유가 180pt 미만이면
    기존처럼 블록 아래에 배치한다."""
    block = PdfBlock(page=0, kind="action", text="HANK walks to the door.",
                     bbox=(72.0, 400.0, 900.0, 420.0))
    ov = StoryboardProfile().place(block, "행크가 문으로 걸어간다", (1008.0, 612.0))
    assert ov.rect[1] >= block.bbox[3]       # 원문 아래
    assert not _rects_intersect(ov.rect, block.bbox)
    assert ov.fontsize == 12.0


def test_place_shrinks_font_near_bottom_instead_of_shifting_up(caplog):
    """아래 경로: 페이지 하단 근접 + 오른쪽 협소 → 위로 밀어 원문을 덮던
    옛 로직 대신 폰트를 축소해 페이지 하단(page_h - 4) 안에 맞춘다. 원문과
    비교차 유지(2026-07-30 리뷰 Finding 2: 이 경로는 밀지 않는다 — 그게
    원래 버그였다 — 대신 8pt에서도 못 맞으면 경고 로그를 남긴다)."""
    long_ko = "가나다라마바사아자차카타파하" * 40  # 축소 없이는 못 담을 분량
    block = PdfBlock(page=0, kind="action", text="a" * 200,
                     bbox=(72.0, 590.0, 900.0, 600.0))
    with caplog.at_level("WARNING", logger="yeson.pdf.profiles.storyboard"):
        ov = StoryboardProfile().place(block, long_ko, (1008.0, 612.0))
    assert ov.fontsize < 12.0
    assert not _rects_intersect(ov.rect, block.bbox)
    assert ov.rect[3] <= 612.0 - 4.0
    assert any("clip" in r.message for r in caplog.records)  # Finding 2(b)


def test_place_right_side_shift_up_recovers_full_legibility():
    """오른쪽 경로: x축이 이미 원문과 분리돼 있어(x0 = block.x1 + 8) y를
    얼마든 움직여도 교차 위험이 없다 — 8pt에서도 못 맞으면 위로 밀어 올려
    실제로 텍스트 전체가 들어가야 한다(2026-07-30 리뷰 Finding 2: 아래
    경로와 달리 오른쪽 경로는 시프트-업을 복원한다). 좁고 짧은(우측 여유
    충분) 블록을 페이지 하단 근처에 둬서, 시프트 없이는 못 담을 양의
    텍스트로도 결국 다 들어가야 함을 검증."""
    from apps.server.domain.pdf_translate.profiles.storyboard import (
        _estimate_height,
    )

    long_ko = "가나다라마바사아자차카타파하" * 40
    block = PdfBlock(page=0, kind="dialog", text="a" * 200,
                     bbox=(72.0, 590.0, 130.0, 600.0))  # 오른쪽 여유 충분
    page_size = (1008.0, 612.0)
    ov = StoryboardProfile().place(block, long_ko, page_size)
    assert ov.rect[0] >= block.bbox[2]  # 여전히 원문 오른쪽
    assert not _rects_intersect(ov.rect, block.bbox)
    assert ov.rect[1] < block.bbox[1]  # 실제로 위로 밀렸다(y0가 원문보다 위)
    available = ov.rect[3] - ov.rect[1]
    needed = _estimate_height(long_ko, ov.rect[2] - ov.rect[0], ov.fontsize)
    assert available >= needed - 0.5  # 잘리지 않고 전부 들어감


def test_place_bottom_edge_block_returns_nondegenerate_onpage_rect():
    """Finding 1(critical) 재현: 원문 블록이 페이지 맨 끝에 거의 붙어(우측
    여유도 없음) 있으면 y0(=block.y1 + GAP)가 페이지 하단 안전 마진(그리고
    심지어 물리적 페이지 자체)을 넘어선다. 예전 코드는 rect (x, 613, x,
    613)처럼 y1==y0인 퇴화 rect를 반환해 PyMuPDF add_freetext_annot이
    'rect is infinite or empty'로 터졌다(리뷰어 실측 재현) — 반드시
    유효한(양의 높이) 온페이지 rect를 반환해야 한다."""
    block = PdfBlock(page=0, kind="action", text="a" * 100,
                     bbox=(72.0, 600.0, 900.0, 609.0))
    page_size = (1008.0, 612.0)
    ov = StoryboardProfile().place(block, "행크가 문으로 걸어간다", page_size)
    x0, y0, x1, y1 = ov.rect
    assert y1 > y0  # 퇴화 아님
    assert x1 > x0
    assert 0.0 <= y0 and y1 <= page_size[1]  # 페이지 안


def test_place_below_when_field_box_has_room():
    """필드 박스 안 원문 아래에 여유가 있으면 좁은 우측 칸 대신 아래 전폭
    12pt로 놓는다(사람 납품본 관례 — GABE01 373p 실측: 원문
    (27.0, 546.7, 790.3, 557.8), 박스 하단 588.0, 사람은 (27.4, 557.1)).
    원문 오른쪽 여유가 _MIN_RIGHT_WIDTH를 넘어도 아래가 우선이다.

    폭은 원문 자체의 x1이 아니라 **필드 박스 우측**까지 전폭이어야 한다
    (설계 §6.1, 2026-07-31 리뷰 Finding — 원문 x1로 좁히면 원문이 짧아도
    번역 폭이 넓어지지 않아 불필요하게 줄바꿈/축소가 일어난다)."""
    block = PdfBlock(page=0, kind="action",
                     text="Bobby does the Three Amigos Salute.",
                     bbox=(27.0, 546.7, 790.3, 557.8),
                     limit_y=588.0, limit_x1=985.1)
    ov = StoryboardProfile().place(block, "바비는 오른손을 가슴에 얹는다.",
                                   (1008.0, 612.0))
    assert ov.rect[1] >= block.bbox[3]      # 원문 아래
    assert ov.rect[0] == block.bbox[0]      # 원문과 같은 좌측 정렬
    assert ov.rect[2] == pytest.approx(block.limit_x1 - 8.0)  # 박스 우측까지 전폭
    assert ov.rect[3] <= 588.0              # 필드 박스 안
    assert ov.fontsize == 12.0
    assert not _rects_intersect(ov.rect, block.bbox)


def test_place_below_uses_full_box_width_so_12pt_fits_real_sentence():
    """실물 GABE01 373p 회귀 가드(2026-07-31 리뷰 Finding): 아래 배치 폭을
    원문 자체의 폭 763.3pt(= 790.3 − 27.0)로 좁히면 이 실제 문장이 12pt에서
    2줄(37.5pt)이 필요해 여유(26.2pt)를 넘기고 10pt로 축소됐다 — 사람은
    같은 자리에 12pt 한 줄로 썼다. 폭을 필드 박스까지의 폭 950.1pt
    (= 977.1 − 27.0)로 쓰면 1줄(22.5pt)에 들어가 12pt를 유지한다."""
    block = PdfBlock(page=0, kind="action",
                     text="Bobby does the Three Amigos Salute. Joseph does too.",
                     bbox=(27.0, 546.7, 790.3, 557.8),
                     limit_y=588.0, limit_x1=985.1)
    ko = ("바비는 오른손을 가슴에 얹고 왼손을 반대쪽 가슴에 댄 다음 "
          "엉덩이를 앞으로 튕겨\"삼총사 경례\"를 한다.죠셉도 그와 같이 한다.")
    ov = StoryboardProfile().place(block, ko, (1008.0, 612.0))
    assert ov.fontsize == 12.0
    assert ov.rect[1] >= block.bbox[3]      # 원문 아래
    assert ov.rect[3] <= 588.0              # 필드 박스 안
    assert not _rects_intersect(ov.rect, block.bbox)


def test_place_below_shrinks_to_10pt_when_12pt_does_not_fit():
    """아래 여유가 12pt엔 모자라고 10pt엔 충분하면 아래 배치를 유지하되
    10pt로 줄인다(사다리는 10pt에서 끊는다).

    폭이 박스까지의 폭 950.1pt(= 977.1 − 27.0)로 넓어졌으므로(위 회귀 가드와 동일 이유),
    `limit_y`를 좁혀 12pt 2줄(37.5pt)은 못 들어가고 10pt 2줄(31.25pt)만
    들어가는 여유(35.0pt)로 맞췄다 — 넓어진 폭 때문에 이전 값(여유 44.0pt)은
    12pt로도 통과해버려 이 테스트가 더 이상 10pt 단을 검증하지 못했다."""
    block = PdfBlock(page=0, kind="action", text="a" * 60,
                     bbox=(27.0, 500.0, 790.3, 512.0),
                     limit_y=551.0, limit_x1=985.1)
    ov = StoryboardProfile().place(block, "가" * 130, (1008.0, 612.0))
    assert ov.fontsize == 10.0
    assert ov.rect[1] >= block.bbox[3]
    assert ov.rect[3] <= 551.0


def test_place_below_clamps_right_edge_to_page_width_even_with_wide_limit_x1():
    """필드 박스가 보고하는 limit_x1이 페이지 폭을 넘어서도(예: OCR/도형
    오차) 아래 배치 rect는 반드시 페이지 폭 한도(page_w - 8) 안에 있어야
    한다 — limit_x1을 그대로 우측 경계로 쓰면 페이지 밖으로 나갈 수 있다
    (Task 1 리뷰 후속: page_w 클램프가 살아있는지 잠그는 회귀 가드)."""
    block = PdfBlock(page=0, kind="action", text="a" * 20,
                     bbox=(900.0, 400.0, 990.0, 412.0),
                     limit_y=440.0, limit_x1=1200.0)  # limit_x1 > page_w
    ov = StoryboardProfile().place(block, "안녕하세요", (1008.0, 612.0))
    assert ov.rect[2] <= 1008.0 - 8.0
    assert not _rects_intersect(ov.rect, block.bbox)


def test_place_below_clamps_limit_y_to_page_height():
    """`_place_below_in_box`의 `min(block.limit_y, page_h - 4.0)` 클램프
    잠금(리뷰 후속, Minor 2) — 이 클램프를 지워도 기존 테스트는 하나도
    실패하지 않아 무방비 상태였다. limit_y(700.0)가 페이지 높이(612)를
    넘는 도형 오차 케이스에서, 클램프가 살아있으면 여유(192pt)가 부족해
    10pt로 내려가 페이지 안(597.25 ≤ 608)에 들어가지만, 클램프를 지우면
    여유가 284pt로 늘어나 12pt가 통과해버려(247.5pt) rect 하단이 페이지
    하단 안전 마진(page_h - 4 = 608)을 넘는다."""
    block = PdfBlock(page=0, kind="action", text="a" * 40,
                     bbox=(72.0, 400.0, 300.0, 412.0),
                     limit_y=700.0, limit_x1=985.1)
    ov = StoryboardProfile().place(block, "가" * 1200, (1008.0, 612.0))
    assert ov.rect[1] >= block.bbox[3]          # 아래 배치 유지
    assert ov.rect[3] <= 612.0 - 4.0            # 페이지 하단 안전 마진 안
    assert not _rects_intersect(ov.rect, block.bbox)


def test_place_falls_back_to_right_when_box_has_no_room_below():
    """박스 아래 여유가 10pt로도 부족하면 기존 우측 경로로 폴백한다
    (실물 Dialog 필드처럼 원문이 박스를 꽉 채운 경우 — 21p 실측:
    원문 하단 516.5, 박스 하단 522.7 → 여유 2.2pt)."""
    block = PdfBlock(page=0, kind="dialog", text="a" * 40,
                     bbox=(27.0, 481.3, 300.0, 516.5),
                     limit_y=522.7, limit_x1=985.1)
    ov = StoryboardProfile().place(
        block, "행크:(노래하며) 밖에서 요리를 하고 싶다면", (1008.0, 612.0))
    assert ov.rect[0] >= block.bbox[2]      # 원문 오른쪽
    assert ov.rect[1] == block.bbox[1]      # 우측 경로의 y 정렬
    assert not _rects_intersect(ov.rect, block.bbox)


def test_place_right_path_stops_at_field_box_right_edge():
    """아래 자리가 없어 **우측 경로**로 떨어져도 오른쪽 끝은 필드 박스
    우측(limit_x1 - 8)이지 페이지 끝이 아니다.

    실물(FL102 3단 p36) 재현: 2열 대사가 박스를 꽉 채워 아래가 막히자
    주석이 `page_w - 8`(=1000.0)까지 뻗어 3열 판넬을 가로질렀다 — 사람은
    같은 자리를 열 안(x1=535.8)에 뒀다. GABE01(1단) 표본 실측으로 이
    상한이 폰트/클리핑에 주는 영향은 0건임을 확인했다."""
    block = PdfBlock(page=0, kind="dialog", text="A tall block.",
                     bbox=(354.4, 294.3, 415.6, 416.4),
                     limit_y=422.1, limit_x1=657.8)
    ov = StoryboardProfile().place(block, "파티마:루이스 화이팅!", (1008.0, 612.0))
    assert ov.rect[0] >= block.bbox[2]        # 우측 경로를 탔다
    assert ov.rect[2] <= 657.8 - 8.0 + 0.01   # 열 밖으로 안 나간다


def test_place_right_path_still_reaches_page_edge_without_limit_x1():
    """limit_x1이 없으면(도형 없는 문서) 우측 끝은 예전대로 page_w - 8 —
    상한 도입이 기존 경로를 조용히 좁히지 않는다는 하위호환 잠금."""
    block = PdfBlock(page=0, kind="dialog", text="A tall block.",
                     bbox=(354.4, 294.3, 415.6, 416.4))
    ov = StoryboardProfile().place(block, "파티마:루이스 화이팅!", (1008.0, 612.0))
    assert ov.rect[2] == pytest.approx(1000.0, abs=0.01)


def test_place_without_limit_y_keeps_legacy_right_placement():
    """limit_y를 모르면(도형도 다음 라벨도 없는 PDF) 판단 근거가 없으므로
    기존 배치 규칙 그대로 — 상한 없이 아래로 놓으면 박스를 넘어 다음 필드를
    침범한다. 하위호환 회귀 잠금."""
    block = PdfBlock(page=0, kind="dialog", text="If you wanna go, then go.",
                     bbox=(72.0, 400.0, 300.0, 420.0))
    assert block.limit_y is None
    ov = StoryboardProfile().place(block, "가고 싶다면 가세요", (1008.0, 612.0))
    assert ov.rect[0] >= block.bbox[2]      # 우측(기존 규칙)
    assert ov.rect[1] == block.bbox[1]
    assert ov.fontsize == 12.0


def test_extract_sets_limit_from_field_box_rectangle(tmp_path):
    """도형이 있는 페이지: 필드 블록의 limit_y/limit_x1이 그 블록을 감싸는
    필드 박스에서 온다(Dialog 박스 하단 522.7 / Action Notes 588.0).

    이 fixture는 필드 박스 둘을 감싸는 페이지 테두리 사각형도 갖고 있다
    (`_field_box`의 "가장 작은 것이 이긴다" 규칙 잠금, 리뷰 후속 Important
    1(a)) — dialog.limit_y가 여전히 522.7(Dialog 박스 하단)이어야
    테두리(가장 큰 사각형)를 잘못 고르지 않았다는 뜻이다. "첫 번째가
    이긴다"로 되돌리면 이 단언이 실패한다(뮤테이션 테스트로 확인, 아래
    커밋 보고서 참조)."""
    doc = open_pdf(_make_storyboard_pdf_with_field_boxes(tmp_path))
    try:
        blocks = StoryboardProfile().extract(doc)
        dialog = next(b for b in blocks if b.kind == "dialog")
        action = next(b for b in blocks if b.kind == "action")
        assert dialog.limit_y == pytest.approx(522.7, abs=1.0)
        assert action.limit_y == pytest.approx(588.0, abs=1.0)
        assert dialog.limit_x1 == pytest.approx(985.1, abs=1.0)
    finally:
        doc.close()


def test_extract_falls_back_to_next_label_when_no_drawings(tmp_path):
    """도형이 없는 PDF: Dialog는 다음 라벨(Action Notes) y0 - _GAP를 상한으로
    받고, 마지막 필드(Action Notes)는 근거가 없어 None으로 남는다
    (= 그 필드는 기존 우측 배치 그대로)."""
    doc = open_pdf(_make_storyboard_pdf(tmp_path))
    try:
        blocks = StoryboardProfile().extract(doc)
        dialog = next(b for b in blocks if b.kind == "dialog")
        action = next(b for b in blocks if b.kind == "action")
        assert dialog.limit_y is not None and dialog.limit_y < 551.4
        assert dialog.limit_x1 is None
        assert action.limit_y is None
    finally:
        doc.close()


def _make_storyboard_pdf_with_panel_label(tmp_path: Path) -> Path:
    """필드(Dialog/Action Notes) + 패널 영역(y 95~455) 안의 빨간 콜아웃
    라벨(사각 테두리 + 빨간 글자, panel_ocr.py 프로토타입 실증과 동일
    구성) — 합성 통합 테스트용(Task 14)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.draw_line((100, 150), (400, 300), color=(0, 0, 0), width=1.5)
    rect = fitz.Rect(300, 200, 500, 240)
    page.draw_rect(rect, color=(1, 0, 0), width=2)
    page.insert_text((rect.x0 + 10, rect.y0 + 28), "HANK'S TRUCK", fontsize=14,
                     color=(1, 0, 0))
    page.insert_text((72, 460), "Dialog", fontsize=8)
    page.insert_text((72, 478), "If you wanna go, then go.", fontsize=10)
    page.insert_text((72, 560), "Action Notes", fontsize=8)
    page.insert_text((72, 578), "HANK walks to the door.", fontsize=10)
    path = tmp_path / "sb_panel_label.pdf"
    doc.save(path)
    doc.close()
    return path


def test_extract_includes_panel_label_and_place_avoids_intersection(tmp_path):
    """합성 통합(Task 14): 필드 + 빨간 패널 라벨 페이지 → extract에
    panel_label kind 포함, place()가 라벨 위(또는 우측) + 원문 비교차."""
    doc = open_pdf(_make_storyboard_pdf_with_panel_label(tmp_path))
    try:
        profile = StoryboardProfile()
        blocks = profile.extract(doc)
        panel_blocks = [b for b in blocks if b.kind == "panel_label"]
        assert len(panel_blocks) == 1
        assert "HANK" in panel_blocks[0].text.upper()
        assert "TRUCK" in panel_blocks[0].text.upper()
        ov = profile.place(panel_blocks[0], "행크의 트럭", doc.page_size(0))
        assert not _rects_intersect(ov.rect, panel_blocks[0].bbox)
        x0, y0, x1, y1 = ov.rect
        assert 0.0 <= x0 and x1 <= 1008.0 and 0.0 <= y0 and y1 <= 612.0
        # 다른 필드(dialog/action) 배치와도 교차하지 않아야 함(비교차 불변식)
        for other in blocks:
            if other is panel_blocks[0]:
                continue
            other_ov = profile.place(other, "가" * 10, doc.page_size(0))
            assert not _rects_intersect(ov.rect, other.bbox)
            assert not _rects_intersect(other_ov.rect, panel_blocks[0].bbox)
    finally:
        doc.close()


def _make_three_panel_pdf(tmp_path: Path) -> Path:
    """3단 템플릿의 기하 그대로 — 판넬 칸 3개(래스터 이미지)와 그림 속 작은
    로고 이미지 하나. 좌표는 실물 실측값이다(FL102·FL104 3단이 전 페이지
    동일: 302.1×168.3pt 3칸)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 629, 354), False)
    pix.clear_with(220)
    for x0 in (38.1, 353.5, 668.9):
        page.insert_image(fitz.Rect(x0, 110.9, x0 + 302.1, 279.2), pixmap=pix)
    logo = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
    logo.clear_with(120)
    page.insert_image(fitz.Rect(120.0, 130.0, 160.0, 170.0), pixmap=logo)
    page.insert_text((72, 460), "Dialog", fontsize=8)
    page.insert_text((72, 478), "If you wanna go, then go.", fontsize=10)
    path = tmp_path / "sb_three_panel.pdf"
    doc.save(path)
    doc.close()
    return path


def test_panel_subregions_reads_panel_boxes_from_the_document(tmp_path):
    """판넬 칸 좌표는 상수로 박지 않고 **문서에서** 읽는다 — Storyboard Pro가
    칸 하나를 이미지 하나로 굽는 관례를 그대로 쓴다. 그림 속 작은 로고
    이미지는 넓이 문턱에서 제외돼 OCR 호출을 늘리지 않는다."""
    from apps.server.domain.pdf_translate.profiles.storyboard import (
        _panel_region,
        _panel_subregions,
    )
    doc = open_pdf(_make_three_panel_pdf(tmp_path))
    try:
        raws = doc.raw_blocks(0)
        page_w, _page_h = doc.page_size(0)
        region = _panel_region(raws, page_w)
        subs = _panel_subregions(doc, 0, region)
        assert len(subs) == 3, subs
        # 여유 2pt: MuPDF가 이미지 원본 종횡비를 지켜 배치하느라 지정 사각형
        # 안에서 폭을 조금 줄인다(실물 익스포트는 종횡비가 맞아 그대로 들어간다).
        assert subs[0] == pytest.approx((38.1, 110.9, 340.2, 279.2), abs=2.0)
        # 영역 밖으로 나가는 부분은 잘린다(칸이 필드 박스를 삼키지 않게).
        tight = _panel_subregions(doc, 0, (0.0, 95.0, page_w, 200.0))
        assert all(s[3] == pytest.approx(200.0) for s in tight), tight
    finally:
        doc.close()


def _make_storyboard_title_page_pdf(tmp_path: Path) -> Path:
    """표지/타이틀 페이지 흉내 — 빨간 큰 로고/타이틀 텍스트만 있고
    Dialog/Action Notes 필드 라벨은 전혀 없다(리뷰 후속 회귀 가드: 실물
    GABE01_A1 page 0에서 쇼 로고 텍스트 "KING"/"HILL"이 패널 라벨로
    오인식되던 문제의 재현)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((300, 200), "KING", fontsize=40, color=(1, 0, 0))
    page.insert_text((300, 260), "HILL", fontsize=40, color=(1, 0, 0))
    path = tmp_path / "sb_title_page.pdf"
    doc.save(path)
    doc.close()
    return path


def test_extract_skips_panel_ocr_on_pages_without_field_labels(tmp_path, monkeypatch):
    """리뷰 후속 회귀 가드: 표지/타이틀 페이지(Dialog/Action Notes 라벨
    없음)는 빨간 텍스트가 있어도 패널 OCR 자체를 건너뛴다 — panel_label
    블록이 0개여야 하고, OCR 엔진(RapidOCR)은 아예 생성되지 않아야 한다
    (엔진 생성 스파이로 확인 — find_panel_labels 자체의 프리필터 스파이
    테스트와 동일 기법을 extract() 호출부에 적용)."""
    from apps.server.domain.pdf_translate import panel_ocr

    calls: list[dict] = []
    monkeypatch.setattr(panel_ocr, "_new_engine",
                        lambda **kw: calls.append(kw))
    panel_ocr._reset_engines()
    doc = open_pdf(_make_storyboard_title_page_pdf(tmp_path))
    try:
        blocks = StoryboardProfile().extract(doc)
        assert [b for b in blocks if b.kind == "panel_label"] == []
        assert calls == []
    finally:
        doc.close()
        panel_ocr._reset_engines()


def _make_storyboard_artwork_only_page_pdf(tmp_path: Path) -> Path:
    """대사도 액션노트도 없는 **순수 그림 페이지** — 씬 테이블 헤더는 있고
    Dialog/Action Notes 라벨은 없다(Storyboard Pro가 빈 필드를 통째로
    생략하기 때문). FL104_FNL_Nrev 실측에서 209페이지 중 34장이 이 모양이고,
    사람은 그 34장 전부에 판넬 안 콜아웃을 달았다."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    # 씬 테이블 헤더(실물 y≈73 — _PANEL_Y_TOP=95 위)
    page.insert_text((39, 80), "Scene", fontsize=12)
    page.insert_text((192, 80), "Panel", fontsize=12)
    page.insert_text((172, 96), "17", fontsize=12)
    # 판넬 영역의 빨간 콜아웃(사람이 번역하는 대상)
    page.insert_text((300, 250), "ZOMBIE", fontsize=10, color=(1, 0, 0))
    path = tmp_path / "sb_artwork_only.pdf"
    doc.save(path)
    doc.close()
    return path


def test_extract_runs_panel_ocr_on_artwork_only_pages_with_scene_header(
        tmp_path, monkeypatch):
    """FL104 실측 회귀(2026-08-03): 필드 라벨이 없어도 씬 테이블 헤더가 있는
    판넬 페이지는 패널 OCR을 **돌려야** 한다.

    예전 게이트(_has_field_label)는 이런 순수 그림 페이지를 표지로 오인해
    OCR을 통째로 건너뛰었고, FL104 209페이지에서 34장이 한 글자도 번역되지
    않았다(사람이 단 주석 97개 누락 = 전체 격차 246개의 39%).

    OCR 결과 자체가 아니라 **게이트가 열렸는지**(엔진 생성)를 본다 — 위
    표지 테스트와 같은 기법이고, 합성 PDF에 대한 RapidOCR 판독 결과에
    의존하지 않아 결정적이다."""
    from apps.server.domain.pdf_translate import panel_ocr

    calls: list[dict] = []
    monkeypatch.setattr(panel_ocr, "_new_engine", lambda **kw: calls.append(kw))
    panel_ocr._reset_engines()
    doc = open_pdf(_make_storyboard_artwork_only_page_pdf(tmp_path))
    try:
        StoryboardProfile().extract(doc)
        assert calls, ("씬 테이블 헤더가 있는 순수 그림 페이지에서 패널 OCR "
                       "게이트가 열리지 않았다 (FL104 34페이지 누락 회귀)")
    finally:
        doc.close()
        panel_ocr._reset_engines()


def test_place_below_fallback_never_crosses_field_box_bottom():
    """FL104 p2 실물 회귀(사용자 스크린샷, 2026-08-03): 대사 주석이 자기 필드
    박스를 넘어 **다음 필드(Action Notes) 박스 위에 겹쳐 찍히던** 문제.

    `_place_below_in_box`가 limit_y 안에 못 넣어 폴백하면, 폴백 경로가
    페이지 하단만 보고 상한 없이 아래로 뻗었다. 잘리는 건 허용하되(이 파일의
    '잘리더라도 원문 비침범이 우선' 원칙) 박스는 넘지 않아야 한다."""
    limit_y = 410.7
    block = PdfBlock(page=0, kind="dialog",
                     text="238 FEMALE SPRING BREAKER #1/FEMALE SPRING BREAKER #2",
                     bbox=(354.4, 285.7, 642.4, 376.3),
                     limit_y=limit_y, limit_x1=657.8)
    long_ko = ("여자 파티광 #1/여자 파티광 #2/여자 파티광 #3/남자 파티광(연호):"
               "화끈하게 놀자. 화끈하게 놀자... " * 4)
    ov = StoryboardProfile().place(block, long_ko, (1008.0, 612.0))
    assert ov.rect[3] <= limit_y + 0.5, (
        f"주석이 필드 박스 하단({limit_y})을 넘어 {ov.rect[3]}까지 뻗었다 — "
        "다음 필드 박스를 덮는다")


def test_place_below_ignores_limit_that_sits_above_the_source():
    """FL104 p16 회귀(사용자 신고 "Dialog 번역 누락", 2026-08-03): 대사 주석이
    필드 박스를 벗어나 **판넬 그림 위로 밀려 올라갔다.**

    그 페이지의 limit_y(281.7)는 자기 블록 bbox(285.7~361.3)보다도 위였다 —
    1열 Dialog의 다음 라벨을 못 찾은 열 무관 폴백이 다른 열 Action Notes의
    y를 집어온 값이다. 그런 모순된 하한을 그대로 접으면 max_y1 < y0가 되고
    크래시 방지 안전망이 주석을 위로 밀어 올린다. 아래 경로의 원칙은
    '위로 밀지 않는다'이므로 그런 하한은 무시해야 한다."""
    block = PdfBlock(page=0, kind="dialog",
                     text="240 BELLE Keep moving, Manny!",
                     bbox=(39.0, 285.7, 319.0, 361.3),
                     limit_y=281.7,          # ← 자기 원문보다 위(모순)
                     limit_x1=326.6)
    ov = StoryboardProfile().place(
        block, "벨: 계속 가, 매니! 몇 블록만 더 가면 집이야.", (1008.0, 612.0))
    assert ov.rect[1] >= block.bbox[3], (
        f"주석이 원문(하단 {block.bbox[3]}) 위 {ov.rect[1]}에 놓였다 — 판넬 침범")


def test_place_panel_label_above_when_room():
    """패널 라벨 배치 기본 경로: 라벨 바로 위, fontsize 10.0 고정."""
    block = PdfBlock(page=0, kind="panel_label", text="HANK'S TRUCK",
                     bbox=(300.0, 250.0, 500.0, 290.0))
    ov = StoryboardProfile().place(block, "행크의 트럭", (1008.0, 612.0))
    assert ov.rect[3] <= block.bbox[1]  # 라벨 위(rect 아래끝이 라벨 위끝 이하)
    assert not _rects_intersect(ov.rect, block.bbox)
    assert ov.fontsize == 10.0


def test_place_panel_label_switches_to_right_when_near_top():
    """라벨이 페이지 상단 가까이 있어 '위' 배치가 페이지 밖으로 나가면
    필드용 오른쪽/아래 로직으로 전환(폭 문턱 90pt로 완화)."""
    block = PdfBlock(page=0, kind="panel_label", text="HANK'S TRUCK",
                     bbox=(300.0, 20.0, 500.0, 40.0))
    ov = StoryboardProfile().place(block, "행크의 트럭", (1008.0, 612.0))
    assert not _rects_intersect(ov.rect, block.bbox)
    assert ov.rect[0] >= block.bbox[2]  # 오른쪽 경로로 전환됨


def test_detect_rejects_page_without_field_labels(tmp_path):
    """필드 라벨이 없는 PDF는 감지하지 않는다 — 판정 근거는 방향이 아니라
    'Dialog'+'Action Notes' 라벨의 존재다(2026-07-31 정정: 예전 이름은
    `test_detect_rejects_portrait`였는데, 이 fixture는 텍스트가 아예 없어
    방향 게이트를 지워도 통과한다 = 방향을 검증한 적이 없다)."""
    import fitz
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    p = tmp_path / "portrait.pdf"
    doc.save(p)
    doc.close()
    d = open_pdf(p)
    try:
        assert detect_profile(d) is None
    finally:
        d.close()


def _make_portrait_storyboard_pdf(tmp_path: Path) -> Path:
    """세로형(612x792) 스토리보드 — 실물 FL102 `1_PANEL` 익스포트가 이
    크기다. 라벨 규약은 가로형과 같다."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((39, 410), "Dialog", fontsize=12)
    page.insert_text((39, 430), "If you wanna go, then go.", fontsize=10)
    page.insert_text((39, 575), "Action Notes", fontsize=12)
    page.insert_text((39, 595), "HANK walks to the door.", fontsize=10)
    path = tmp_path / "sb_portrait.pdf"
    doc.save(path)
    doc.close()
    return path


def test_detect_accepts_portrait_storyboard(tmp_path):
    """세로형 스토리보드도 감지한다(2026-07-31).

    예전 `detect()`는 `w <= h`면 즉시 False였다 — GABE01(1008x612)에 맞춘
    가드였는데, 실물 FL102의 `1_PANEL` 익스포트가 **612x792 세로형**이라
    정당한 스토리보드가 "지원하지 않는 PDF 포맷입니다"로 거부됐다.
    방향은 애초에 판별력도 없었다: 타당성 문서가 조사한 4개 포맷(스토리
    보드·대본·컬러노트·리드시트)이 전부 가로형이라, 가로/세로는 스토리
    보드를 다른 포맷과 구분해주지 않는다. 실제 판별자는 라벨 2종이다."""
    doc = open_pdf(_make_portrait_storyboard_pdf(tmp_path))
    try:
        profile = detect_profile(doc)
        assert profile is not None and profile.name == "storyboard"
        blocks = profile.extract(doc)
        dialog = next(b for b in blocks if b.kind == "dialog")
        assert dialog.text == "If you wanna go, then go."
    finally:
        doc.close()


# 3단(3열) 템플릿 실측 좌표 — FL102_FNL_A_3_PANEL.pdf, 1008x612
_COL_X = (39.0, 354.4, 669.8)
_DIALOG_BOX_X = ((36.0, 342.4), (351.4, 657.8), (666.8, 973.1))
_DIALOG_BOX_Y = (291.3, 422.1)
_ACTION_BOX_Y = (425.1, 556.0)


def _make_storyboard_pdf_three_panel(tmp_path: Path) -> Path:
    """3단 스토리보드 한 페이지 — 실물 FL102_FNL_A_3_PANEL 좌표 그대로.

    한 페이지에 Scene/Panel + Dialog + Action Notes가 **열마다 한 벌씩**
    총 3벌 있다. 열 간격은 315.4pt로 `_field_content`의 x 허용폭(60pt)보다
    훨씬 넓다 — 즉 열끼리 내용이 섞일 일은 없고, 문제는 오직 "2·3열을
    아예 안 본다"였다.

    페이지 푸터(`Property of ...`)도 실물 좌표(x0=405.8, y0≈563.5)에 둔다:
    2열 x0(354.4)과 51.4pt 차이라 x 허용폭 60pt 안에 들어오고, 마지막
    필드(Action Notes)는 '다음 라벨'이 없어 창 상한이 없다 — 그래서 창을
    필드 박스 하단으로 막지 않으면 푸터가 2열 Action Notes 내용으로
    빨려 들어간다(실물 79페이지에서 77건 발생)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    for i, x in enumerate(_COL_X):
        bx0, bx1 = _DIALOG_BOX_X[i]
        page.draw_rect(fitz.Rect(bx0, _DIALOG_BOX_Y[0], bx1, _DIALOG_BOX_Y[1]),
                       color=(0, 0, 0), width=1)
        page.draw_rect(fitz.Rect(bx0, _ACTION_BOX_Y[0], bx1, _ACTION_BOX_Y[1]),
                       color=(0, 0, 0), width=1)
        page.insert_text((x, 305), "Dialog", fontsize=12)
        page.insert_text((x, 340), f"ANNOUNCER {i + 1} speaks now.", fontsize=10)
        page.insert_text((x, 439), "Action Notes", fontsize=12)
        page.insert_text((x, 470), f"Panel {i + 1} action beat.", fontsize=10)
    page.insert_text((405.8, 570),
                     "FL102_FNL_A Property of Netflix & Robin Red Breast", fontsize=6)
    path = tmp_path / "sb_three_panel.pdf"
    doc.save(path)
    doc.close()
    return path


def test_extract_finds_every_column_on_three_panel_page(tmp_path):
    """3단 페이지에서 필드를 **열마다** 뽑는다 — 6건(대사 3 + 액션 3).

    회귀 근거(2026-07-31 실측, FL102_FNL_A_3_PANEL 79페이지): 예전
    `_field_content`는 라벨을 찾는 즉시 `break`하고 `extract`도 `_FIELDS`를
    페이지당 한 번만 돌아서, 뽑히는 블록 48건이 **전부 1열**이었다
    (열별 분포 {39.0: 48}). 사람 납품본이 필드에 단 한글 주석 59건 중
    2·3열 34건이 통째로 누락됐다."""
    doc = open_pdf(_make_storyboard_pdf_three_panel(tmp_path))
    try:
        blocks = [b for b in StoryboardProfile().extract(doc)
                  if b.kind in ("dialog", "action")]
        assert len(blocks) == 6
        assert sum(1 for b in blocks if b.kind == "dialog") == 3
        assert sum(1 for b in blocks if b.kind == "action") == 3
        # 각 열이 자기 내용을 갖는다 — 열이 섞이거나 누락되면 실패
        for i, x in enumerate(_COL_X):
            col = [b for b in blocks if abs(b.bbox[0] - x) < 5.0]
            assert len(col) == 2, f"열 x0={x}에서 2건이 아니라 {len(col)}건"
            dialog = next(b for b in col if b.kind == "dialog")
            action = next(b for b in col if b.kind == "action")
            assert dialog.text == f"ANNOUNCER {i + 1} speaks now."
            assert action.text == f"Panel {i + 1} action beat."
    finally:
        doc.close()


def test_extract_limits_come_from_the_blocks_own_column_box(tmp_path):
    """3단에서 limit_y/limit_x1은 **그 블록이 속한 열의** 박스에서 온다.

    열 무관으로 고르면 1열 블록이 3열 박스의 우측(973.1)을 상한으로 받아
    주석이 옆 열을 가로지른다."""
    doc = open_pdf(_make_storyboard_pdf_three_panel(tmp_path))
    try:
        blocks = [b for b in StoryboardProfile().extract(doc)
                  if b.kind in ("dialog", "action")]
        # 먼저 6건을 못 박는다 — 2·3열이 없으면 아래 루프가 공허하게
        # 통과해버려(검사할 블록이 0건) 판별력이 사라진다.
        assert len(blocks) == 6
        for i, x in enumerate(_COL_X):
            _bx0, bx1 = _DIALOG_BOX_X[i]
            for b in (b for b in blocks if abs(b.bbox[0] - x) < 5.0):
                expect_y = (_DIALOG_BOX_Y[1] if b.kind == "dialog"
                            else _ACTION_BOX_Y[1])
                assert b.limit_y == pytest.approx(expect_y, abs=1.0)
                assert b.limit_x1 == pytest.approx(bx1, abs=1.0)
    finally:
        doc.close()


def test_extract_last_field_window_stops_at_field_box_bottom(tmp_path):
    """마지막 필드(Action Notes)의 내용 창은 필드 박스 하단에서 끊긴다 —
    페이지 푸터가 내용으로 빨려 들어가면 안 된다.

    실측: 이 상한이 없으면 FL102 79페이지에서 푸터가 2열 Action Notes로
    77건 유입된다. GABE01(1단, 표본 149페이지)에서는 이 상한으로 잃는
    블록이 **0건**이라 기존 코퍼스에는 무해하다(회귀 아님)."""
    doc = open_pdf(_make_storyboard_pdf_three_panel(tmp_path))
    try:
        blocks = StoryboardProfile().extract(doc)
        # 푸터를 빨아들이는 건 **2열** Action Notes다(x0 51.4pt 차이). 2열이
        # 추출되지 않으면 아래 단언이 공허하게 통과하므로 먼저 못 박는다.
        action_cols = sorted(round(b.bbox[0], 1) for b in blocks
                             if b.kind == "action")
        assert len(action_cols) == 3, f"열별 action 3건이 아님: {action_cols}"
        for b in blocks:
            assert "Property of" not in b.text, (
                f"푸터가 {b.kind} 내용으로 유입됨: {b.text!r}")
    finally:
        doc.close()


def _make_storyboard_pdf_offset_columns(tmp_path: Path) -> Path:
    """다음 라벨을 '같은 열 우선'으로 고르는지 가르는 fixture — 2열의
    Action Notes 라벨(y≈349)이 1열 것(y≈417)보다 **위에** 있다. 도형은
    일부러 없다(창 상한이 오직 다음 라벨에서만 오게).

    ⚠2열 라벨을 **먼저** 삽입하는 것이 이 fixture의 핵심이다. 옛 규칙은
    y가 아니라 `raws` 순서의 첫 매치를 골랐으므로, 1열 라벨을 먼저 넣으면
    옛 규칙도 우연히 정답을 맞혀 판별력이 사라진다(뮤테이션 실측으로
    확인: 삽입 순서를 뒤집으면 옛 규칙도 통과해버렸다)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((354.4, 362), "Action Notes", fontsize=12)   # 2열, 더 위
    page.insert_text((39.0, 305), "Dialog", fontsize=12)
    page.insert_text((39.0, 400), "ANNOUNCER speaks now.", fontsize=10)
    page.insert_text((39.0, 430), "Action Notes", fontsize=12)
    path = tmp_path / "sb_offset_cols.pdf"
    doc.save(path)
    doc.close()
    return path


def test_next_label_prefers_same_column_over_a_nearer_other_column(tmp_path):
    """1열 Dialog의 창 상한은 **1열의** Action Notes(y≈417)여야 한다 —
    더 가까운 2열 라벨(y≈349)을 고르면 1열 대사(y≈389)가 창 밖으로
    밀려나 통째로 사라진다.

    '열 무관으로 먼저 찾기'로 되돌리면 이 테스트가 실패한다."""
    doc = open_pdf(_make_storyboard_pdf_offset_columns(tmp_path))
    try:
        blocks = StoryboardProfile().extract(doc)
        dialog = next(b for b in blocks if b.kind == "dialog")
        assert dialog.text == "ANNOUNCER speaks now."
    finally:
        doc.close()


def test_place_keeps_annotation_inside_its_own_column(tmp_path):
    """3단에서 어떤 배치 경로를 타든 주석 rect가 **자기 열의 박스 우측**을
    넘지 않는다 — 넘으면 옆 열 판넬을 침범한다."""
    doc = open_pdf(_make_storyboard_pdf_three_panel(tmp_path))
    try:
        profile = StoryboardProfile()
        blocks = [b for b in profile.extract(doc)
                  if b.kind in ("dialog", "action")]
        assert len(blocks) == 6
        for b in blocks:
            i = min(range(3), key=lambda k: abs(_COL_X[k] - b.bbox[0]))
            _bx0, bx1 = _DIALOG_BOX_X[i]
            ov = profile.place(b, "아나운서가 지금 말한다. " * 3,
                               doc.page_size(b.page))
            assert ov.rect[2] <= bx1 + 1.0, (
                f"열 {i} 주석이 박스 우측({bx1})을 넘음: {ov.rect}")
            assert not _rects_intersect(ov.rect, b.bbox)
    finally:
        doc.close()


SAMPLES = os.environ.get("YESON_PDF_SAMPLES")


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
def test_real_storyboard_sample(monkeypatch):
    """실물 검증(로컬 전용): GABE01_A1 앞 30페이지에서 감지 + 블록 추출.

    2026-07-30 E2E 후속(다중 블록 병합) 재측정: 병합 전 30페이지 기준
    22블록(16d+6a, 필드당 1블록)이었으나, 병합해도 30페이지 안에서는
    필드당 후보가 다중 블록인 페이지가 없어 블록 '개수'는 그대로
    22(16d+6a) — 대신 병합으로 각 블록의 '내용'이 달라진다(대사 누락
    수정). page 30의 dialog는 원래 화자 줄(`3 HANK/EMPLOYEES`)만
    반환했으나 병합 후에는 실제 대사(`Propane.`)까지 포함해야 한다.

    Task 14 후속(패널 콜아웃 라벨 OCR): 이 문서(1037페이지) 전체를
    extract()하므로 패널 OCR도 전 페이지에서 돈다 — page idx 1에
    "HANK'S TRUCK" 라벨이 실재(수작업본 관례상 '행크의 트럭' 주석)해야
    한다. 프리필터 통과 카운트는 find_panel_labels가 프리필터를 통과했을
    때만 호출하는 panel_ocr._get_engine을 스파이해 별도 재스캔 없이 이
    단일 extract() 호출에서 얻는다.

    프리필터 통과 페이지 수 범위 재측정(실물 전체 문서 스캔, 2026-07-30):
    브리프의 "85주석/47페이지" 추정보다 훨씬 많다. 원인은 버그가 아니라
    서로 다른 모집단 비교였다: "85/47"은 **납품(번역 완료)본**에서 육안
    으로 셀 수 있는 주석 수 — 순수 코드 라벨(1000SB, 651 등)은 기존 스킵
    규칙(ko==원문이면 주석 생략)으로 납품본에 아예 안 나타난다. 이
    테스트는 **추출 단계** 카운트라 코드 라벨도 전부 포함된다(스킵은
    번역 이후 오버레이 단계 몫). 상위 빈도 라벨을 단어/코드로 나눠 보면
    캐릭터 이름(HANK 15회, CONNIE 13회, JOSEPH 11회, BOOMHAUER 10회,
    BOBBY 10회, BILL 8회, DALE 7회, MUSTACHES 7회 = 81)이 "85주석"
    추정과 거의 정확히 들어맞고, 나머지는 차량/자산 코드(1000SB·1000SA·
    651·652·658·656A 등, 다수 페이지 반복) — 브리프가 예측한 "대부분
    캐릭터 이름"과 일치한다. 이상치 페이지도 없다(최대 10라벨/페이지,
    전부 다중 차량 주차장 씬 등 정당한 케이스).

    표지 페이지 게이트(리뷰 후속, 2026-07-30): 최초 실측(146페이지/280
    블록)에는 표지 페이지(0)의 쇼 로고 텍스트("KING"/"HILL")가 패널
    라벨로 잘못 섞여 있었다 — Dialog/Action Notes 필드 라벨이 있는
    페이지만 패널 OCR을 돌리도록 extract()에 게이트를 추가해 제거했다
    (page 0은 필드 라벨이 없는 표지라 패널 OCR 자체가 스킵된다)."""
    from apps.server.domain.pdf_translate import panel_ocr

    prefilter_pass = {"n": 0}
    orig_get_engine = panel_ocr._get_engine

    def _counting_get_engine():
        prefilter_pass["n"] += 1
        return orig_get_engine()

    monkeypatch.setattr(panel_ocr, "_get_engine", _counting_get_engine)

    path = Path(SAMPLES) / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
    doc = open_pdf(path)
    try:
        profile = detect_profile(doc)
        assert profile is not None and profile.name == "storyboard"
        t0 = time.time()
        all_blocks = profile.extract(doc)
        elapsed = time.time() - t0
        blocks = [b for b in all_blocks if b.page < 30]
        # Task 14 후속: blocks에는 이제 panel_label도 섞여 있으므로 필드
        # (dialog/action) 개수 불변식은 필드 kind로만 필터링해 검증한다 —
        # 안 그러면 새로 생긴 panel_label 블록이 22 카운트를 깨뜨린다.
        field_blocks = [b for b in blocks if b.kind in ("dialog", "action")]
        assert len(field_blocks) == 22
        dialog = [b for b in field_blocks if b.kind == "dialog"]
        action = [b for b in field_blocks if b.kind == "action"]
        assert len(dialog) == 16
        assert len(action) == 6
        assert all("\t" not in b.text for b in blocks)
        page30_dialog = next(
            b for b in all_blocks if b.page == 30 and b.kind == "dialog")
        assert "Propane." in page30_dialog.text

        panel_blocks = [b for b in all_blocks if b.kind == "panel_label"]
        page0_panel = [b for b in panel_blocks if b.page == 0]
        page1_panel = [b for b in panel_blocks if b.page == 1]
        print(
            f"\npdf-translate panel OCR: extract() wall_time={elapsed:.1f}s "
            f"prefilter_pass_pages={prefilter_pass['n']} "
            f"panel_labels_found={len(panel_blocks)} "
            f"page0_labels={[b.text for b in page0_panel]} "
            f"page1_labels={[b.text for b in page1_panel]}")
        # 리뷰 후속(2026-07-30): 표지 페이지(0)는 Dialog/Action Notes 라벨이
        # 없으므로 패널 OCR 자체가 게이트에 걸려 스킵된다 — 로고 텍스트
        # ("KING"/"HILL")가 더 이상 라벨로 오인식되면 안 된다.
        assert page0_panel == []
        assert any("HANK" in b.text.upper() for b in page1_panel)
        # 실측(2026-07-30, 전체 1037페이지, 표지 게이트 적용 후) 145페이지/
        # 278블록 — 위 docstring 설명대로 순수 코드 라벨까지 포함하는
        # 추출-단계 카운트라 "85주석/47페이지"(납품본 기준)보다 크다.
        # Task 19 재측정(빨강 마스크 재설계 + 히트 문턱 완화, 같은 문서):
        # 156페이지/303블록 — p18류 희석된 빨강(안티에일리어싱)이 추가로
        # 잡혀 소폭 증가. 100~250은 그 실측치 주변 여유폭 — 지나치게
        # 적으면(프리필터 붕괴) 또는 지나치게 많으면(빨강 문턱이 너무
        # 느슨해 잡음까지 통과) 회귀로 잡는다.
        assert 100 <= prefilter_pass["n"] <= 250
        # 리뷰 후속(라운드 1 Minor 3): 라벨 총계에도 회귀 단언을 추가한다
        # (페이지 수는 라벨 수의 간접 프록시일 뿐). 실측 신규 25건 전부를
        # OLD(구 마스크·문턱) 스캔과 대조 확인(소실 0건, 신규만 25건) —
        # 카테고리는 전부 기존에 이미 정당성이 확인된 부류다: 차량/자산
        # 코드(651·652·2000~2024류, p18 CAR006A/CAR010/CAR018A/1000SB
        # 포함), 기존에 이미 등장하던 캐릭터 이름(DALE·BILL, 이전 실측에서
        # 각 7·8회로 이미 정당한 것으로 확인됨)의 추가 등장, HANK'S TRUCK
        # 계열 라벨 2건 추가. 유일하게 애매한 건 p537의 "1025HATHER1026"
        # (두 코드가 OCR로 합쳐 읽힌 것으로 보임 — 페이지 자체가 표지류
        # 오탐이 아니라 정상 템플릿 페이지의 실제 빨강 라벨 영역이라
        # Task 14류 페이지 오인식은 아니다. 텍스트 품질 이슈로 남겨둔다).
        # 리뷰 후속(라운드 2 Minor): 260은 이 태스크의 실질 개선을 못 잠근다
        # — 마스크 변경을 통째로 되돌리면 303이 옛 실측치 278로 떨어지는데
        # 278도 260을 통과해버려 회귀를 놓친다. 295로 올려 그 되돌림을
        # 직접 잡는다(p18 CAR 라벨 실물 단언이 이미 되돌림을 잡지만, 이건
        # 벨트-앤-브레이스 이중 방어).
        assert 295 <= len(panel_blocks) <= 350

        # 불변식(2026-07-30 실기 피드백 후속): 어떤 배치 경로를 타든 주석
        # rect는 **자기 블록 자신의** block.bbox와 교차하지 않는다 — 전 문서
        # 전 블록에 대해 확인(패널 라벨 포함, Task 14 후속). 사용자
        # 스크린샷으로 신고된 원래 버그(주석이 자기 원문을 덮음)가 이 성질이다.
        #
        # ⚠ 범위 정정(전브랜치 리뷰 M-9(a)): 이 루프는 블록 A의 주석이 블록 B의
        # 원문 위에 얹히는지는 **보지 않는다** — 그건 Task 13이 이연한 별개
        # 항목이고, placeholder 길이 보정(M-9(b))과 함께 재측정 대상이다.
        profile2 = StoryboardProfile()
        for b in all_blocks:
            placeholder_ko = "가" * max(10, len(b.text) // 2)
            ov = profile2.place(b, placeholder_ko, doc.page_size(b.page))
            assert not _rects_intersect(ov.rect, b.bbox), (
                f"page {b.page} kind={b.kind}: rect {ov.rect} intersects "
                f"bbox {b.bbox}")
    finally:
        doc.close()


# ── refine_ko: 판넬 라벨의 미번역 영문 줄 제거 (FL104_Orev p70 실측) ──────

def _panel_block(text: str = "x") -> PdfBlock:
    return PdfBlock(page=70, kind="panel_label", text=text, bbox=(0, 0, 1, 1))


@pytest.mark.parametrize("ko,expected", [
    # 실측 결함 그대로 — 자산 코드가 캐릭터 라벨과 한 묶음이 되어 딸려 나갔다.
    ("CROWDINC037\n히피여자", "히피여자"),
    ("SBINC12\n좀비\n여자 파티광1", "좀비\n여자 파티광1"),
    # 통째로 미번역이면 붙일 게 없다 → 빈 문자열(부르는 쪽이 건너뛴다)
    ("CROWDINC037", ""),
])
def test_refine_ko_drops_untranslated_english_lines(ko, expected):
    """묶기 규칙을 아무리 다듬어도 서로 무관한 두 라벨이 나란히 서면 다시
    생기는 결함이라, 마지막 관문에서 줄 단위로 걷어낸다."""
    assert StoryboardProfile().refine_ko(_panel_block(), ko) == expected


@pytest.mark.parametrize("ko", [
    "부수\n성노동자", "좀비\n파티광3", "싸이클 1/2", "차006A",
    # 사람 납품본 실측: 한글 없는 줄 113개 중 109개가 숫자 전용이고,
    # 라틴 글자가 든 4개도 전부 한 글자 싸이클 기호다 — 정상 주석이다.
    "126", "005", "1/2", "A 2/2\nB 1/2",
])
def test_refine_ko_keeps_legitimate_lines(ko):
    """숫자 줄·한 글자 기호는 사람도 쓴다 — 지우면 멀쩡한 주석을 잃는다."""
    assert StoryboardProfile().refine_ko(_panel_block(), ko) == ko


def test_refine_ko_leaves_field_blocks_untouched():
    """본문(dialog/action)에는 자산 ID·파일명이 정당하게 섞인다(프롬프트가
    그대로 두라고 지시한다) — 줄 단위로 떼어낼 성질이 아니라 건드리지 않는다."""
    ko = "매니:TGNO_PizzaBox_CL_V01 상자를 든다."
    for kind in ("dialog", "action"):
        block = PdfBlock(page=1, kind=kind, text="x", bbox=(0, 0, 1, 1))
        assert StoryboardProfile().refine_ko(block, ko) == ko
