from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from apps.server.domain.pdf_translate import panel_ocr
from apps.server.domain.pdf_translate.backend import open_pdf

_REGION = (0.0, 95.0, 1008.0, 460.0)


def _make_panel_pdf(tmp_path: Path, *, label_color=(1, 0, 0)) -> Path:
    """가로형 페이지 + 빨간 사각 테두리 라벨(리더라인 콜아웃 흉내) + 검정
    잡선(패널 그림선 흉내). label_color를 검정으로 주면 빨강 프리필터
    가드 검증용 fixture가 된다."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.draw_line((100, 150), (400, 300), color=(0, 0, 0), width=1.5)
    page.draw_line((500, 120), (700, 350), color=(0, 0, 0), width=1.5)
    page.draw_circle((300, 200), 40, color=(0, 0, 0), width=1.5)
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
