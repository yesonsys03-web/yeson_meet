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


@dataclass(frozen=True)
class CorruptWord:
    """글리프→유니코드 매핑이 깨져 **추출 문자를 믿을 수 없는** 단어
    (Task 20). PDF가 스스로 "이 글리프의 유니코드를 모른다"고 알려준
    문자만 담는다 — 텍스트 휴리스틱 추정이 아니다.

    `block_index`/`offset`은 같은 페이지 `raw_blocks()` 결과 안의 위치다
    (리스트 인덱스 + 그 블록 `text` 안의 시작 오프셋). 두 좌표계가
    어긋나면 복구가 엉뚱한 자리를 덮으므로, 백엔드는 반드시
    `raw_blocks()`와 **같은 조립 규칙**으로 이 값을 계산해야 한다.

    `bad_indices`는 `text` 안에서 매핑이 깨진 문자의 인덱스다 — 복구는
    이 위치의 문자만 바꿀 수 있다(그 외 위치는 추출값이 옳다는 게 PDF의
    주장이므로 건드리지 않는다).
    """
    block_index: int
    offset: int
    text: str
    bad_indices: tuple[int, ...]
    bbox: tuple[float, float, float, float]  # 단어 자체의 bbox(pt)


class PdfDocument(Protocol):
    @property
    def page_count(self) -> int: ...
    def page_size(self, page: int) -> tuple[float, float]: ...
    def raw_blocks(self, page: int) -> list[RawBlock]: ...
    def page_rects(self, page: int) -> list[tuple[float, float, float, float]]: ...
    def image_rects(self, page: int) -> list[tuple[float, float, float, float]]: ...
    def corrupt_words(self, page: int) -> list[CorruptWord]: ...
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
