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


# ── 깨진 추출 문자 탐지 (Task 20) ────────────────────────────────────────
#
# 탐지 근거는 텍스트 휴리스틱이 아니라 PDF 자신의 표식이다 — MuPDF가
# 글리프의 유니코드를 결정하지 못하면 get_texttrace()가 U+FFFD를 준다.
# 합성 PDF로는 그런 깨진 폰트를 만들 수 없으므로, **깨진 글리프 목록만**
# 주입(_unmapped_origins 몽키패치)하고 나머지 경로(rawdict 재조립, 오프셋
# 산출, 단어 묶기)는 진짜 페이지 위에서 그대로 돌린다.


def _origin_of(doc, page: int, needle: str):
    """합성 페이지에서 특정 문자의 origin을 실제 rawdict로 찾아 준다."""
    from apps.server.domain.pdf_translate.backend_mupdf import _origin_key
    for b in doc._doc[page].get_text("rawdict")["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b["lines"]:
            for span in line["spans"]:
                for ch in span["chars"]:
                    if ch["c"] == needle:
                        return _origin_key(ch["origin"])
    raise AssertionError(f"{needle!r} not found on page {page}")


def test_corrupt_words_empty_when_nothing_unmapped(synthetic_pdf):
    """정상 페이지는 빈 목록 — 비싼 rawdict 재조립조차 하지 않는 빠른 경로."""
    doc = open_pdf(synthetic_pdf)
    try:
        assert doc.corrupt_words(0) == []
        assert doc.corrupt_words(1) == []
    finally:
        doc.close()


def test_corrupt_words_offsets_match_raw_blocks(synthetic_pdf, monkeypatch):
    """★계약: block_index/offset은 raw_blocks() 결과 안의 위치여야 한다.

    두 좌표계가 어긋나면 복구가 엉뚱한 자리를 덮는다 — corrupt_words는
    rawdict(문자 관점)로, raw_blocks는 dict(스팬 관점)로 텍스트를 조립하므로
    같은 문자열이 나온다는 보장이 계약의 핵심이다. 여기서는 실제 블록의
    실제 문자를 깨진 것으로 표시하고, 돌려받은 좌표로 원문을 잘라 그
    단어가 정확히 나오는지 확인한다."""
    doc = open_pdf(synthetic_pdf)
    try:
        target = _origin_of(doc, 0, "w")  # "wanna"의 첫 글자
        monkeypatch.setattr(type(doc), "_unmapped_origins",
                            lambda self, page: {target})
        words = doc.corrupt_words(0)
        assert len(words) == 1
        word = words[0]
        assert word.text == "wanna"
        assert word.bad_indices == (0,)
        blocks = doc.raw_blocks(0)
        sliced = blocks[word.block_index].text[
            word.offset:word.offset + len(word.text)]
        assert sliced == word.text
        x0, y0, x1, y1 = word.bbox
        assert x0 < x1 and y0 < y1
    finally:
        doc.close()


def test_corrupt_words_groups_by_whitespace_word(synthetic_pdf, monkeypatch):
    """깨진 문자가 여러 단어에 흩어져 있으면 단어마다 별개 항목이 된다 —
    단어 단위여야 OCR 크롭이 추출 단어와 1:1로 맞는다."""
    doc = open_pdf(synthetic_pdf)
    try:
        targets = {_origin_of(doc, 0, "w"), _origin_of(doc, 0, "D")}
        monkeypatch.setattr(type(doc), "_unmapped_origins",
                            lambda self, page: targets)
        words = doc.corrupt_words(0)
        assert sorted(w.text for w in words) == ["Dialog", "wanna"]
        blocks = doc.raw_blocks(0)
        for w in words:
            assert blocks[w.block_index].text[
                w.offset:w.offset + len(w.text)] == w.text
    finally:
        doc.close()
