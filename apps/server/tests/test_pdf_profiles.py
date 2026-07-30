from __future__ import annotations

import os
from pathlib import Path

import pytest

from apps.server.domain.pdf_translate.backend import open_pdf
from apps.server.domain.pdf_translate.profiles import detect_profile
from apps.server.domain.pdf_translate.profiles.base import has_hangul, normalize_ws
from apps.server.domain.pdf_translate.profiles.storyboard import StoryboardProfile


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
        # (a) dialog 블록 중 라벨 그대로이거나 라벨로 시작하는 것 없음
        assert not any(
            b.text == "Action Notes" or b.text.startswith("Action Notes")
            for b in dialog_blocks
        )
        # (b) dialog 블록이 action 블록 텍스트를 중복하지 않음
        assert not any(b.text == action.text for b in dialog_blocks)
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


def test_place_returns_rect_below_block_within_page(tmp_path):
    doc = open_pdf(_make_storyboard_pdf(tmp_path))
    try:
        profile = StoryboardProfile()
        block = next(b for b in profile.extract(doc) if b.kind == "dialog")
        ov = profile.place(block, "가고 싶다면 가세요", doc.page_size(0))
        _x0, y0, x1, y1 = ov.rect
        assert y0 >= block.bbox[3]          # 원문 아래
        assert y1 <= 612 and x1 <= 1008     # 페이지 안
        assert ov.page == 0 and ov.fontsize == 12.0
    finally:
        doc.close()


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
def test_real_storyboard_sample():
    """실물 검증(로컬 전용): GABE01_A1 앞 30페이지에서 감지 + 블록 추출.

    2026-07-30 E2E 후속(다중 블록 병합) 재측정: 병합 전 30페이지 기준
    22블록(16d+6a, 필드당 1블록)이었으나, 병합해도 30페이지 안에서는
    필드당 후보가 다중 블록인 페이지가 없어 블록 '개수'는 그대로
    22(16d+6a) — 대신 병합으로 각 블록의 '내용'이 달라진다(대사 누락
    수정). page 30의 dialog는 원래 화자 줄(`3 HANK/EMPLOYEES`)만
    반환했으나 병합 후에는 실제 대사(`Propane.`)까지 포함해야 한다."""
    path = Path(SAMPLES) / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
    doc = open_pdf(path)
    try:
        profile = detect_profile(doc)
        assert profile is not None and profile.name == "storyboard"
        all_blocks = profile.extract(doc)
        blocks = [b for b in all_blocks if b.page < 30]
        assert len(blocks) == 22
        dialog = [b for b in blocks if b.kind == "dialog"]
        action = [b for b in blocks if b.kind == "action"]
        assert len(dialog) == 16
        assert len(action) == 6
        assert all("\t" not in b.text for b in blocks)
        page30_dialog = next(
            b for b in all_blocks if b.page == 30 and b.kind == "dialog")
        assert "Propane." in page30_dialog.text
    finally:
        doc.close()
