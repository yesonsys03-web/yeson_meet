from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest

from apps.server.domain.pdf_translate import panel_ocr
from apps.server.domain.pdf_translate.backend import (
    CorruptWord,
    RawBlock,
    open_pdf,
)
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


def _make_black_cam_guide_pdf(tmp_path):
    """FL102 관례 — 제작 지시어가 **검정 글자**로 적힌 판넬. 빨강은 판넬
    테두리뿐이라(글자가 아니라) 히트 빨강 비율은 0에 가깝다. 그림 속 간판
    미끼("OPEN 24 HOURS")도 같은 검정으로 함께 둔다 — 색으로는 둘이 구분되지
    않으므로, 하나만 통과하면 판별 근거가 색이 아니라 '확인된 지시어 이름'
    임이 증명된다."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.draw_rect(fitz.Rect(80, 110, 900, 280), color=(1, 0, 0), width=3)
    page.insert_text((360, 200), "CAM GUIDE", fontsize=20, color=(0, 0, 0))
    page.insert_text((120, 180), "OPEN 24 HOURS", fontsize=16, color=(0, 0, 0))
    path = tmp_path / "cam_guide.pdf"
    doc.save(path)
    doc.close()
    return path


def test_find_panel_labels_accepts_black_production_label(tmp_path, monkeypatch):
    """검정 제작 지시어(CAM GUIDE)는 빨강 비율 문턱을 우회해 통과한다 —
    같은 검정인 그림 속 간판(OPEN 24 HOURS)은 계속 걸러진다.

    실물 근거(2026-07-31, 사용자 신고 "카메라 가이드 번역 누락"): FL102 p27·
    p30에서 OCR은 `CAM GUIDE`를 신뢰도 0.99~1.00으로 정확히 읽는데 히트
    빨강 비율이 0.000이라 버려졌다. 사람 납품본은 이 문서에서 `카메라 가이드`
    6건·`필드가이드…` 5건을 단다. 문턱을 낮추는 대신 이름으로 통과시키는
    이유가 이 테스트의 두 번째 단언이다(간판은 여전히 배제)."""
    monkeypatch.delenv(panel_ocr.ENV_ENABLED, raising=False)
    doc = open_pdf(_make_black_cam_guide_pdf(tmp_path))
    try:
        labels = panel_ocr.find_panel_labels(doc, 0, _REGION)
        texts = " ".join(b.text.upper().replace(" ", "") for b in labels)
        assert "CAMGUIDE" in texts
        assert "HOURS" not in texts
    finally:
        doc.close()


@pytest.mark.parametrize("text,expected", [
    ("CAM GUIDE", True), ("CAMGUIDE", True), ("cam guide", True),
    ("FIELD GUIDE 1-2", True), ("FIELDGUIDE A LOUIS ONLY", True),
    ("REFERENCE", True),
    ("OPEN 24 HOURS", False), ("REHAB CENTER", False), ("1000SB", False),
    ("HANK'S TRUCK", False),
])
def test_is_production_label_matches_only_known_terms(text, expected):
    """공백·대소문자 무시 + 접두 매칭(뒤에 식별자가 붙는 실물 변형) —
    그림 속 간판이나 자산 코드는 통과시키지 않는다."""
    assert panel_ocr._is_production_label(text) is expected


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


# ══ 깨진 추출 문자 복구 (Task 20) ═══════════════════════════════════════


def _png_bytes(width_px: int, height_px: int) -> bytes:
    import io as _io

    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (width_px, height_px), (255, 255, 255)).save(buf, "PNG")
    return buf.getvalue()


class _StubDoc:
    """복구 경로 단위 테스트용 최소 문서 — 깨진 단어 목록과 렌더만 제공한다.

    복구는 "백엔드가 짚어 준 단어를 렌더해 OCR로 재판독"이 전부라, 실제
    깨진 폰트를 가진 PDF 없이도 그 계약을 전부 검증할 수 있다(합성 PDF로는
    깨진 cmap을 만들 수 없다 — 실물 검증은 아래 env 게이트 테스트가 한다)."""

    def __init__(self, words, *, page_w=200.0, page_h=100.0):
        self._words = words
        self.page_w = page_w
        self.page_h = page_h
        self.render_calls: list[int] = []

    def corrupt_words(self, page: int):
        return list(self._words)

    def render_png(self, page: int, *, dpi: int = 120) -> bytes:
        self.render_calls.append(dpi)
        scale = dpi / 72.0
        return _png_bytes(int(self.page_w * scale), int(self.page_h * scale))


class _FakeEngine:
    """RapidOCR 대역 — 호출 순서대로 미리 정한 결과를 돌려준다."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def __call__(self, _crop):
        self.calls += 1
        hits = self._results.pop(0) if self._results else []
        return ([([[0, 0], [1, 0], [1, 1], [0, 1]], text, score)
                 for text, score in hits], 0.0)


