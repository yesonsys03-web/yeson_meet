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
    """이 fixture의 Dialog 블록은 페이지가 넓어(1008pt) 오른쪽 여유가
    충분(right_w ≈ 200pt >= _MIN_RIGHT_WIDTH)하므로 2026-07-30 배치 규칙상
    오른쪽 배치가 선택된다 — 'below'는 더 이상 이 fixture에 대한 정확한
    가정이 아니다(경로 무관 불변식만 검증: 원문 비교차 + 페이지 안)."""
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


def test_detect_rejects_portrait(tmp_path):
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
