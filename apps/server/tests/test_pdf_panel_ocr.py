from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest

from apps.server.domain.pdf_translate import panel_ocr
from apps.server.domain.pdf_translate.backend import open_pdf
from apps.server.domain.pdf_translate.profiles.storyboard import _panel_region

_REGION = (0.0, 95.0, 1008.0, 460.0)


def _make_panel_pdf(tmp_path: Path, *, label_color=(1, 0, 0)) -> Path:
    """가로형 페이지 + 빨간 사각 테두리 라벨(리더라인 콜아웃 흉내) + 검정
    잡선(패널 그림선 흉내) + 검정 **글자** 미끼. label_color를 검정으로 주면
    빨강 프리필터 가드 검증용 fixture가 된다.

    검정 글자 미끼("OPEN 24 HOURS", 패널 안 간판 글씨 흉내)는 2026-07-30
    테스트 품질 스윕에서 추가됐다 — 그 전엔 검정 요소가 전부 벡터 도형이라
    OCR 히트가 아예 생기지 않았고, 그래서 히트 단위 빨강 비율 필터
    (_HIT_RED_RATIO_MIN)가 오프라인에서 **한 번도 무언가를 걸러내지 않았다**.
    미끼가 있으면 raw 히트 2개 중 1개가 필터에서 떨어져, 아래 테스트들의
    `len(labels) == 1`이 비로소 판별력을 갖는다."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.draw_line((100, 150), (400, 300), color=(0, 0, 0), width=1.5)
    page.draw_line((500, 120), (700, 350), color=(0, 0, 0), width=1.5)
    page.draw_circle((300, 200), 40, color=(0, 0, 0), width=1.5)
    page.insert_text((120, 180), "OPEN 24 HOURS", fontsize=16, color=(0, 0, 0))
    rect = fitz.Rect(300, 250, 500, 290)
    if label_color == (1, 0, 0):
        page.draw_rect(rect, color=(1, 0, 0), width=2)
    page.insert_text((rect.x0 + 10, rect.y0 + 28), "HANK'S TRUCK", fontsize=14,
                     color=label_color)
    path = tmp_path / "panel.pdf"
    doc.save(path)
    doc.close()
    return path


def test_find_panel_labels_reads_red_callout(tmp_path, monkeypatch):
    """빨간 콜아웃 라벨(사각 테두리 + 빨간 글자)을 OCR로 찾는다 — 텍스트에
    HANK·TRUCK 포함(OCR 오차 허용: 대문자·아포스트로피 관용), bbox가 라벨
    근방(±20pt)."""
    monkeypatch.delenv(panel_ocr.ENV_ENABLED, raising=False)
    path = _make_panel_pdf(tmp_path)
    doc = open_pdf(path)
    try:
        labels = panel_ocr.find_panel_labels(doc, 0, _REGION)
        assert len(labels) == 1
        text = labels[0].text.upper()
        assert "HANK" in text
        assert "TRUCK" in text
        x0, y0, x1, y1 = labels[0].bbox
        # 라벨 원본 사각 테두리 rect은 (300, 250, 500, 290) — OCR 텍스트
        # bbox는 글자 폭만큼(테두리보다 좁게) 잡히므로, ±20pt 여유를 준
        # 테두리 안에 완전히 들어오는지로 "근방"을 검증한다.
        assert 280.0 <= x0 and x1 <= 520.0
        assert 230.0 <= y0 and y1 <= 310.0
    finally:
        doc.close()


def test_hit_red_ratio_filter_drops_black_text_that_ocr_actually_read(
        tmp_path, monkeypatch):
    """_HIT_RED_RATIO_MIN이 실제로 하중을 받는지 직접 잠근다.

    문턱을 0으로 낮추면 검정 글자 미끼("OPEN 24 HOURS")까지 함께 나오고,
    실제 문턱(0.15)에서는 빨간 라벨만 남는다 — 즉 이 상수가 없으면 패널 안
    간판·표지판 글씨가 번역 대상으로 새어 들어간다. 첫 단언이 픽스처의
    유효성(미끼가 정말 OCR에 잡힘)을 함께 잠근다."""
    monkeypatch.delenv(panel_ocr.ENV_ENABLED, raising=False)
    path = _make_panel_pdf(tmp_path)
    doc = open_pdf(path)
    try:
        monkeypatch.setattr(panel_ocr, "_HIT_RED_RATIO_MIN", 0.0)
        unfiltered = panel_ocr.find_panel_labels(doc, 0, _REGION)
        texts = " ".join(b.text.upper() for b in unfiltered)
        assert "TRUCK" in texts and "HOURS" in texts  # 미끼가 실제 OCR 히트다

        monkeypatch.setattr(panel_ocr, "_HIT_RED_RATIO_MIN", 0.15)
        filtered = panel_ocr.find_panel_labels(doc, 0, _REGION)
        assert len(filtered) == 1
        assert "TRUCK" in filtered[0].text.upper()
    finally:
        doc.close()


def test_find_panel_labels_no_red_returns_empty_without_ocr(tmp_path, monkeypatch):
    """빨강이 없는 페이지는 프리필터에서 즉시 []를 반환하고, 비싼 OCR
    엔진(RapidOCR)은 아예 호출되지 않아야 한다(엔진 생성 스파이로 확인)."""
    monkeypatch.delenv(panel_ocr.ENV_ENABLED, raising=False)
    calls: list[dict] = []
    monkeypatch.setattr(panel_ocr, "_new_engine",
                        lambda **kw: calls.append(kw))
    panel_ocr._reset_engines()
    path = _make_panel_pdf(tmp_path, label_color=(0, 0, 0))  # 검정 텍스트
    doc = open_pdf(path)
    try:
        labels = panel_ocr.find_panel_labels(doc, 0, _REGION)
        assert labels == []
        assert calls == []
    finally:
        doc.close()
        panel_ocr._reset_engines()


def test_find_panel_labels_kill_switch_returns_empty(tmp_path, monkeypatch):
    """YESON_PDF_PANEL_OCR=0이면 빨간 라벨이 있어도 항상 []."""
    monkeypatch.setenv(panel_ocr.ENV_ENABLED, "0")
    path = _make_panel_pdf(tmp_path)
    doc = open_pdf(path)
    try:
        assert panel_ocr.find_panel_labels(doc, 0, _REGION) == []
    finally:
        doc.close()


# ── 빨강 마스크 재설계 (Task 19, 실측 2026-07-30) ─────────────────────────
# p18 실측: 얇은 빨간 글자가 안티에일리어싱으로 희석돼 절대 채도 마스크
# (구 R>150,G<100,B<100)로는 60dpi 프리필터에서 5px뿐(문턱 50 미달, 전멸)
# 이고 심지어 200dpi 히트 자체도 비율 0.053~0.083으로 옛 히트 문턱(0.15)에
# 크게 못 미쳤다(라벨 6종 전부). R−G/R−B 차이 기준 마스크로 바꾸면 같은
# 픽셀이 안정적으로 잡힌다(프리필터·히트 판정 공용 함수).

def test_red_mask_detects_diluted_red_that_absolute_saturation_would_miss():
    """R−G/R−B 차이 기준 마스크는 절대 채도(R>150 등)가 아니므로 희석된
    빨강(p18류)도 잡는다 — 완전한 흰색에 가까운 옅은 분홍조차 R−G/R−B
    차이만 크면 빨강으로 판정된다."""
    diluted_red = np.full((10, 10, 3), 255, dtype=np.uint8)
    diluted_red[:, :, 1] = 180  # G
    diluted_red[:, :, 2] = 180  # B
    assert int(panel_ocr._red_mask(diluted_red).sum()) == 100


def test_red_mask_rejects_grayscale_ink_regardless_of_brightness():
    """검정/회색 그림선(R≈G≈B)은 밝기와 무관하게 항상 마스크에서 제외돼야
    한다(p1·p18·p93 실측: 검정 히트 전부 비율 0.000) — 오탐 없이 완화가
    가능한 이유."""
    for gray in (0, 80, 180, 255):
        arr = np.full((10, 10, 3), gray, dtype=np.uint8)
        assert int(panel_ocr._red_mask(arr).sum()) == 0


def test_find_panel_labels_prefilter_passes_diluted_red_below_old_thresholds(
        tmp_path, monkeypatch):
    """통합 확인: 절대 채도 마스크로는 60dpi에서 0px(옛 문턱 50 미달 —
    프리필터에서 전멸해 OCR 자체가 스킵됐을 색)인 옅은 빨강 라벨도, 완화된
    마스크(R>120,R−G>50,R−B>50)+문턱 10으로는 프리필터를 통과해 실제로
    OCR이 호출되고 라벨이 검출돼야 한다."""
    monkeypatch.delenv(panel_ocr.ENV_ENABLED, raising=False)
    path = _make_panel_pdf(tmp_path, label_color=(0.75, 0.45, 0.45))
    doc = open_pdf(path)
    try:
        low_arr = panel_ocr._decode_png(doc.render_png(0, dpi=panel_ocr._PREFILTER_DPI))
        low_crop = panel_ocr._crop_region_px(low_arr, _REGION, panel_ocr._PREFILTER_DPI)
        # 이 색은 절대 채도 기준으로는 60dpi에서 완전히 안 잡힌다(0px) —
        # 옛 문턱(50)은 물론 새 문턱(10)으로도 옛 마스크였다면 전멸했을 것.
        old_strict_count = int(
            ((low_crop[..., 0].astype(int) > 150)
             & (low_crop[..., 1].astype(int) < 100)
             & (low_crop[..., 2].astype(int) < 100)).sum())
        assert old_strict_count < panel_ocr._PREFILTER_MIN_PIXELS

        labels = panel_ocr.find_panel_labels(doc, 0, _REGION)
        assert len(labels) == 1
        assert "TRUCK" in labels[0].text.upper()
    finally:
        doc.close()


# ── 실물 진단 (Task 19, 2026-07-30, 페이지 번호는 0-based) ────────────────

SAMPLES = os.environ.get("YESON_PDF_SAMPLES")


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
def test_real_sample_page1_finds_hanks_truck_label():
    """실물 검증(로컬 전용): GABE01_A1 page idx 1은 수작업본에 '행크의
    트럭' 주석이 실재하는 페이지 — HANK'S TRUCK 라벨을 읽어야 한다."""
    path = Path(SAMPLES) / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
    doc = open_pdf(path)
    try:
        t0 = time.time()
        labels = panel_ocr.find_panel_labels(doc, 1, _REGION)
        elapsed = time.time() - t0
        print(f"\npanel OCR page1 elapsed={elapsed:.2f}s labels={labels}")
        assert any("HANK" in b.text.upper() for b in labels)
    finally:
        doc.close()


def _real_region(doc, page_idx: int) -> tuple[float, float, float, float]:
    """extract()가 실제로 쓰는 것과 동일한 방식(_panel_region)으로 페이지별
    영역을 구한다 — 고정 _REGION 상수는 Dialog 라벨 y좌표가 다른 실물
    페이지에서 프로덕션과 다른 크롭을 만들어 OCR 분할 결과가 달라질 수
    있다(page 93 실측: 고정 460 크롭은 "ASS"를 분리, 실제 프로덕션 크롭
    458.3은 "ASSDUDE"로 합쳐 읽는다 — 반드시 실제 경로와 같은 방식으로
    검증해야 한다)."""
    raws = doc.raw_blocks(page_idx)
    page_w, _page_h = doc.page_size(page_idx)
    return _panel_region(raws, page_w)


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
def test_real_sample_p18_finds_at_least_three_car_labels():
    """실물 진단(Task 19, 2026-07-30): p18(0-based)은 얇은 빨간 글자가
    60dpi 다운스케일 안티에일리어싱으로 희석돼 옛 절대-채도 마스크로는
    프리필터에서 전멸했다(실측 5px, 문턱 50 미달) — 심지어 200dpi 히트
    자체도 옛 마스크 기준 비율 0.053~0.083으로 옛 히트 문턱(0.15)에
    못 미쳤다. R−G/R−B 차이 기준 마스크(프리필터·히트 공용)로 CAR
    라벨(≥3개)이 검출돼야 한다."""
    path = Path(SAMPLES) / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
    doc = open_pdf(path)
    try:
        region = _real_region(doc, 18)
        labels = panel_ocr.find_panel_labels(doc, 18, region)
        car_labels = [b for b in labels if "CAR" in b.text.upper()]
        print(f"\np18 labels={[b.text for b in labels]}")
        assert len(car_labels) >= 3
    finally:
        doc.close()


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
def test_real_sample_p93_isolated_ass_fragment_not_present():
    """실물 검증(Task 19, 2026-07-30) — 브리프 정정: p93(0-based)의 "ASS"
    파편은 **고칠 대상이 아니라 실증 대상**이었다. 재구축한 비교 하니스가
    최신 출력에서 이 파편을 재현하지 못했고, 이 테스트가 실제 프로덕션
    경로(_panel_region으로 산출한 영역)로 그 상태를 직접 확인한다: OCR은
    "BADASS DUDE" 계열 텍스트를 "ASSDUDE"로 합쳐 읽어 고립된 정확 일치
    "ASS" 히트가 없다 — MUSTACHES에 80%+ 포함되는 작은 히트도 없다(면적
    포함률 실측 ~74.5%, 80% 미달). 그래서 이 태스크는 히트 dedupe(포함
    bbox 제거)를 구현하지 않았다 — 없는 결함을 고치려 멀쩡한 OCR 경로를
    건드리지 않는다는 브리프 지시에 따른 것. 이 테스트는 그 판단의 회귀
    감지용이다(다시 고립된 "ASS"가 나타나면 dedupe 도입을 재검토할 신호)."""
    path = Path(SAMPLES) / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
    doc = open_pdf(path)
    try:
        region = _real_region(doc, 93)
        labels = panel_ocr.find_panel_labels(doc, 93, region)
        texts = [b.text.upper() for b in labels]
        print(f"\np93 labels={texts}")
        assert "ASS" not in texts  # 고립된 정확 일치 파편 없음
        assert any("BAD" in t for t in texts)
        assert any("MUSTACHES" in t for t in texts)
    finally:
        doc.close()


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
def test_real_sample_p410_finds_hanks_truck_label():
    """실물 진단(Task 19, 2026-07-30): p410(0-based)은 프리필터를 이미
    통과했지만(실측 1539px, 옛 문턱 50 통과) OCR은 "Hanks Truck"을 정확히
    읽으면서도(score 1.000) 그 히트의 빨강 비율이 0.134로 옛 히트
    문턱(0.15)에 근소하게 못 미쳐 걸러졌었다 — 원인은 프리필터가 아니라
    히트 판정이었다. 재설계된 마스크(비율 0.241)로 검출을 확인한다."""
    path = Path(SAMPLES) / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
    doc = open_pdf(path)
    try:
        region = _real_region(doc, 410)
        labels = panel_ocr.find_panel_labels(doc, 410, region)
        texts = [b.text.upper() for b in labels]
        print(f"\np410 labels={texts}")
        assert any("HANK" in t for t in texts)
    finally:
        doc.close()
