"""교체 가능한 PDF 백엔드 인터페이스.

PyMuPDF(AGPL)를 이 인터페이스 뒤에 격리한다 — 외부 배포가 생기면
backend_mupdf.py만 pypdfium2+pypdf 조합으로 교체한다(호출부 무수정).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RawBlock:
    """페이지의 텍스트 블록 — 스팬 병합·좌표는 pt, 원점은 좌상단."""
    text: str
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)


class PdfDocument(Protocol):
    @property
    def page_count(self) -> int: ...
    def page_size(self, page: int) -> tuple[float, float]: ...
    def raw_blocks(self, page: int) -> list[RawBlock]: ...
    def producer(self) -> str: ...
    def add_freetext(self, page: int, rect: tuple[float, float, float, float],
                     text: str, *, fontsize: float = 12.0,
                     color: tuple[float, float, float] = (0, 0, 1)) -> None: ...
    def render_png(self, page: int, *, dpi: int = 120) -> bytes: ...
    def save(self, dest: Path) -> None: ...
    def close(self) -> None: ...


def open_pdf(path: Path) -> PdfDocument:
    """백엔드 선택의 유일한 지점."""
    from .backend_mupdf import MuPdfDocument
    return MuPdfDocument(path)
