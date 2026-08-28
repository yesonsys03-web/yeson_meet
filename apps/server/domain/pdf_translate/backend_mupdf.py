"""PyMuPDF 구현 — backend.PdfDocument의 유일한 프로덕션 구현(교체점)."""
from __future__ import annotations

from pathlib import Path

import fitz

from .backend import CorruptWord, RawBlock

# 매핑 불가 글리프 표식 — MuPDF가 글리프의 유니코드를 결정하지 못하면
# get_texttrace()의 문자 유니코드를 U+FFFD(REPLACEMENT CHARACTER)로 준다.
# get_text()는 그런 글리프에도 **뭔가**를 내놓기 때문에(폰트 내장 인코딩
# 폴백) 깨진 문자가 평범한 문자로 위장한다 — 실물 GABE01 A1 실측:
# `sc49`가 `sc4B`로, `56 HANK (Cont.)`가 `9= HANK 7Cont.8`로 추출된다.
# 그래서 "추출 결과가 이상해 보이는가"를 텍스트로 추측하는 대신, PDF가
# 스스로 모른다고 말하는 이 표식을 detection의 단일 근거로 삼는다.
_UNMAPPED = 0xFFFD

# origin 좌표 매칭 정밀도 — texttrace(글리프 관점)와 rawdict(문자 관점)는
# 같은 문자에 같은 origin을 준다(실물 21페이지 전수 대조로 확인). 부동소수
# 표현 차이만 흡수하면 되므로 소수점 2자리로 충분하다.
_ORIGIN_NDIGITS = 2


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

    def corrupt_words(self, page: int) -> list[CorruptWord]:
        """매핑이 깨진 문자를 포함한 단어들 — 없으면 [](대다수 페이지).

        1단계(전 페이지, 실측 1037페이지 0.7초)는 get_texttrace()로 매핑
        불가 글리프의 origin만 모은다. 하나도 없으면 곧바로 [] — 비싼
        rawdict 조립은 실제로 깨진 페이지에서만 돈다.

        2단계는 rawdict로 블록을 **raw_blocks()와 똑같은 규칙으로** 다시
        조립하면서(스팬 문자 이어붙임 → 줄은 \\n → strip → 빈 블록 제외)
        각 문자가 깨졌는지를 origin으로 표시하고, 깨진 문자를 포함한
        공백 구분 단어만 뽑는다. 두 경로가 같은 문자열을 만든다는 것은
        실물 21페이지 전수 대조로 확인했고 테스트로 잠가 뒀다 — 어긋나면
        복구가 엉뚱한 자리를 덮으므로 이 정합이 계약이다.
        """
        bad_origins = self._unmapped_origins(page)
        if not bad_origins:
            return []
        out: list[CorruptWord] = []
        block_index = -1
        for b in self._doc[page].get_text("rawdict")["blocks"]:
            if b.get("type") != 0:
                continue
            chars = _rawdict_block_chars(b, bad_origins)
            text = "".join(c for c, _bad, _bbox in chars)
            stripped = text.strip()
            if not stripped:
                continue  # raw_blocks()도 빈 블록은 내보내지 않는다
            block_index += 1
            # strip()으로 잘려나간 앞쪽 길이만큼 문자 배열도 같이 민다 —
            # offset이 raw_blocks() 텍스트 기준이어야 하기 때문.
            lead = len(text) - len(text.lstrip())
            chars = chars[lead:lead + len(stripped)]
            if not any(bad for _c, bad, _bbox in chars):
                continue
            out.extend(_corrupt_words_in_block(chars, block_index))
        return out

    def _unmapped_origins(self, page: int) -> set[tuple[float, float]]:
        origins: set[tuple[float, float]] = set()
        for span in self._doc[page].get_texttrace():
            if span.get("type") != 0:  # 0=글리프 텍스트(그 외는 도형류)
                continue
            for char in span["chars"]:
                if char[0] == _UNMAPPED:
                    origins.add(_origin_key(char[2]))
        return origins

    def page_rects(self, page: int) -> list[tuple[float, float, float, float]]:
        """페이지 벡터 도형의 경계 사각형들 — 프로파일이 '필드 박스'를 찾는
        원재료다. 어떤 것이 필드 박스인지(폭·높이 문턱, 포함 관계)는 포맷별
        관례라 프로파일이 판단한다. 중복은 제거하고 (y0, x0) 오름차순."""
        seen: set[tuple[float, float, float, float]] = set()
        for d in self._doc[page].get_drawings():
            r = d["rect"]
            seen.add((r.x0, r.y0, r.x1, r.y1))
        return sorted(seen, key=lambda r: (r[1], r[0]))

    def image_rects(self, page: int) -> list[tuple[float, float, float, float]]:
        """페이지에 배치된 **래스터 이미지**의 사각형들 — 스토리보드에서는
        판넬 그림 한 칸이 이미지 하나다(Storyboard Pro 익스포트 관례).

        판넬 그림은 벡터 도형이 아니라 이미지라 `page_rects`로는 잡히지
        않는다(실측: 3단 페이지의 벡터 사각형은 전부 하단 필드 박스와 씬
        테이블). 판넬 칸 자체의 좌표가 필요한 곳은 OCR 크롭이다 —
        panel_ocr.find_panel_labels 참고. (y0, x0) 오름차순."""
        out: list[tuple[float, float, float, float]] = []
        for b in self._doc[page].get_text("dict")["blocks"]:
            if b.get("type") != 1:
                continue
            x0, y0, x1, y1 = b["bbox"]
            out.append((x0, y0, x1, y1))
        return sorted(out, key=lambda r: (r[1], r[0]))

    def producer(self) -> str:
        return str(self._doc.metadata.get("producer") or "")

    def add_freetext(self, page, rect, text, *, fontsize=12.0, color=(0, 0, 1)):
        # fontname 미지정: MuPDF 어피어런스 생성기가 CJK 폴백 폰트를 쓴다
        # (2026-07-29 스파이크 실증 — 한글 글리프 렌더·저장 확인).
        annot = self._doc[page].add_freetext_annot(
            fitz.Rect(*rect), text, fontsize=fontsize, text_color=color)
        annot.update()

    def render_png(self, page: int, *, dpi: int = 120,
                   annots: bool = True) -> bytes:
        """annots=False면 **스캔 원본만** 그린다.

        PyMuPDF 기본값은 주석 포함이라, 이미 번역된 시트를 다시 넣으면 사람이
        단 한글 주석까지 픽셀로 찍혀 OCR에 섞인다 — 그러면 한글과 붙은 영문
        노트가 has_hangul 규칙에 걸려 통째로 버려진다(재업로드·개정본 경로).
        손글씨 판독은 스캔만 보면 된다."""
        return self._doc[page].get_pixmap(dpi=dpi, annots=annots).tobytes("png")

    def save(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._doc.save(str(dest))

    def close(self) -> None:
        self._doc.close()


def _origin_key(origin) -> tuple[float, float]:
    return (round(origin[0], _ORIGIN_NDIGITS), round(origin[1], _ORIGIN_NDIGITS))


# 문자 3튜플: (문자, 매핑깨짐 여부, 문자 bbox|None). 줄 구분자로 넣는 "\n"은
# 실제 글리프가 아니라 조립 산물이라 bbox가 없다(None).
_Char = tuple[str, bool, tuple[float, float, float, float] | None]


def _rawdict_block_chars(block: dict,
                         bad_origins: set[tuple[float, float]]) -> list[_Char]:
    """rawdict 블록 → 문자 배열. raw_blocks()의 조립 규칙(스팬 문자
    이어붙임, 줄 사이 "\\n")을 그대로 따른다."""
    chars: list[_Char] = []
    for i, line in enumerate(block["lines"]):
        if i:
            chars.append(("\n", False, None))
        for span in line["spans"]:
            for ch in span["chars"]:
                chars.append((ch["c"],
                              _origin_key(ch["origin"]) in bad_origins,
                              tuple(ch["bbox"])))
    return chars


def _corrupt_words_in_block(chars: list[_Char],
                            block_index: int) -> list[CorruptWord]:
    """문자 배열에서 **깨진 문자를 포함한** 공백 구분 단어만 뽑는다.

    단어 단위인 이유(Task 20 실측): 블록/줄 전체를 렌더해 OCR에 넣으면
    RapidOCR의 텍스트 검출이 넓은 자간에서 박스를 쪼개 같은 글자를 두 번
    돌려주거나(`9= HANK 7Cont.8` → `56` + `6 HANK (Cont.)`) 없던 글자를
    끼워 넣는다(`127 B` + `BOBBY (CONT.)`). 단어 하나만 크롭하면 검출
    분할이 일어날 여지가 없어 추출 단어와 1:1로 맞출 수 있다.
    """
    out: list[CorruptWord] = []
    i = 0
    n = len(chars)
    while i < n:
        if chars[i][0].isspace():
            i += 1
            continue
        j = i
        while j < n and not chars[j][0].isspace():
            j += 1
        word = chars[i:j]
        bad_indices = tuple(k for k, (_c, bad, _b) in enumerate(word) if bad)
        if bad_indices:
            boxes = [b for _c, _bad, b in word if b is not None]
            if boxes:
                out.append(CorruptWord(
                    block_index=block_index,
                    offset=i,
                    text="".join(c for c, _bad, _b in word),
                    bad_indices=bad_indices,
                    bbox=(min(b[0] for b in boxes), min(b[1] for b in boxes),
                          max(b[2] for b in boxes), max(b[3] for b in boxes)),
                ))
        i = j
    return out