def _use_engine(monkeypatch, engine):
    monkeypatch.setattr(panel_ocr, "_new_engine", lambda **kw: engine)
    panel_ocr._reset_engines()


def _word(text, bad_indices, *, block_index=0, offset=0,
          bbox=(10.0, 10.0, 60.0, 22.0)):
    return CorruptWord(block_index=block_index, offset=offset, text=text,
                       bad_indices=bad_indices, bbox=bbox)


# ── _align_repair: 복구의 안전 계약 ──────────────────────────────────────

def test_align_repair_substitutes_only_flagged_positions():
    """실물 사례 — `sc4B.`의 OCR 판독은 대문자 `SC49.`지만, 깨진 위치는
    숫자 하나뿐이라 `sc` 대소문자는 추출값이 남고 숫자만 바뀐다. 이것이
    "OCR을 믿되 깨진 자리에서만 믿는다"의 실체다."""
    assert panel_ocr._align_repair("sc4B.", "SC49.", (3,)) == "sc49."


def test_align_repair_preserves_length_and_unflagged_chars():
    """★계약 두 가지: (1) 길이 불변 (2) 깨지지 않은 위치 불변.
    이 둘이 "조용한 악화 금지"를 코드로 강제한다."""
    cases = [
        ("sc4B.", "SC49.", (3,)),
        ("9= HANK 7Cont.8", "56 HANK (Cont.)", (0, 1, 8, 14)),
        (";oah...", "Woah..", (0,)),
        ("taDle", "table", (2,)),
        ("CIndows", "Windows", (0,)),
        ("Bobb7Ds", "Bobby's", (4, 5)),
        ("정상", "완전히 다른 판독", ()),
    ]
    for extracted, ocr, bad in cases:
        out = panel_ocr._align_repair(extracted, ocr, bad)
        assert len(out) == len(extracted), (extracted, out)
        for i, ch in enumerate(extracted):
            if i not in bad:
                assert out[i] == ch, (extracted, out, i)


def test_align_repair_without_flags_is_identity():
    """깨진 위치가 없으면 OCR이 뭐라 하든 원문 그대로 — 복구 대상이
    아닌 텍스트에 이 함수가 잘못 불려도 손상이 없다."""
    assert panel_ocr._align_repair("table", "TABLE!!", ()) == "table"


def test_align_repair_ignores_ocr_inserted_characters():
    """길이가 다른 replace는 채택하지 않는다 — 실측: 크롭 여백을 넓혔을 때
    `7Cont.8`의 판독이 `((Cont.)`로 나와 괄호가 하나 늘었다. 어느 문자가
    어느 문자에 대응하는지 알 수 없는 구간은 고치지 않는 쪽을 택한다."""
    assert panel_ocr._align_repair("7Cont.8", "((Cont.)", (0, 6)) == "7Cont.)"


def test_align_repair_keeps_chars_ocr_did_not_see():
    """OCR이 덜 본 문자(delete)는 추출값을 남긴다 — `;oah...`의 판독은
    마침표를 하나 덜 본 `Woah..`지만 결과는 `Woah...`로 온전하다."""
    assert panel_ocr._align_repair(";oah...", "Woah..", (0,)) == "Woah..."


# ── repair_corrupt_words: 복구·유지 판정 ────────────────────────────────

