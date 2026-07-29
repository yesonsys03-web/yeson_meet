"""PyMuPDF 구현 — backend.PdfDocument의 유일한 프로덕션 구현(교체점)."""
from __future__ import annotations

from pathlib import Path

import fitz

from .backend import RawBlock


class MuPdfDocument:
    def __init__(self, path: Path):
        self._doc = fitz.open(path)

    @property
    def page_count(self) -> int:
        return len(self._doc)

    def page_size(self, page: int) -> tuple[float, float]:
        r = self._doc[page].rect
        return (r.width, r.height)

    def raw_blocks(self, page: int) -> list[RawBlock]:
        # 스팬 병합: Skia 웹 익스포트(리드시트형)는 Type3 폰트라 스팬이 글자
        # 단위로 파편화된다("E|p|i|so|de") — 스팬 text를 그대로 이어붙이면 온전한
        # 문자열이 된다(실측). 줄은 \n으로 잇는다.
        out: list[RawBlock] = []
        for b in self._doc[page].get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            lines = ["".join(s["text"] for s in line["spans"]) for line in b["lines"]]
            text = "\n".join(lines).strip()
            if not text:
                continue
            x0, y0, x1, y1 = b["bbox"]
            out.append(RawBlock(text=text, bbox=(x0, y0, x1, y1)))
        return out

    def producer(self) -> str:
        return str(self._doc.metadata.get("producer") or "")

    def add_freetext(self, page, rect, text, *, fontsize=12.0, color=(0, 0, 1)):
        # fontname 미지정: MuPDF 어피어런스 생성기가 CJK 폴백 폰트를 쓴다
        # (2026-07-29 스파이크 실증 — 한글 글리프 렌더·저장 확인).
        annot = self._doc[page].add_freetext_annot(
            fitz.Rect(*rect), text, fontsize=fontsize, text_color=color)
        annot.update()

    def render_png(self, page: int, *, dpi: int = 120) -> bytes:
        return self._doc[page].get_pixmap(dpi=dpi).tobytes("png")

    def save(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._doc.save(str(dest))

    def close(self) -> None:
        self._doc.close()
