"""포맷 프로파일 계약 — 프로파일은 '어느 블록을 번역해 어디에 놓는가'만 안다.

새 포맷 추가 = 이 계약을 구현한 파일 하나를 profiles/에 추가하고
__init__.py의 _PROFILES에 등록. 번역·잡 관리·오버레이 실행은 공통부가 담당.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..backend import PdfDocument

_HANGUL = re.compile(r"[가-힣]")
_WS = re.compile(r"[ \t\r\n]+")


def has_hangul(text: str) -> bool:
    """한글 포함 블록은 번역 대상에서 제외한다 — 부분 번역본/번역 완료본을
    다시 넣어도 이중 번역이 생기지 않게 하는 공통 안전 규칙."""
    return bool(_HANGUL.search(text))


def normalize_ws(text: str) -> str:
    """탭·개행·연속 공백을 단일 공백으로 (Storyboard Pro는 단어 사이가 탭)."""
    return _WS.sub(" ", text).strip()


@dataclass(frozen=True)
class PdfBlock:
    page: int   # 0-based
    kind: str   # 프로파일 정의 값 (storyboard: "dialog" | "action")
    text: str   # 정규화된 원문
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class Overlay:
    page: int
    rect: tuple[float, float, float, float]
    text: str
    fontsize: float


class FormatProfile(Protocol):
    name: str
    label: str
    def detect(self, doc: PdfDocument) -> bool: ...
    def extract(self, doc: PdfDocument) -> list[PdfBlock]: ...
    def place(self, block: PdfBlock, ko_text: str,
              page_size: tuple[float, float]) -> Overlay: ...