def test_repair_replaces_flagged_word_in_block(monkeypatch):
    monkeypatch.delenv(panel_ocr.ENV_TEXT_REPAIR, raising=False)
    engine = _FakeEngine([[("SC49.", 0.97)]])
    _use_engine(monkeypatch, engine)
    try:
        blocks = [RawBlock(text="match sc4B.", bbox=(0, 0, 100, 20))]
        doc = _StubDoc([_word("sc4B.", (3,), offset=6)])
        out = panel_ocr.repair_corrupt_words(doc, 0, blocks)
        assert out[0].text == "match sc49."
        assert out[0].bbox == blocks[0].bbox
        assert engine.calls == 1
    finally:
        panel_ocr._reset_engines()


def test_repair_applies_multiple_words_in_one_block(monkeypatch):
    """한 블록에 깨진 단어가 여럿이어도 각 자리가 정확히 갱신돼야 한다."""
    monkeypatch.delenv(panel_ocr.ENV_TEXT_REPAIR, raising=False)
    engine = _FakeEngine([[("table", 0.99)], [("SC49.", 0.97)]])
    _use_engine(monkeypatch, engine)
    try:
        text = "hook up taDle to sc4B."
        blocks = [RawBlock(text=text, bbox=(0, 0, 100, 20))]
        doc = _StubDoc([
            _word("taDle", (2,), offset=text.index("taDle")),
            _word("sc4B.", (3,), offset=text.index("sc4B.")),
        ])
        out = panel_ocr.repair_corrupt_words(doc, 0, blocks)
        assert out[0].text == "hook up table to sc49."
    finally:
        panel_ocr._reset_engines()


@pytest.mark.parametrize("hits,why", [
    ([], "판독 없음"),
    ([("SC49.", 0.42)], "신뢰도 미달"),
    ([("씬49.", 0.99)], "비ASCII 판독"),
])
def test_repair_keeps_original_when_ocr_is_not_trustworthy(
        monkeypatch, hits, why):
    """★조용한 악화 금지 — 판독이 없거나, 신뢰도가 낮거나, 글자가 아닌
    것을 글자로 본 판독(한글/CJK 혼입)이면 원래 추출값을 그대로 남긴다.
    최악의 결과가 "못 고침"이지 "더 나빠짐"이 아니어야 한다."""
    monkeypatch.delenv(panel_ocr.ENV_TEXT_REPAIR, raising=False)
    _use_engine(monkeypatch, _FakeEngine([hits]))
    try:
        blocks = [RawBlock(text="match sc4B.", bbox=(0, 0, 100, 20))]
        doc = _StubDoc([_word("sc4B.", (3,), offset=6)])
        out = panel_ocr.repair_corrupt_words(doc, 0, blocks)
        assert out[0].text == "match sc4B.", why
    finally:
        panel_ocr._reset_engines()


def test_repair_keeps_original_when_ocr_engine_raises(monkeypatch):
    """엔진이 터져도 단어 하나 때문에 추출 전체가 실패하면 안 된다."""
    monkeypatch.delenv(panel_ocr.ENV_TEXT_REPAIR, raising=False)

    class _Boom:
        def __call__(self, _crop):
            raise RuntimeError("onnx exploded")

    _use_engine(monkeypatch, _Boom())
    try:
        blocks = [RawBlock(text="match sc4B.", bbox=(0, 0, 100, 20))]
        doc = _StubDoc([_word("sc4B.", (3,), offset=6)])
        out = panel_ocr.repair_corrupt_words(doc, 0, blocks)
        assert out[0].text == "match sc4B."
    finally:
        panel_ocr._reset_engines()


def test_repair_skips_word_whose_offset_does_not_match(monkeypatch):
    """백엔드 좌표계와 raw_blocks()가 어긋나면(오프셋의 문자열이 다르면)
    엉뚱한 자리를 덮느니 아무것도 하지 않는다."""
    monkeypatch.delenv(panel_ocr.ENV_TEXT_REPAIR, raising=False)
    engine = _FakeEngine([[("SC49.", 0.97)]])
    _use_engine(monkeypatch, engine)
    try:
        blocks = [RawBlock(text="match sc4B.", bbox=(0, 0, 100, 20))]
        doc = _StubDoc([_word("sc4B.", (3,), offset=0)])  # 실제 위치는 6
        out = panel_ocr.repair_corrupt_words(doc, 0, blocks)
        assert out[0].text == "match sc4B."
        assert engine.calls == 0  # 판독조차 시도하지 않는다
    finally:
        panel_ocr._reset_engines()


