from __future__ import annotations

from pathlib import Path

import pytest

from apps.server.domain.pdf_translate.backend import open_pdf


@pytest.fixture
def synthetic_pdf(tmp_path: Path) -> Path:
    """합성 2페이지 PDF — 백엔드 자체(pymupdf)로 만든다(테스트 전용 의존 OK)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)  # 스토리보드형 가로 페이지
    page.insert_text((72, 500), "Dialog", fontsize=8)
    page.insert_text((72, 520), "If you wanna go, then go.", fontsize=10)
    doc.new_page(width=1008, height=612)
    path = tmp_path / "s.pdf"
    doc.save(path)
    doc.close()
    return path


def test_open_pdf_reads_blocks_and_size(synthetic_pdf):
    doc = open_pdf(synthetic_pdf)
    try:
        assert doc.page_count == 2
        w, h = doc.page_size(0)
        assert (round(w), round(h)) == (1008, 612)
        texts = [b.text for b in doc.raw_blocks(0)]
        assert any("Dialog" in t for t in texts)
        assert any("wanna go" in t for t in texts)
        for b in doc.raw_blocks(0):
            x0, y0, x1, y1 = b.bbox
            assert x0 < x1 and y0 < y1
    finally:
        doc.close()


def test_freetext_korean_roundtrip_and_render(synthetic_pdf, tmp_path):
    doc = open_pdf(synthetic_pdf)
    doc.add_freetext(0, (72, 530, 400, 560), "가고 싶다면 가세요", fontsize=12)
    out = tmp_path / "out.pdf"
    doc.save(out)
    doc.close()

    doc2 = open_pdf(out)
    try:
        png = doc2.render_png(0, dpi=72)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        # 라운드트립: 주석 내용이 보존됐는지 원시 fitz로 확인
        import fitz
        raw = fitz.open(out)
        contents = [a.info.get("content", "") for a in raw[0].annots()]
        raw.close()
        assert any("가고 싶다면" in c for c in contents)
    finally:
        doc2.close()