def test_repair_skips_out_of_range_block_index(monkeypatch):
    monkeypatch.delenv(panel_ocr.ENV_TEXT_REPAIR, raising=False)
    engine = _FakeEngine([[("SC49.", 0.97)]])
    _use_engine(monkeypatch, engine)
    try:
        blocks = [RawBlock(text="match sc4B.", bbox=(0, 0, 100, 20))]
        doc = _StubDoc([_word("sc4B.", (3,), block_index=7, offset=6)])
        out = panel_ocr.repair_corrupt_words(doc, 0, blocks)
        assert out[0].text == "match sc4B."
        assert engine.calls == 0
    finally:
        panel_ocr._reset_engines()


def test_repair_without_corrupt_words_renders_nothing(monkeypatch):
    """깨진 단어가 없는 페이지(=절대다수)는 렌더도 OCR도 하지 않는다 —
    전 문서 비용이 탐지분으로만 유지되는 근거."""
    monkeypatch.delenv(panel_ocr.ENV_TEXT_REPAIR, raising=False)
    engine = _FakeEngine([])
    _use_engine(monkeypatch, engine)
    try:
        blocks = [RawBlock(text="clean text", bbox=(0, 0, 100, 20))]
        doc = _StubDoc([])
        out = panel_ocr.repair_corrupt_words(doc, 0, blocks)
        assert out is blocks
        assert doc.render_calls == []
        assert engine.calls == 0
    finally:
        panel_ocr._reset_engines()


def test_repair_kill_switch_skips_detection(monkeypatch):
    """YESON_PDF_TEXT_REPAIR=0이면 탐지조차 하지 않는다."""
    monkeypatch.setenv(panel_ocr.ENV_TEXT_REPAIR, "0")
    engine = _FakeEngine([[("SC49.", 0.97)]])
    _use_engine(monkeypatch, engine)
    try:
        blocks = [RawBlock(text="match sc4B.", bbox=(0, 0, 100, 20))]
        doc = _StubDoc([_word("sc4B.", (3,), offset=6)])
        out = panel_ocr.repair_corrupt_words(doc, 0, blocks)
        assert out is blocks
        assert doc.render_calls == []
        assert engine.calls == 0
    finally:
        panel_ocr._reset_engines()


def test_repair_degrades_gracefully_without_backend_support(monkeypatch):
    """corrupt_words를 제공하지 않는 백엔드(교체 구현)에서는 현행 동작으로
    조용히 내려간다 — 복구는 부가 기능이지 추출의 전제가 아니다."""
    monkeypatch.delenv(panel_ocr.ENV_TEXT_REPAIR, raising=False)

    class _OldDoc:
        def render_png(self, page, *, dpi=120):
            raise AssertionError("렌더까지 가면 안 된다")

    blocks = [RawBlock(text="match sc4B.", bbox=(0, 0, 100, 20))]
    assert panel_ocr.repair_corrupt_words(_OldDoc(), 0, blocks) is blocks


# ── 실물 복구 검증 (Task 20, 페이지 번호는 0-based) ──────────────────────
#
# 브리프 표의 페이지 번호는 1-based였다(p483 등) — 여기서는 이 리포지터리의
# 다른 실물 테스트와 같은 0-based로 적는다(브리프 p483 = 아래 482).

def _repaired_text(doc, page: int) -> str:
    raws = panel_ocr.repair_corrupt_words(doc, page, doc.raw_blocks(page))
    return "\n".join(b.text for b in raws)


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
@pytest.mark.parametrize("page,broken,expected", [
    # 브리프 표 7건(1-based p431·p483×2·p515×2·p542×2·p975) 전부.
    (430, "sc4=", "previous sc46."),          # 씬 번호
    (430, ":>001", "incidental #2001"),       # 인시덴털 자산 번호
    (430, "Dack your Dusiness", "back your business"),
    (482, "9= HANK 7Cont.8", "56 HANK (Cont.)"),   # 깨진 큐 헤더
    (482, "sc4B", "previous sc49."),          # ★사용자가 지적한 그 씬 번호
    (482, "taDle", "hook up table"),
    (514, "=0 THATHERTON", "60 THATHERTON"),
    (514, "Dlame", "You can blame"),
    (514, "Cindows", "DX Propane Truck Windows"),
    (541, "=@ THATHERTON 7Cont.8", "63 THATHERTON (Cont.)"),
    (541, "CaDin Cindows", "DX Party Bus Cabin Windows"),
    (974, "sc109", "match sc103."),           # ★"LLM 드리프트"로 오진됐던 그 값
    (974, "Bobb7Ds", "Bobby's screen"),
    (974, "12> B*BB. 5C*NT.6", "124 BOBBY (CONT.)"),
    # 브리프 휴리스틱이 놓쳤던 추가 실물 케이스: `J`와 `H`가 **제어문자**로
    # 매핑돼 화면상 아무것도 아닌 것처럼 보였다(`JOSEPH`이 `OSEP`으로
    # 읽히던 정체). 깨진 문자열을 제어문자까지 그대로 적어야 판별력이
    # 있다 — "OSEP"으로 적으면 복구 결과 "JOSEPH"에도 들어 있어 잔존
    # 검사가 헛돈다.
    (796, "\x15OSEP\x16", "97 JOSEPH (Cont.)"),
    (853, "\x15OSEP\x16", "110 JOSEPH"),
])
def test_real_sample_repairs_corrupted_extraction(page, broken, expected):
    """추출이 깨진 실물 페이지를 렌더·재판독으로 복구한다.

    이 표가 이 태스크의 핵심 회귀 가드다 — `sc49`/`sc103`은 사용자가 직접
    지적한 값이고("특히 숫자는 틀리면 안 돼"), 이전에 "LLM 숫자 드리프트"로
    오진돼 엉뚱한 계층(숫자 보존 게이트)에 수정이 들어갔던 바로 그 값이다."""
    path = Path(SAMPLES) / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
    doc = open_pdf(path)
    try:
        before = "\n".join(b.text for b in doc.raw_blocks(page))
        assert broken in before, "전제 확인: 추출이 실제로 깨져 있어야 한다"
        after = _repaired_text(doc, page)
        assert expected in after
        assert broken not in after
    finally:
        doc.close()


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
@pytest.mark.parametrize("page", [97, 343, 483, 975])
def test_real_sample_clean_pages_unchanged(page):
    """무회귀 — 멀쩡한 페이지는 블록 리스트 객체까지 그대로 돌아온다
    (렌더도 OCR도 하지 않는 빠른 경로)."""
    path = Path(SAMPLES) / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
    doc = open_pdf(path)
    try:
        raws = doc.raw_blocks(page)
        assert doc.corrupt_words(page) == []
        assert panel_ocr.repair_corrupt_words(doc, page, raws) is raws
    finally:
        doc.close()


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
def test_real_sample_detection_cost_is_bounded():
    """비용 상한 — 탐지는 전 페이지에서 돌지만 매핑 불가 글리프가 있는
    페이지에서만 복구가 열린다. 실측(2026-07-31): 1037페이지 중 21페이지,
    탐지 전체 1.3초. 이 비율이 크게 늘면(예: 10%) OCR 비용이 폭증하므로
    회귀 신호로 잠근다."""
    path = Path(SAMPLES) / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
    doc = open_pdf(path)
    try:
        t0 = time.time()
        flagged = [p for p in range(doc.page_count) if doc.corrupt_words(p)]
        elapsed = time.time() - t0
        print(f"\ncorrupt-page detection: {len(flagged)}/{doc.page_count} "
              f"pages in {elapsed:.1f}s -> {flagged}")
        assert len(flagged) == 21
        assert len(flagged) < doc.page_count * 0.05
    finally:
        doc.close()
