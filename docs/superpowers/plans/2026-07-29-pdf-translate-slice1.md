# PDF 스토리보드 번역 — 슬라이스 1 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 데스크톱 앱에 "스토리보드 번역" 최상위 탭을 추가 — PDF 업로드 → Storyboard Pro 포맷 자동 감지 → Dialog/Action Notes 블록을 기존 번역 엔진으로 번역 → FreeText 주석 오버레이 → 프리뷰(페이지 PNG)·번역 PDF 다운로드.

**Architecture:** `apps/server/domain/pdf_translate/` 신규 패키지 — 교체 가능 PDF 백엔드(`backend.py` 인터페이스 + `backend_mupdf.py` PyMuPDF 구현) + 포맷 프로파일 플러그인(`profiles/`) + 공통 번역/오버레이/잡 러너. 잡 패턴은 `video_captions/job_tasks.py` 미러(세마포어·세대 가드). UI는 A안: `ConsoleView`에 `"pdf"` 추가, 신규 `PdfTranslatePanel` 완전 격리.

**Tech Stack:** PyMuPDF(신규, AGPL — 교체점은 backend_mupdf.py 하나), FastAPI, SQLAlchemy(+alembic 0007), Tauri(Rust reqwest 멀티파트), React.

**설계 근거·포맷 실측:** `docs/pdf-translation-feasibility-2026-07-29.md` (확정 결정 섹션 포함). 스파이크 실증(2026-07-29): PyMuPDF 1.28 `add_freetext_annot`은 fontname 지정 없이도 한글 글리프를 정상 렌더·저장한다(어피어런스에 CJK 폴백 폰트 포함).

## Global Constraints

- 작업 브랜치: `feat/pdf-translate-slice1` (main 직접 커밋 금지 — 리포에 브랜치 보호는 없지만 정책).
- VibeLign 규칙: 가능한 가장 작은 패치. 기존 파일 수정은 명시된 지점만. `# === ANCHOR: NAME_START/END ===` 마커가 있는 파일은 마커 밖으로 나가지 말 것.
- **기존 자막 번역 동작 무변경**: Task 3의 `prompt_builder` 주입은 기본값이 기존 `build_translation_prompt`여야 하고, 잠금 테스트로 고정한다.
- 서버 테스트 실행 커맨드(로컬, Docker 없음):
  `TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest <경로> -v`
  (conftest가 Postgres DSN 기본 — SQLite 지정 필수. 기존 20개 SQLite 비호환 실패는 이 기능과 무관.)
- 프런트 게이트: `pnpm -C apps/desktop test` + `pnpm -C apps/desktop build` (tsc+vite; ESLint 없음).
- Rust 게이트: `cd apps/desktop/src-tauri && cargo check`.
- 커밋 전 `ruff check <변경한 파이썬 경로>`.
- 실물 샘플 경로(로컬 전용, 테스트는 env 가드): `/Users/usabatch/coding/EASA01_Shipping_Documents`.
- 커밋 메시지: `feat(pdf-translate): <한국어 요약>` 형식(리포 관례).
- **dev에서 새 서버 라우트는 재동결(build-server.sh) 전까지 데스크톱 앱에서 404** — Task 10 전에는 uvicorn 직접 기동으로 검증.

---

### Task 1: pymupdf 의존성 + PDF 백엔드 인터페이스 + PyMuPDF 구현

**Files:**
- Modify: `apps/server/pyproject.toml` (dependencies 배열 끝에 1줄)
- Create: `apps/server/domain/pdf_translate/__init__.py` (빈 파일)
- Create: `apps/server/domain/pdf_translate/backend.py`
- Create: `apps/server/domain/pdf_translate/backend_mupdf.py`
- Test: `apps/server/tests/test_pdf_backend.py`

**Interfaces (Produces):**
- `backend.RawBlock(text: str, bbox: tuple[float,float,float,float])` (frozen dataclass)
- `backend.PdfDocument` Protocol: `page_count: int`(property), `page_size(page:int)->tuple[float,float]`, `raw_blocks(page:int)->list[RawBlock]`, `producer()->str`, `add_freetext(page:int, rect:tuple, text:str, *, fontsize:float=12.0, color:tuple=(0,0,1))->None`, `render_png(page:int, *, dpi:int=120)->bytes`, `save(dest:Path)->None`, `close()->None`
- `backend.open_pdf(path: Path) -> PdfDocument` — 백엔드 선택의 유일한 지점

**Steps:**

- [ ] **1-1: 브랜치 생성 + 의존성 추가**

```bash
git checkout -b feat/pdf-translate-slice1
```

`apps/server/pyproject.toml` dependencies 배열 마지막(`"rapidocr-onnxruntime>=1.3",` 다음)에 추가:

```toml
  "pymupdf>=1.26",
```

```bash
uv lock && uv sync --all-packages
.venv/bin/python -c "import pymupdf, fitz; print(pymupdf.__doc__)"
```
Expected: 버전 문자열 출력. (`uv lock` 누락 시 CI `uv sync --frozen`이 3플랫폼 전부 실패한다.)

- [ ] **1-2: 실패하는 테스트 작성** — `apps/server/tests/test_pdf_backend.py`

```python
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
```

- [ ] **1-3: 실패 확인**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_backend.py -v
```
Expected: FAIL — `ModuleNotFoundError: apps.server.domain.pdf_translate`

- [ ] **1-4: 구현** — `backend.py`:

```python
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
```

`backend_mupdf.py`:

```python
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
```

`__init__.py`는 빈 파일로 생성.

- [ ] **1-5: 통과 확인 + 커밋**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_backend.py -v
ruff check apps/server/domain/pdf_translate apps/server/tests/test_pdf_backend.py
git add apps/server/pyproject.toml uv.lock apps/server/domain/pdf_translate apps/server/tests/test_pdf_backend.py
git commit -m "feat(pdf-translate): PDF 백엔드 인터페이스 + PyMuPDF 구현 (pymupdf 의존성 추가)"
```

---

### Task 2: 포맷 프로파일 계약 + Storyboard Pro 프로파일 + 자동 감지

**Files:**
- Create: `apps/server/domain/pdf_translate/profiles/__init__.py`
- Create: `apps/server/domain/pdf_translate/profiles/base.py`
- Create: `apps/server/domain/pdf_translate/profiles/storyboard.py`
- Test: `apps/server/tests/test_pdf_profiles.py`

**Interfaces:**
- Consumes: Task 1의 `PdfDocument`, `RawBlock`
- Produces:
  - `base.PdfBlock(page:int, kind:str, text:str, bbox:tuple)` (frozen dataclass)
  - `base.Overlay(page:int, rect:tuple, text:str, fontsize:float)` (frozen dataclass)
  - `base.FormatProfile` Protocol: `name:str`, `label:str`, `detect(doc)->bool`, `extract(doc)->list[PdfBlock]`, `place(block:PdfBlock, ko_text:str, page_size:tuple)->Overlay`
  - `base.has_hangul(text:str)->bool`, `base.normalize_ws(text:str)->str`
  - `profiles.detect_profile(doc) -> FormatProfile | None` (등록 순서대로 첫 매치)

**Steps:**

- [ ] **2-1: 실패하는 테스트 작성** — `apps/server/tests/test_pdf_profiles.py`

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from apps.server.domain.pdf_translate.backend import open_pdf
from apps.server.domain.pdf_translate.profiles import detect_profile
from apps.server.domain.pdf_translate.profiles.base import has_hangul, normalize_ws
from apps.server.domain.pdf_translate.profiles.storyboard import StoryboardProfile


def _make_storyboard_pdf(tmp_path: Path, *, korean_dialog: bool = False) -> Path:
    """Storyboard Pro export를 흉내 낸 합성 페이지 — 가로 1008x612,
    'Dialog'/'Action Notes' 라벨 아래에 내용 블록."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((680, 460), "Dialog", fontsize=8)
    dialog = "이미 번역된 대사" if korean_dialog else "If\tyou\twanna\tgo, then go."
    page.insert_text((680, 478), dialog, fontsize=10)
    page.insert_text((72, 560), "Action Notes", fontsize=8)
    page.insert_text((72, 578), "HANK walks to the door.", fontsize=10)
    path = tmp_path / ("sb_ko.pdf" if korean_dialog else "sb.pdf")
    doc.save(path)
    doc.close()
    return path


def test_helpers():
    assert has_hangul("씬 내내") is True
    assert has_hangul("If you wanna") is False
    assert normalize_ws("If\tyou\twanna  go\n") == "If you wanna go"


def test_detect_and_extract_storyboard(tmp_path):
    doc = open_pdf(_make_storyboard_pdf(tmp_path))
    try:
        profile = detect_profile(doc)
        assert profile is not None and profile.name == "storyboard"
        blocks = profile.extract(doc)
        kinds = {b.kind for b in blocks}
        assert kinds == {"dialog", "action"}
        dialog = next(b for b in blocks if b.kind == "dialog")
        assert dialog.text == "If you wanna go, then go."  # 탭 정규화
        assert dialog.page == 0
    finally:
        doc.close()


def test_extract_skips_hangul_blocks(tmp_path):
    doc = open_pdf(_make_storyboard_pdf(tmp_path, korean_dialog=True))
    try:
        blocks = StoryboardProfile().extract(doc)
        assert all(b.kind != "dialog" for b in blocks)  # 한글 대사는 제외
    finally:
        doc.close()


def test_place_returns_rect_below_block_within_page(tmp_path):
    doc = open_pdf(_make_storyboard_pdf(tmp_path))
    try:
        profile = StoryboardProfile()
        block = next(b for b in profile.extract(doc) if b.kind == "dialog")
        ov = profile.place(block, "가고 싶다면 가세요", doc.page_size(0))
        x0, y0, x1, y1 = ov.rect
        assert y0 >= block.bbox[3]          # 원문 아래
        assert y1 <= 612 and x1 <= 1008     # 페이지 안
        assert ov.page == 0 and ov.fontsize == 12.0
    finally:
        doc.close()


def test_detect_rejects_portrait(tmp_path):
    import fitz
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    p = tmp_path / "portrait.pdf"
    doc.save(p)
    doc.close()
    d = open_pdf(p)
    try:
        assert detect_profile(d) is None
    finally:
        d.close()


SAMPLES = os.environ.get("YESON_PDF_SAMPLES")


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
def test_real_storyboard_sample():
    """실물 검증(로컬 전용): GABE01_A1 앞 30페이지에서 감지 + 블록 추출."""
    path = Path(SAMPLES) / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
    doc = open_pdf(path)
    try:
        profile = detect_profile(doc)
        assert profile is not None and profile.name == "storyboard"
        blocks = [b for b in profile.extract(doc) if b.page < 30]
        assert len(blocks) >= 1
        assert all("\t" not in b.text for b in blocks)
    finally:
        doc.close()
```

- [ ] **2-2: 실패 확인**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_profiles.py -v
```
Expected: FAIL — `ModuleNotFoundError: ...profiles`

- [ ] **2-3: 구현** — `profiles/base.py`:

```python
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
    def detect(self, doc: "PdfDocument") -> bool: ...
    def extract(self, doc: "PdfDocument") -> list[PdfBlock]: ...
    def place(self, block: PdfBlock, ko_text: str,
              page_size: tuple[float, float]) -> Overlay: ...
```

`profiles/storyboard.py`:

```python
"""Storyboard Pro 익스포트 프로파일 (King of the Hill GABE01 실측 기반).

페이지당 판넬 1장 + 고정 위치 'Dialog'/'Action Notes' 라벨. 템플릿 익스포트라
라벨 x좌표가 전 페이지 동일 — 라벨 블록을 찾고 그 아래 가장 가까운 블록을
내용으로 본다(라벨과 내용이 한 블록으로 붙어 나오는 변형도 처리).
"""
from __future__ import annotations

import math

from ..backend import PdfDocument, RawBlock
from .base import Overlay, PdfBlock, has_hangul, normalize_ws

_FIELDS = (("Dialog", "dialog"), ("Action Notes", "action"))
_FONTSIZE = 12.0          # 기존 수작업 번역본 실측(AdobeMyungjoStd 12pt)
_GAP = 4.0                # 원문 블록과 주석 사이 여백(pt)
_MIN_WIDTH = 280.0        # 주석 박스 최소 폭
_DETECT_PAGES = 3


class StoryboardProfile:
    name = "storyboard"
    label = "스토리보드 (Storyboard Pro)"

    def detect(self, doc: PdfDocument) -> bool:
        w, h = doc.page_size(0)
        if w <= h:  # 가로형이 아니면 아님
            return False
        found: set[str] = set()
        for page in range(min(_DETECT_PAGES, doc.page_count)):
            for b in doc.raw_blocks(page):
                t = normalize_ws(b.text)
                for label, _kind in _FIELDS:
                    if t == label or t.startswith(label):
                        found.add(label)
        return len(found) == len(_FIELDS)

    def extract(self, doc: PdfDocument) -> list[PdfBlock]:
        out: list[PdfBlock] = []
        for page in range(doc.page_count):
            raws = doc.raw_blocks(page)
            for label, kind in _FIELDS:
                content = _field_content(raws, label)
                if content is None:
                    continue
                text = normalize_ws(content.text)
                if not text or has_hangul(text):
                    continue
                out.append(PdfBlock(page=page, kind=kind, text=text,
                                    bbox=content.bbox))
        return out

    def place(self, block: PdfBlock, ko_text: str,
              page_size: tuple[float, float]) -> Overlay:
        page_w, page_h = page_size
        x0 = block.bbox[0]
        x1 = min(page_w - 8.0, max(block.bbox[2], x0 + _MIN_WIDTH))
        width = x1 - x0
        height = _estimate_height(ko_text, width, _FONTSIZE)
        y0 = block.bbox[3] + _GAP
        y1 = y0 + height
        if y1 > page_h - 4.0:  # 페이지 하단을 넘으면 위로 밀어 올린다
            y1 = page_h - 4.0
            y0 = max(0.0, y1 - height)
        return Overlay(page=block.page, rect=(x0, y0, x1, y1),
                       text=ko_text, fontsize=_FONTSIZE)


def _field_content(raws: list[RawBlock], label: str) -> RawBlock | None:
    """라벨과 정확히 일치하는 블록의 '아래 가장 가까운' 블록을 내용으로.
    라벨+내용이 한 블록이면 라벨 접두를 떼고 나머지를 내용으로."""
    label_block = None
    for b in raws:
        t = normalize_ws(b.text)
        if t == label:
            label_block = b
            break
        if t.startswith(label) and len(t) > len(label):
            rest = t[len(label):].lstrip(" :")
            if rest:
                return RawBlock(text=rest, bbox=b.bbox)
    if label_block is None:
        return None
    lx0, _ly0, _lx1, ly1 = label_block.bbox
    candidates = [b for b in raws
                  if b.bbox[1] >= ly1 - 1.0 and abs(b.bbox[0] - lx0) < 60.0
                  and normalize_ws(b.text) != label]
    if not candidates:
        return None
    return min(candidates, key=lambda b: b.bbox[1])


def _estimate_height(text: str, width: float, fontsize: float) -> float:
    """CJK 근사 폭(글자당 ≈ fontsize pt)으로 줄수 → 박스 높이."""
    chars_per_line = max(8, int(width / fontsize))
    lines = max(1, math.ceil(len(text) / chars_per_line))
    return (lines + 0.5) * fontsize * 1.25
```

`profiles/__init__.py`:

```python
from ..backend import PdfDocument
from .base import FormatProfile
from .storyboard import StoryboardProfile

# 등록 순서 = 감지 우선순위. 새 포맷은 여기 한 줄 추가.
_PROFILES: tuple[FormatProfile, ...] = (StoryboardProfile(),)


def detect_profile(doc: PdfDocument) -> FormatProfile | None:
    for profile in _PROFILES:
        if profile.detect(doc):
            return profile
    return None
```

- [ ] **2-4: 통과 확인(합성) + 실물 확인**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_profiles.py -v
YESON_PDF_SAMPLES=/Users/usabatch/coding/EASA01_Shipping_Documents TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_profiles.py::test_real_storyboard_sample -v
```
Expected: 전부 PASS. 실물 테스트가 실패하면 `_field_content`의 x 허용폭(60pt)·라벨 매칭을 실물 좌표에 맞춰 조정(감지·추출 규칙 수정은 이 파일 안에서만).

- [ ] **2-5: 커밋**

```bash
ruff check apps/server/domain/pdf_translate apps/server/tests/test_pdf_profiles.py
git add apps/server/domain/pdf_translate/profiles apps/server/tests/test_pdf_profiles.py
git commit -m "feat(pdf-translate): 포맷 프로파일 계약 + Storyboard Pro 프로파일·자동 감지"
```

---

### Task 3: 번역 프로바이더에 prompt_builder 주입 (기존 동작 무변경)

**Files:**
- Modify: `apps/server/domain/video_captions/translate.py` (GeminiFlashTranslator `__init__`/`translate_batch`)
- Modify: `apps/server/domain/video_captions/translate_cli.py` (CliTranslator `__init__`/`translate_batch`, `create_translator`)
- Test: `apps/server/tests/test_pdf_prompt_injection.py`

**Interfaces:**
- Produces: `create_translator(provider=None, cli_model=None, *, prompt_builder: Callable[[list[str]], str] | None = None)` — gemini·CLI(claude/codex/agy/opencode) 엔진에 전달. qwen/apple 계열은 자체 프롬프트 유지(무시) — 문서화된 제약.

**Steps:**

- [ ] **3-1: 실패하는 테스트 작성** — `apps/server/tests/test_pdf_prompt_injection.py`

```python
from __future__ import annotations

import json

import pytest

from apps.server.domain.video_captions.translate import GeminiFlashTranslator


@pytest.mark.asyncio
async def test_default_prompt_is_subtitle_prompt(monkeypatch):
    """잠금: prompt_builder 미지정 시 기존 자막 프롬프트 그대로 — 영상 번역 무변경."""
    captured = {}

    async def _spy(self, prompt):
        captured["prompt"] = prompt
        return json.dumps(["안녕"])

    monkeypatch.setattr(GeminiFlashTranslator, "_generate", _spy)
    out = await GeminiFlashTranslator(api_key="x").translate_batch(["Hi"])
    assert out == ["안녕"]
    assert "subtitle line" in captured["prompt"]


@pytest.mark.asyncio
async def test_custom_prompt_builder_is_used(monkeypatch):
    captured = {}

    async def _spy(self, prompt):
        captured["prompt"] = prompt
        return json.dumps(["안녕"])

    monkeypatch.setattr(GeminiFlashTranslator, "_generate", _spy)
    t = GeminiFlashTranslator(api_key="x",
                              prompt_builder=lambda texts: f"CUSTOM {len(texts)}")
    await t.translate_batch(["Hi"])
    assert captured["prompt"] == "CUSTOM 1"


def test_create_translator_passes_builder_to_cli(monkeypatch):
    from apps.server.domain.video_captions import translate_cli as tc
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    builder = lambda texts: "B"  # noqa: E731
    t = tc.create_translator("claude", prompt_builder=builder)
    assert isinstance(t, tc.CliTranslator)
    assert t._prompt_builder is builder
```

- [ ] **3-2: 실패 확인**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_prompt_injection.py -v
```
Expected: FAIL — `TypeError: unexpected keyword argument 'prompt_builder'`

- [ ] **3-3: 최소 구현**

`translate.py` — `GeminiFlashTranslator.__init__` 시그니처를 다음으로 바꾸고(다른 줄 무변경):

```python
    def __init__(self, api_key: str | None = None, model: str | None = None,
                 prompt_builder: Callable[[list[str]], str] | None = None):
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._model = model or os.environ.get(TRANSLATE_MODEL_ENV, DEFAULT_TRANSLATE_MODEL)
        # PDF 번역 등 다른 도메인이 자기 프롬프트를 주입하는 플러그 지점.
        # 기본값은 기존 자막 프롬프트 — 미지정 호출부는 동작 불변.
        self._prompt_builder = prompt_builder or build_translation_prompt
```

`translate_batch` 첫 줄을 `prompt = self._prompt_builder(texts)`로 교체.

`translate_cli.py` — `CliTranslator.__init__`에 `prompt_builder=None` 파라미터 추가 + `self._prompt_builder = prompt_builder`(지연 import 순환 방지: `translate_batch`에서 `builder = self._prompt_builder or build_translation_prompt`). `translate_batch`의 `prompt = build_translation_prompt(texts)`를 `prompt = (self._prompt_builder or build_translation_prompt)(texts)`로 교체.

`create_translator` 시그니처:

```python
def create_translator(
    provider: str | None = None, cli_model: str | None = None,
    *, prompt_builder=None,
) -> TranslationProvider:
```

gemini 분기 → `GeminiFlashTranslator(prompt_builder=prompt_builder)`, CLI 분기(`_BACKENDS` 경유 CliTranslator 생성부) → `prompt_builder=prompt_builder` 전달. apple/qwen 분기는 무변경(자체 프롬프트/MT — docstring에 한 줄: "prompt_builder는 gemini·CLI 엔진에만 적용").

- [ ] **3-4: 통과 확인 + 기존 번역 테스트 회귀 확인 + 커밋**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_prompt_injection.py apps/server/tests/test_video_translate.py apps/server/tests/test_api_translate_models.py -v
ruff check apps/server/domain/video_captions/translate.py apps/server/domain/video_captions/translate_cli.py
git add -A apps/server/domain/video_captions apps/server/tests/test_pdf_prompt_injection.py
git commit -m "feat(pdf-translate): 번역 프로바이더 prompt_builder 주입 (기본값=자막 프롬프트, 무변경 잠금)"
```
(`test_video_translate.py`가 없으면 `ls apps/server/tests | grep translate`로 실제 자막 번역 테스트 파일명을 찾아 그것을 돌린다 — 반드시 기존 번역 스위트 1개 이상 회귀 확인.)

---

### Task 4: PDF 전용 프롬프트 + 리질리언트 배치 번역

**Files:**
- Create: `apps/server/domain/pdf_translate/translate_blocks.py`
- Test: `apps/server/tests/test_pdf_translate_blocks.py`

**Interfaces:**
- Consumes: `TranslationProvider`(translate_batch), `TranslationError`, `apply_ko_corrections`, `glossary_block`
- Produces:
  - `build_pdf_prompt(texts: list[str]) -> str`
  - `async translate_texts(texts: list[str], provider, *, chunk_size=50, progress_cb: Callable[[float], Awaitable[None]] | None = None) -> list[str]` — 개수불일치 이분탐색, 1줄 실패는 원문 유지, `apply_ko_corrections` 적용

**Steps:**

- [ ] **4-1: 실패하는 테스트 작성** — `apps/server/tests/test_pdf_translate_blocks.py`

```python
from __future__ import annotations

import pytest

from apps.server.domain.pdf_translate.translate_blocks import (
    build_pdf_prompt, translate_texts)
from apps.server.domain.video_captions.translate import TranslationError


class FakeTranslator:
    def __init__(self, script):
        self.script = list(script)  # 호출별 반환값 또는 예외
        self.calls = []

    async def translate_batch(self, texts):
        self.calls.append(list(texts))
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        if action == "echo-ko":
            return [f"KO:{t}" for t in texts]
        return action


def test_pdf_prompt_mentions_production_not_subtitles():
    p = build_pdf_prompt(["HANK walks."])
    assert "subtitle" not in p
    assert "JSON array" in p
    assert "HANK walks." in p


@pytest.mark.asyncio
async def test_translate_texts_happy_path():
    t = FakeTranslator(["echo-ko"])
    out = await translate_texts(["a", "b"], t)
    assert out == ["KO:a", "KO:b"]


@pytest.mark.asyncio
async def test_translate_texts_bisects_on_count_mismatch():
    # 1차: 2줄 요청에 1줄 반환(불일치) → 반으로 쪼개 재시도
    t = FakeTranslator([["하나"], ["A번역"], ["B번역"]])
    out = await translate_texts(["a", "b"], t)
    assert out == ["A번역", "B번역"]
    assert t.calls == [["a", "b"], ["a"], ["b"]]


@pytest.mark.asyncio
async def test_translate_texts_keeps_source_on_single_failure():
    t = FakeTranslator([TranslationError("boom"), TranslationError("boom")])
    out = await translate_texts(["a"], t)
    assert out == ["a"]  # 원문 유지 폴백 (is_source_copy 규약과 동일)


@pytest.mark.asyncio
async def test_progress_cb_called():
    fracs = []

    async def cb(f):
        fracs.append(f)

    t = FakeTranslator(["echo-ko", "echo-ko"])
    await translate_texts([str(i) for i in range(60)], t, chunk_size=50,
                          progress_cb=cb)
    assert fracs == [50 / 60, 1.0]
```

- [ ] **4-2: 실패 확인**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_translate_blocks.py -v
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **4-3: 구현** — `translate_blocks.py`:

```python
"""PDF 블록 배치 번역 — 자막 도메인의 엔진(create_translator)을 그대로 쓰되
프롬프트와 리질리언트 배치는 이 도메인 소유다.

_translate_resilient(자막 모듈 private)를 import하지 않고 동일 알고리즘을
여기 둔다 — 자막 쪽 리팩토링이 PDF 번역을 흔들지 않게 도메인을 분리한다.
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable

from apps.server.ai.glossary import apply_ko_corrections, glossary_block
from apps.server.domain.video_captions.translate import (TranslationError,
                                                         TranslationProvider)

logger = logging.getLogger("yeson.pdf.translate")


def build_pdf_prompt(texts: list[str]) -> str:
    """제작 문서(스토리보드 대사·액션 노트) 배열 → KO 번역 지시 프롬프트."""
    numbered = json.dumps(texts, ensure_ascii=False)
    return (
        "Translate each English text block from an animation production "
        "document (storyboard dialog and action notes) into natural Korean "
        "for Korean animation staff.\n"
        "Keep imperative production notes in polite 요청형 (예: '...하세요'). "
        "Keep dialogue natural and faithful to the tone of the source.\n"
        "Do NOT translate asset IDs, scene/panel codes, or file-name-like "
        "tokens (e.g. TGNO_PizzaBox_CL_V01, 5LBW03_07_01) — copy them "
        "unchanged.\n"
        "Input is a JSON array of strings; return ONLY a JSON array of the "
        "same length with the Korean translations in the same order.\n"
        "Return ONLY the JSON array. No prose, no markdown fences.\n"
        "Use this glossary:\n"
        + glossary_block()
        + "\n\nInput:\n" + numbered
    )


async def _resilient(provider: TranslationProvider, texts: list[str],
                     cause: str | None = None) -> list[str]:
    """개수 불일치/오류에 견디는 배치 — 반으로 쪼개 재시도, 1줄 실패는 원문 유지."""
    if not texts:
        return []
    try:
        result = await provider.translate_batch(texts)
        if len(result) == len(texts):
            return result
        cause = f"반환 개수 불일치({len(result)} != {len(texts)})"
    except TranslationError as exc:
        cause = str(exc)
    if len(texts) == 1:
        logger.warning("pdf-translate: 1블록 번역 실패(%s) — 원문 유지: %r",
                       cause or "원인 미상", texts[0][:60])
        return list(texts)
    mid = len(texts) // 2
    left = await _resilient(provider, texts[:mid], cause)
    right = await _resilient(provider, texts[mid:], cause)
    return left + right


async def translate_texts(
    texts: list[str],
    provider: TranslationProvider,
    *,
    chunk_size: int = 50,
    progress_cb: Callable[[float], Awaitable[None]] | None = None,
) -> list[str]:
    out: list[str] = []
    for i in range(0, len(texts), chunk_size):
        chunk = texts[i:i + chunk_size]
        translated = await _resilient(provider, chunk)
        out.extend(apply_ko_corrections(t.strip()) for t in translated)
        logger.info("pdf-translate: %d/%d blocks", len(out), len(texts))
        if progress_cb is not None:
            await progress_cb(len(out) / len(texts))
    return out
```

- [ ] **4-4: 통과 확인 + 커밋**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_translate_blocks.py -v
ruff check apps/server/domain/pdf_translate
git add apps/server/domain/pdf_translate/translate_blocks.py apps/server/tests/test_pdf_translate_blocks.py
git commit -m "feat(pdf-translate): PDF 전용 프롬프트 + 리질리언트 배치 번역"
```

---

### Task 5: PdfJob 모델 + alembic 0007 + 잡 파일 스토어

**Files:**
- Modify: `apps/server/db/models.py` (`MODELS_VIDEO_SEGMENT_END` 앵커 뒤, `MODELS_END` 앵커 앞에 새 앵커 블록 추가)
- Create: `apps/server/db/alembic/versions/0007_pdf_jobs.py`
- Create: `apps/server/domain/pdf_translate/pdf_store.py`
- Test: `apps/server/tests/test_pdf_store.py`

**Interfaces:**
- Produces:
  - `models.PdfJob` — 컬럼: `id`, `external_id`(Uuid unique), `owner_user_id`(FK app_user.id), `title`(String 255), `source_ref`(Text, 원 파일명), `format`(String 32, nullable), `translate_provider`(String 32, nullable), `translate_cli_model`(String 128, nullable), `status`(String 16, default "queued"), `progress`(Integer, default 0), `error`(Text nullable), `source_path`(Text nullable), `translated_path`(Text nullable), `page_count`(Integer nullable), `block_count`(Integer nullable), `created_at`/`updated_at`(VideoJob과 동일 패턴)
  - status 값: `queued|extracting|translating|overlaying|done|error|cancelled`
  - `pdf_store.pdf_jobs_root() -> Path` = `$STORAGE_ROOT/pdf_jobs`, `pdf_store.pdf_job_dir(external_id) -> Path`

**Steps:**

- [ ] **5-1: 실패하는 테스트 작성** — `apps/server/tests/test_pdf_store.py`

```python
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from apps.server.db.models import PdfJob
from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir, pdf_jobs_root


def test_pdf_job_dir_under_storage_root(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    eid = uuid4()
    assert pdf_jobs_root() == tmp_path / "pdf_jobs"
    assert pdf_job_dir(eid) == tmp_path / "pdf_jobs" / str(eid)


async def test_pdf_job_row_roundtrip(db_session, admin_user):
    job = PdfJob(external_id=uuid4(), owner_user_id=admin_user.id,
                 title="GABE01_A1", source_ref="GABE01_A1.pdf", status="queued")
    db_session.add(job)
    await db_session.commit()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job.id))).scalar_one()
    assert row.status == "queued" and row.progress == 0
    assert row.format is None and row.translated_path is None
```

- [ ] **5-2: 실패 확인**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_store.py -v
```
Expected: FAIL — `ImportError: cannot import name 'PdfJob'`

- [ ] **5-3: 구현**

`models.py` — `MODELS_VIDEO_SEGMENT_END`와 `MODELS_END` 사이에 삽입(컬럼 타입 표기는 위 VideoJob 블록과 동일 스타일):

```python
# === ANCHOR: MODELS_PDF_JOB_START ===
class PdfJob(Base):
    __tablename__ = "pdf_job"

    id: Mapped[int] = mapped_column(_BigIntId, primary_key=True, autoincrement=True)
    external_id: Mapped[PyUUID] = mapped_column(
        Uuid(as_uuid=True), unique=True, nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(
        _BigIntId, ForeignKey("app_user.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # 원 업로드 파일명 — 다운로드 파일명(<stem>_번역.pdf) 유도에 쓴다
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    # 감지된 포맷 프로파일 이름 (extracting 단계에서 채워짐)
    format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    translate_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    translate_cli_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # queued|extracting|translating|overlaying|done|error|cancelled
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="queued", default="queued"
    )
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    translated_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    block_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )
# === ANCHOR: MODELS_PDF_JOB_END ===
```

`alembic/versions/0007_pdf_jobs.py` — `0004_video_captions.py`를 열어 video_job 테이블 생성부의 타입 표기(특히 BigInteger id·Uuid 컬럼의 정확한 스펠링)를 그대로 따라 `pdf_job` 단일 테이블 생성 마이그레이션 작성. `revision = "0007"`, `down_revision = "0006"`. 컬럼 목록은 위 모델과 1:1. `downgrade`는 `op.drop_table("pdf_job")`.

(번들 SQLite는 `create_all`이 새 테이블을 만들므로 `seed.py` ALTER 블록 추가는 **불필요** — 신규 테이블은 컬럼 보강 대상이 아니다.)

`pdf_store.py`:

```python
"""PDF 번역 작업의 파일 저장소 경로 (video_captions/job_store.py와 동형)."""
from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID


def pdf_jobs_root() -> Path:
    root = os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")
    return Path(root) / "pdf_jobs"


def pdf_job_dir(external_id: UUID | str) -> Path:
    return pdf_jobs_root() / str(external_id)
```

- [ ] **5-4: 통과 확인 + 커밋**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_store.py apps/server/tests/test_db_portability.py -v
ruff check apps/server/db apps/server/domain/pdf_translate
git add apps/server/db/models.py apps/server/db/alembic/versions/0007_pdf_jobs.py apps/server/domain/pdf_translate/pdf_store.py apps/server/tests/test_pdf_store.py
git commit -m "feat(pdf-translate): PdfJob 모델 + alembic 0007 + 잡 파일 스토어"
```

---

### Task 6: 잡 태스크 레지스트리 + 러너 + 시작 스윕

**Files:**
- Create: `apps/server/domain/pdf_translate/pdf_tasks.py`
- Create: `apps/server/domain/pdf_translate/pdf_run.py`
- Modify: `apps/server/main.py` (lifespan에 스윕 1줄 — `fail_inflight_video_jobs_at_startup()` 호출 인접)
- Test: `apps/server/tests/test_pdf_run.py`

**Interfaces:**
- Consumes: Task 1 `open_pdf`, Task 2 `detect_profile`, Task 3 `create_translator(prompt_builder=...)`, Task 4 `build_pdf_prompt`/`translate_texts`, Task 5 `PdfJob`/`pdf_job_dir`
- Produces:
  - `pdf_tasks.start_pdf_task(external_id: UUID, coro) -> None`, `pdf_tasks.cancel_pdf_task(external_id) -> bool`, `pdf_tasks._set_status(external_id, status, *, error=None, **fields)`, `pdf_tasks._bump_generation/_current_generation`
  - `pdf_run.run_pdf_job(external_id: UUID)` — queued→extracting→translating→overlaying→done, 산출물 `pdf_job_dir/translated.pdf`
  - `pdf_run.fail_inflight_pdf_jobs_at_startup()`
  - `pdf_run.PdfTranslateError(RuntimeError)`

**Steps:**

- [ ] **6-1: 실패하는 테스트 작성** — `apps/server/tests/test_pdf_run.py`

```python
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from apps.server.db.models import PdfJob
from apps.server.domain.pdf_translate import pdf_run
from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir


def _make_storyboard_pdf(dest: Path) -> None:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((680, 460), "Dialog", fontsize=8)
    page.insert_text((680, 478), "If you wanna go, then go.", fontsize=10)
    page.insert_text((72, 560), "Action Notes", fontsize=8)
    page.insert_text((72, 578), "HANK walks to the door.", fontsize=10)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dest)
    doc.close()


class FakeTranslator:
    async def translate_batch(self, texts):
        return [f"KO:{t}" for t in texts]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(pdf_run, "create_translator",
                        lambda provider, cli_model, prompt_builder: FakeTranslator())
    yield


async def _seed_job(db_session, admin_user, *, status="queued") -> PdfJob:
    eid = uuid4()
    src = pdf_job_dir(eid) / "source.pdf"
    _make_storyboard_pdf(src)
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="sb.pdf", status=status, source_path=str(src))
    db_session.add(job)
    await db_session.commit()
    return job


async def test_run_pdf_job_happy_path(db_session, admin_user):
    job = await _seed_job(db_session, admin_user)
    await pdf_run.run_pdf_job(job.external_id)
    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job.id))).scalar_one()
    assert row.status == "done" and row.progress == 100
    assert row.format == "storyboard" and row.page_count == 1
    assert row.block_count == 2
    out = Path(row.translated_path)
    assert out.exists()
    import fitz
    d = fitz.open(out)
    contents = [a.info.get("content", "") for a in d[0].annots()]
    d.close()
    assert any(c.startswith("KO:") for c in contents)


async def test_run_pdf_job_unsupported_format_sets_error(db_session, admin_user,
                                                         tmp_path):
    import fitz
    eid = uuid4()
    src = pdf_job_dir(eid) / "source.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(); doc.new_page(width=612, height=792); doc.save(src); doc.close()
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="x.pdf", status="queued", source_path=str(src))
    db_session.add(job)
    await db_session.commit()
    await pdf_run.run_pdf_job(eid)
    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job.id))).scalar_one()
    assert row.status == "error" and "포맷" in (row.error or "")


async def test_fail_inflight_at_startup(db_session, admin_user):
    job = await _seed_job(db_session, admin_user, status="translating")
    await pdf_run.fail_inflight_pdf_jobs_at_startup()
    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job.id))).scalar_one()
    assert row.status == "error"
```

- [ ] **6-2: 실패 확인**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_run.py -v
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **6-3: 구현**

`pdf_tasks.py` — `video_captions/job_tasks.py` 미러(ffmpeg kill 제외). 주석 포함 전체:

```python
"""PDF 번역 작업의 실행 기반 — video_captions/job_tasks.py 미러.

의도적 복제: 자막 잡 레지스트리와 상태(_tasks/세대/세마포어)를 공유하면
한쪽 취소·직렬화가 다른 도메인으로 번진다. 알고리즘은 같고 소유는 분리.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select

from apps.server.db.models import PdfJob
from apps.server.db.session import AsyncSessionLocal

logger = logging.getLogger("yeson.pdf.pipeline")

_PROGRESS = {"queued": 0, "extracting": 5, "translating": 0,
             "overlaying": 95, "done": 100}

_tasks: set[asyncio.Task] = set()
_PDF_SEMAPHORE = asyncio.Semaphore(1)  # 번역 작업 직렬화 (배치 순서 보장)
_job_tasks: dict[str, asyncio.Task] = {}
_job_generation: dict[str, int] = {}


def _bump_generation(external_id: UUID | str) -> int:
    key = str(external_id)
    gen = _job_generation.get(key, 0) + 1
    _job_generation[key] = gen
    return gen


def _current_generation(external_id: UUID | str) -> int:
    return _job_generation.get(str(external_id), 0)


def start_pdf_task(external_id: UUID, coro) -> None:
    key = str(external_id)
    task = asyncio.create_task(coro)
    _tasks.add(task)
    _job_tasks[key] = task

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if _job_tasks.get(key) is t:
            _job_tasks.pop(key, None)

    task.add_done_callback(_done)


def cancel_pdf_task(external_id: UUID) -> bool:
    _bump_generation(external_id)
    task = _job_tasks.get(str(external_id))
    if task is not None and not task.done():
        task.cancel()
        return True
    return False


async def _load_job(db, external_id: UUID) -> PdfJob:
    return (await db.execute(
        select(PdfJob).where(PdfJob.external_id == external_id)
    )).scalar_one()


async def _set_status(external_id: UUID, status: str, *, error: str | None = None,
                      **fields) -> None:
    async with AsyncSessionLocal() as db:
        job = await _load_job(db, external_id)
        job.status = status
        job.progress = _PROGRESS.get(status, job.progress)
        job.error = error
        for key, value in fields.items():
            setattr(job, key, value)
        await db.commit()


async def _set_progress(external_id: UUID, pct: int, generation: int) -> None:
    if generation != _current_generation(external_id):
        return
    try:
        async with AsyncSessionLocal() as db:
            job = await _load_job(db, external_id)
            job.progress = pct
            await db.commit()
    except Exception:  # noqa: BLE001 — 진행률은 부가 정보
        logger.exception("failed to update progress for pdf job %s", external_id)


async def _try_set_error(external_id: UUID, message: str) -> None:
    try:
        await _set_status(external_id, "error", error=message)
    except Exception:  # noqa: BLE001
        logger.exception("failed to record error for pdf job %s", external_id)
```

`pdf_run.py`:

```python
"""PDF 번역 파이프라인 러너 — extract → translate → overlay."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from apps.server.db.models import PdfJob
from apps.server.db.session import AsyncSessionLocal
from apps.server.domain.video_captions.translate import maybe_aclose_translator
from apps.server.domain.video_captions.translate_cli import create_translator

from .backend import open_pdf
from .pdf_store import pdf_job_dir
from .pdf_tasks import (_PDF_SEMAPHORE, _bump_generation, _current_generation,
                        _set_progress, _set_status, _try_set_error)
from .profiles import detect_profile
from .translate_blocks import build_pdf_prompt, translate_texts

logger = logging.getLogger("yeson.pdf.pipeline")


class PdfTranslateError(RuntimeError):
    pass


async def run_pdf_job(external_id: UUID) -> None:
    await _PDF_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    doc = None
    try:
        async with AsyncSessionLocal() as db:
            job = (await db.execute(
                select(PdfJob).where(PdfJob.external_id == external_id)
            )).scalar_one()
            source_path = job.source_path
            provider = job.translate_provider
            cli_model = job.translate_cli_model
        if not source_path or not Path(source_path).exists():
            raise PdfTranslateError("원본 PDF 파일이 없습니다")

        await _set_status(external_id, "extracting")
        doc = await asyncio.to_thread(open_pdf, Path(source_path))
        profile = detect_profile(doc)
        if profile is None:
            raise PdfTranslateError(
                "지원하지 않는 PDF 포맷입니다 (현재 지원: 스토리보드형)")
        blocks = await asyncio.to_thread(profile.extract, doc)
        if not blocks:
            raise PdfTranslateError("번역할 텍스트 블록을 찾지 못했습니다")
        await _set_status(external_id, "translating", format=profile.name,
                          page_count=doc.page_count, block_count=len(blocks))

        async def on_progress(frac: float) -> None:
            # 세대가 바뀌었으면(취소) 다음 청크로 가기 전에 중단한다
            if generation != _current_generation(external_id):
                raise asyncio.CancelledError
            await _set_progress(external_id, int(frac * 100), generation)

        translator = create_translator(provider, cli_model,
                                       prompt_builder=build_pdf_prompt)
        try:
            ko_texts = await translate_texts([b.text for b in blocks], translator,
                                             progress_cb=on_progress)
        finally:
            await maybe_aclose_translator(translator)

        await _set_status(external_id, "overlaying")

        def _overlay_and_save() -> Path:
            for block, ko in zip(blocks, ko_texts):
                ko = ko.strip()
                # 번역 실패 폴백(원문 복사)·빈 결과는 주석을 달지 않는다
                if not ko or ko == block.text.strip():
                    continue
                ov = profile.place(block, ko, doc.page_size(block.page))
                doc.add_freetext(ov.page, ov.rect, ov.text, fontsize=ov.fontsize)
            dest = pdf_job_dir(external_id) / "translated.pdf"
            doc.save(dest)
            return dest

        dest = await asyncio.to_thread(_overlay_and_save)
        await _set_status(external_id, "done", translated_path=str(dest))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — 최종 방어선
        logger.exception("pdf job %s failed", external_id)
        if generation == _current_generation(external_id):
            await _try_set_error(external_id, str(exc))
    finally:
        if doc is not None:
            doc.close()
        _PDF_SEMAPHORE.release()


async def fail_inflight_pdf_jobs_at_startup() -> None:
    """재시작 시 in-flight 작업을 error로 — 좀비 'translating' 행 방지."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(PdfJob).where(PdfJob.status.in_(
            ("queued", "extracting", "translating", "overlaying"))))
        ).scalars().all()
        for job in rows:
            job.status = "error"
            job.error = "서버 재시작으로 중단됨 — 다시 업로드하세요"
        if rows:
            await db.commit()
```

`main.py` — lifespan에서 `fail_inflight_video_jobs_at_startup()` 호출을 grep으로 찾아 바로 다음 줄에:

```python
    from apps.server.domain.pdf_translate.pdf_run import fail_inflight_pdf_jobs_at_startup
    await fail_inflight_pdf_jobs_at_startup()
```
(import는 기존 스타일에 맞춰 파일 상단으로 올려도 됨 — 기존 video 스윕 import 위치와 동일하게.)

- [ ] **6-4: 통과 확인 + 커밋**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_pdf_run.py -v
ruff check apps/server/domain/pdf_translate apps/server/main.py
git add apps/server/domain/pdf_translate apps/server/main.py apps/server/tests/test_pdf_run.py
git commit -m "feat(pdf-translate): 잡 러너 (extract→translate→overlay) + 취소 세대 가드 + 시작 스윕"
```

---

### Task 7: API 라우트 /pdf-jobs + 등록

**Files:**
- Create: `apps/server/api/v1/pdf_jobs.py`
- Modify: `apps/server/main.py` (import 1줄 + `include_router` 1줄 — `video_jobs_router` 등록 줄 바로 아래)
- Test: `apps/server/tests/test_api_pdf_jobs.py`

**Interfaces:**
- Consumes: Task 5 `PdfJob`/`pdf_job_dir`, Task 6 `run_pdf_job`/`start_pdf_task`/`cancel_pdf_task`, Task 1 `open_pdf`, `save_upload`(video_captions.ingest), `_default_owner_id`(api.v1.video_jobs — 형제 모듈 헬퍼 재사용)
- Produces (전부 `/api/v1` 프리픽스 하위):
  - `POST /pdf-jobs/upload` (multipart: `file`, `title?`, `translate_provider?`, `translate_cli_model?`) → 201 `{job_id}`
  - `GET /pdf-jobs` → `{items: [...]}` (created_at desc)
  - `GET /pdf-jobs/{job_id}` → 상세
  - `GET /pdf-jobs/{job_id}/page/{n}?variant=source|translated` → `image/png`
  - `GET /pdf-jobs/{job_id}/download` → FileResponse(`<stem>_번역.pdf`)
  - `POST /pdf-jobs/{job_id}/cancel` → 200 (터미널 상태면 409)
  - `DELETE /pdf-jobs/{job_id}` → 204 (실행 중이면 취소 후 삭제 + 디렉터리 rmtree)
  - `_start_pdf_pipeline(external_id)` — 테스트 심(모듈 함수, monkeypatch 지점)

**Steps:**

- [ ] **7-1: 실패하는 테스트 작성** — `apps/server/tests/test_api_pdf_jobs.py`

```python
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from apps.server.api.v1 import pdf_jobs as api_pdf
from apps.server.db.models import PdfJob
from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(api_pdf, "_start_pdf_pipeline", lambda eid: None)
    yield


def _tiny_pdf_bytes() -> bytes:
    import fitz
    doc = fitz.open()
    doc.new_page(width=1008, height=612)
    data = doc.tobytes()
    doc.close()
    return data


async def test_upload_creates_job_and_saves_file(client, admin_user, db_session):
    resp = await client.post(
        "/api/v1/pdf-jobs/upload",
        data={"title": "콘티", "translate_provider": "gemini"},
        files={"file": ("GABE01_A1.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 201
    row = (await db_session.execute(select(PdfJob).where(
        PdfJob.external_id == resp.json()["job_id"]))).scalar_one()
    assert row.title == "콘티" and row.source_ref == "GABE01_A1.pdf"
    assert row.status == "queued"
    assert Path(row.source_path).exists()


async def test_upload_rejects_non_pdf(client, admin_user):
    resp = await client.post(
        "/api/v1/pdf-jobs/upload",
        files={"file": ("clip.mp4", b"xx", "video/mp4")},
    )
    assert resp.status_code == 422


async def test_upload_rejects_unknown_provider(client, admin_user):
    resp = await client.post(
        "/api/v1/pdf-jobs/upload",
        data={"translate_provider": "no-such-engine"},
        files={"file": ("a.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 422


async def test_list_and_detail(client, admin_user, db_session):
    job = PdfJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                 source_ref="a.pdf", status="done", progress=100,
                 format="storyboard", page_count=3, block_count=7)
    db_session.add(job)
    await db_session.commit()
    items = (await client.get("/api/v1/pdf-jobs")).json()["items"]
    assert items[0]["job_id"] == str(job.external_id)
    detail = (await client.get(f"/api/v1/pdf-jobs/{job.external_id}")).json()
    assert detail["format"] == "storyboard" and detail["page_count"] == 3


async def test_page_png_source_variant(client, admin_user, db_session):
    eid = uuid4()
    src = pdf_job_dir(eid) / "source.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(_tiny_pdf_bytes())
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="a.pdf", status="queued", source_path=str(src))
    db_session.add(job)
    await db_session.commit()
    resp = await client.get(f"/api/v1/pdf-jobs/{eid}/page/0?variant=source")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (await client.get(
        f"/api/v1/pdf-jobs/{eid}/page/9?variant=source")).status_code == 404
    assert (await client.get(
        f"/api/v1/pdf-jobs/{eid}/page/0?variant=translated")).status_code == 404


async def test_download_requires_done(client, admin_user, db_session, tmp_path):
    eid = uuid4()
    out = pdf_job_dir(eid) / "translated.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_tiny_pdf_bytes())
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="GABE01_A1.pdf", status="done", progress=100,
                 translated_path=str(out))
    db_session.add(job)
    await db_session.commit()
    resp = await client.get(f"/api/v1/pdf-jobs/{eid}/download")
    assert resp.status_code == 200
    assert "GABE01_A1_%EB%B2%88%EC%97%AD.pdf" in resp.headers.get(
        "content-disposition", "") or "_번역.pdf" in resp.headers.get(
        "content-disposition", "")


async def test_cancel_and_delete(client, admin_user, db_session):
    job = PdfJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                 source_ref="a.pdf", status="translating", progress=40)
    db_session.add(job)
    await db_session.commit()
    assert (await client.post(
        f"/api/v1/pdf-jobs/{job.external_id}/cancel")).status_code == 200
    db_session.expire_all()
    row = (await db_session.execute(select(PdfJob).where(
        PdfJob.id == job.id))).scalar_one()
    assert row.status == "cancelled"
    assert (await client.post(
        f"/api/v1/pdf-jobs/{job.external_id}/cancel")).status_code == 409
    assert (await client.delete(
        f"/api/v1/pdf-jobs/{job.external_id}")).status_code == 204
```

- [ ] **7-2: 실패 확인**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_api_pdf_jobs.py -v
```
Expected: FAIL — 404 (라우터 미등록) 또는 ImportError

- [ ] **7-3: 구현** — `api/v1/pdf_jobs.py`:

```python
"""PDF 스토리보드 번역 작업 API — video_jobs.py와 동형의 얇은 라우트."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Response,
                     UploadFile, status)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.api.deps import get_session
from apps.server.api.v1.video_jobs import _default_owner_id
from apps.server.db.models import PdfJob
from apps.server.domain.pdf_translate.backend import open_pdf
from apps.server.domain.pdf_translate.pdf_run import run_pdf_job
from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir
from apps.server.domain.pdf_translate.pdf_tasks import (cancel_pdf_task,
                                                        start_pdf_task)
from apps.server.domain.video_captions.ingest import save_upload
from apps.server.domain.video_captions.translate_cli import list_translate_engines

router = APIRouter(tags=["pdf-jobs"], prefix="/pdf-jobs")

# 엔진 목록에서 자동 도출 — video_jobs와 동일 이유(하드코딩 드리프트 방지)
_PROVIDER_PATTERN = "^(" + "|".join(
    e["value"] for e in list_translate_engines()) + ")$"

_TERMINAL = ("done", "error", "cancelled")


def _start_pdf_pipeline(external_id: UUID) -> None:  # test seam
    start_pdf_task(external_id, run_pdf_job(external_id))


async def _get_job(db: AsyncSession, job_id: UUID) -> PdfJob:
    job = (await db.execute(
        select(PdfJob).where(PdfJob.external_id == job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "작업을 찾을 수 없습니다")
    return job


def _summary(job: PdfJob) -> dict:
    return {
        "job_id": str(job.external_id), "title": job.title,
        "source_ref": job.source_ref, "format": job.format,
        "translate_provider": job.translate_provider,
        "status": job.status, "progress": job.progress, "error": job.error,
        "page_count": job.page_count, "block_count": job.block_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def create_pdf_job(
    db: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    translate_provider: Annotated[
        str | None, Form(pattern=_PROVIDER_PATTERN)] = None,
    translate_cli_model: Annotated[str | None, Form()] = None,
) -> dict:
    filename = file.filename or "upload.pdf"
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "PDF 파일만 업로드할 수 있습니다")
    external_id = uuid4()
    dest = pdf_job_dir(external_id) / "source.pdf"
    try:
        await save_upload(file, dest)
        owner_id = await _default_owner_id(db)
        job = PdfJob(external_id=external_id, owner_user_id=owner_id,
                     title=title or filename, source_ref=filename,
                     translate_provider=translate_provider,
                     translate_cli_model=translate_cli_model,
                     status="queued", source_path=str(dest))
        db.add(job)
        await db.commit()
    except Exception:
        shutil.rmtree(pdf_job_dir(external_id), ignore_errors=True)
        raise
    _start_pdf_pipeline(external_id)
    return {"job_id": str(external_id)}


@router.get("")
async def list_pdf_jobs(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    rows = (await db.execute(
        select(PdfJob).order_by(PdfJob.created_at.desc(), PdfJob.id.desc())
    )).scalars().all()
    return {"items": [_summary(j) for j in rows]}


@router.get("/{job_id}")
async def get_pdf_job(
    job_id: UUID, db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    return _summary(await _get_job(db, job_id))


def _render_page(path: str, page: int) -> bytes:
    doc = open_pdf(Path(path))
    try:
        if page < 0 or page >= doc.page_count:
            raise IndexError(page)
        return doc.render_png(page, dpi=120)
    finally:
        doc.close()


@router.get("/{job_id}/page/{page}")
async def get_pdf_page_png(
    job_id: UUID, page: int,
    db: Annotated[AsyncSession, Depends(get_session)],
    variant: str = "source",
) -> Response:
    job = await _get_job(db, job_id)
    path = job.translated_path if variant == "translated" else job.source_path
    if not path or not Path(path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PDF가 아직 없습니다")
    try:
        png = await asyncio.to_thread(_render_page, path, page)
    except IndexError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "페이지 범위 밖입니다")
    return Response(content=png, media_type="image/png")


@router.get("/{job_id}/download")
async def download_pdf(
    job_id: UUID, db: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    job = await _get_job(db, job_id)
    if job.status != "done" or not job.translated_path:
        raise HTTPException(status.HTTP_409_CONFLICT, "아직 번역이 끝나지 않았습니다")
    path = Path(job.translated_path)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "번역 PDF가 없습니다")
    name = f"{Path(job.source_ref).stem}_번역.pdf"
    return FileResponse(path, media_type="application/pdf", filename=name)


@router.post("/{job_id}/cancel")
async def cancel_pdf_job(
    job_id: UUID, db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await _get_job(db, job_id)
    if job.status in _TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 끝난 작업입니다")
    cancel_pdf_task(job_id)
    job.status = "cancelled"
    job.progress = 0
    await db.commit()
    return {"status": "cancelled"}


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pdf_job(
    job_id: UUID, db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    job = await _get_job(db, job_id)
    cancel_pdf_task(job_id)
    await db.delete(job)
    await db.commit()
    shutil.rmtree(pdf_job_dir(job_id), ignore_errors=True)
```

주의: `from apps.server.api.deps import get_session`은 video_jobs.py의 실제 import 경로를 확인해 동일하게(다르면 그쪽을 따른다). `main.py`에 video_jobs_router와 같은 스타일로 import + `app.include_router(pdf_jobs_router, prefix="/api/v1")`를 `video_jobs_router` 줄 바로 아래 추가.

- [ ] **7-4: 통과 확인 + 커밋**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests/test_api_pdf_jobs.py apps/server/tests/test_api_video_jobs.py -v
ruff check apps/server/api/v1/pdf_jobs.py apps/server/main.py
git add apps/server/api/v1/pdf_jobs.py apps/server/main.py apps/server/tests/test_api_pdf_jobs.py
git commit -m "feat(pdf-translate): /pdf-jobs API (업로드·목록·상세·페이지 PNG·다운로드·취소·삭제)"
```

---

### Task 8: Tauri 업로드 커맨드 (Rust)

**Files:**
- Create: `apps/desktop/src-tauri/src/pdf_upload.rs`
- Modify: `apps/desktop/src-tauri/src/lib.rs` (mod 선언 1줄 + generate_handler에 1줄 — `video_download::download_to_file` 인접)

**Interfaces:**
- Produces: Tauri command `upload_pdf_file(upload_url, path, title, translate_provider?, translate_cli_model?) -> String(서버 JSON)` — `upload_video_file`과 동형(스트리밍 멀티파트, whisper 필드 없음)

**Steps:**

- [ ] **8-1: 구현** — `pdf_upload.rs`:

```rust
// PDF 스토리보드 번역 업로드 커맨드 — video_upload::upload_video_file과 동형.
// 173MB급 스토리보드도 스트리밍 전송으로 메모리를 상수로 유지한다.

use std::path::Path;

#[tauri::command]
pub async fn upload_pdf_file(
    upload_url: String,
    path: String,
    title: String,
    translate_provider: Option<String>,
    translate_cli_model: Option<String>,
) -> Result<String, String> {
    let file = tokio::fs::File::open(&path)
        .await
        .map_err(|e| format!("파일 열기 실패: {e}"))?;
    let len = file
        .metadata()
        .await
        .map_err(|e| format!("파일 정보 실패: {e}"))?
        .len();
    let file_name = Path::new(&path)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("upload.pdf")
        .to_string();

    let stream = tokio_util::io::ReaderStream::new(file);
    let part = reqwest::multipart::Part::stream_with_length(
        reqwest::Body::wrap_stream(stream),
        len,
    )
    .file_name(file_name)
    .mime_str("application/pdf")
    .map_err(|e| e.to_string())?;

    let mut form = reqwest::multipart::Form::new()
        .text("title", title)
        .part("file", part);
    if let Some(p) = translate_provider.filter(|s| !s.is_empty()) {
        form = form.text("translate_provider", p);
    }
    if let Some(m) = translate_cli_model.filter(|s| !s.is_empty()) {
        form = form.text("translate_cli_model", m);
    }

    let resp = reqwest::Client::new()
        .post(&upload_url)
        .multipart(form)
        .send()
        .await
        .map_err(|e| format!("업로드 실패: {e}"))?;
    let status = resp.status();
    let body = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        let tail: String = body.chars().take(300).collect();
        return Err(format!("HTTP {status}: {tail}"));
    }
    Ok(body)
}
```

`lib.rs` — 상단 mod 목록에 `mod pdf_upload;` 추가(기존 mod 선언부에 맞춰), `generate_handler![` 목록의 `video_download::download_to_file,` 줄 아래에 `pdf_upload::upload_pdf_file,` 추가.

- [ ] **8-2: 컴파일 확인 + 커밋**

```bash
cd apps/desktop/src-tauri && cargo check && cd -
git add apps/desktop/src-tauri/src/pdf_upload.rs apps/desktop/src-tauri/src/lib.rs
git commit -m "feat(pdf-translate): Tauri upload_pdf_file 스트리밍 업로드 커맨드"
```

---

### Task 9: 프런트 — pdfApi + A안 탭 + PdfTranslatePanel(업로드·목록·진행·취소·삭제)

**Files:**
- Create: `apps/desktop/src/console/pdfApi.ts`
- Create: `apps/desktop/src/console/PdfTranslatePanel.tsx`
- Modify: `apps/desktop/src/console/types.ts:2` (`ConsoleView`에 `"pdf"`)
- Modify: `apps/desktop/src/console/ConsoleNav.tsx:18` (navItems에 1항목)
- Modify: `apps/desktop/src/console/DesktopConsole.tsx` (import 1줄 + section 1블록)
- Test: `apps/desktop/src/console/pdfApi.test.ts`

**Interfaces:**
- Consumes: `apiBase()`(sessionApi), `listTranslateEngines()`(videoApi — 엔진 드롭다운 재사용), Tauri `upload_pdf_file`/`download_to_file`
- Produces (pdfApi.ts):
  - `type PdfJobSummary = { job_id: string; title: string; source_ref: string; format: string | null; translate_provider: string | null; status: string; progress: number; error: string | null; page_count: number | null; block_count: number | null; created_at: string | null }`
  - `uploadPdfJob(file: File, title: string, translateProvider?: string, translateCliModel?: string): Promise<{job_id: string}>` (브라우저 폴백용 FormData)
  - `pdfUploadUrl(): string`, `listPdfJobs(): Promise<PdfJobSummary[]>`, `getPdfJob(id): Promise<PdfJobSummary>`, `pdfPageUrl(id: string, page: number, variant: "source" | "translated"): string`, `pdfDownloadUrl(id: string): string`, `cancelPdfJob(id): Promise<void>`, `deletePdfJob(id): Promise<void>`
  - `isActivePdfStatus(status: string): boolean` — `queued|extracting|translating|overlaying`

**Steps:**

- [ ] **9-1: 실패하는 테스트 작성** — `apps/desktop/src/console/pdfApi.test.ts`

```typescript
import { describe, expect, it } from "vitest";
import { isActivePdfStatus, pdfPageUrl } from "./pdfApi";

describe("pdfApi helpers", () => {
  it("active statuses are the four in-flight ones", () => {
    for (const s of ["queued", "extracting", "translating", "overlaying"]) {
      expect(isActivePdfStatus(s)).toBe(true);
    }
    for (const s of ["done", "error", "cancelled"]) {
      expect(isActivePdfStatus(s)).toBe(false);
    }
  });

  it("pdfPageUrl encodes variant", () => {
    expect(pdfPageUrl("abc", 3, "translated")).toContain(
      "/api/v1/pdf-jobs/abc/page/3?variant=translated");
  });
});
```

- [ ] **9-2: 실패 확인**

```bash
pnpm -C apps/desktop test
```
Expected: FAIL — `Cannot find module './pdfApi'`

- [ ] **9-3: 구현**

`pdfApi.ts`:

```typescript
// PDF 스토리보드 번역 API 클라이언트. request 헬퍼는 videoApi.ts의 것과 동형
// 의도적 복제 — videoApi의 비공개 헬퍼를 export로 승격하지 않는다(최소 접촉).
import { apiBase } from "./sessionApi";

export type PdfJobSummary = {
  job_id: string;
  title: string;
  source_ref: string;
  format: string | null;
  translate_provider: string | null;
  status: string;
  progress: number;
  error: string | null;
  page_count: number | null;
  block_count: number | null;
  created_at: string | null;
};

async function request<T>(url: string, init: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* JSON 아님 — 상태코드만 */
    }
    throw new Error(detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export function isActivePdfStatus(status: string): boolean {
  return ["queued", "extracting", "translating", "overlaying"].includes(status);
}

export function pdfUploadUrl(): string {
  return `${apiBase()}/api/v1/pdf-jobs/upload`;
}

export async function uploadPdfJob(
  file: File, title: string,
  translateProvider?: string, translateCliModel?: string,
): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  if (title) form.append("title", title);
  if (translateProvider) form.append("translate_provider", translateProvider);
  if (translateCliModel) form.append("translate_cli_model", translateCliModel);
  return request(pdfUploadUrl(), { method: "POST", body: form });
}

export async function listPdfJobs(): Promise<PdfJobSummary[]> {
  const out = await request<{ items: PdfJobSummary[] }>(
    `${apiBase()}/api/v1/pdf-jobs`, {});
  return out.items;
}

export async function getPdfJob(jobId: string): Promise<PdfJobSummary> {
  return request(`${apiBase()}/api/v1/pdf-jobs/${jobId}`, {});
}

export function pdfPageUrl(
  jobId: string, page: number, variant: "source" | "translated",
): string {
  return `${apiBase()}/api/v1/pdf-jobs/${jobId}/page/${page}?variant=${variant}`;
}

export function pdfDownloadUrl(jobId: string): string {
  return `${apiBase()}/api/v1/pdf-jobs/${jobId}/download`;
}

export async function cancelPdfJob(jobId: string): Promise<void> {
  await request(`${apiBase()}/api/v1/pdf-jobs/${jobId}/cancel`,
    { method: "POST" });
}

export async function deletePdfJob(jobId: string): Promise<void> {
  await request(`${apiBase()}/api/v1/pdf-jobs/${jobId}`, { method: "DELETE" });
}
```

`types.ts:2`:

```typescript
export type ConsoleView = "setup" | "help" | "history" | "settings" | "video" | "pdf";
```

`ConsoleNav.tsx` navItems — `{ view: "video", label: "자막 메이커" },` 아래에:

```typescript
  { view: "pdf", label: "스토리보드 번역" },
```

`DesktopConsole.tsx` — import 추가 `import { PdfTranslatePanel } from "./PdfTranslatePanel";`, video 섹션 아래에:

```tsx
        <section hidden={activeView !== "pdf"}
          style={activeView === "pdf" ? consoleStyles.sectionScroll : undefined}>
          <PdfTranslatePanel active={activeView === "pdf"} />
        </section>
```

`PdfTranslatePanel.tsx` — 업로드(파일 선택 + 엔진 드롭다운) + 작업 목록(1.5초 폴링, 활성 작업 있을 때만) + 취소/삭제. Tauri 런타임이면 `plugin-dialog` `open({filters:[{name:"PDF",extensions:["pdf"]}], multiple:true})` + `invoke("upload_pdf_file", ...)`, 아니면 `<input type="file" accept="application/pdf" multiple>` + `uploadPdfJob`. 런타임 감지는 `VideoCaptionPanel.tsx:41`의 `hasTauriRuntime()`과 동일한 식(`"__TAURI_INTERNALS__" in window` 계열 — 그 파일의 구현을 복사)을 사용:

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { listTranslateEngines, type TranslateEngineInfo } from "./videoApi";
import {
  cancelPdfJob, deletePdfJob, isActivePdfStatus, listPdfJobs,
  pdfUploadUrl, uploadPdfJob, type PdfJobSummary,
} from "./pdfApi";

const STATUS_LABEL: Record<string, string> = {
  queued: "대기", extracting: "추출 중", translating: "번역 중",
  overlaying: "주석 생성 중", done: "완료", error: "오류", cancelled: "취소됨",
};

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function PdfTranslatePanel({ active }: { active: boolean }) {
  const [engines, setEngines] = useState<TranslateEngineInfo[]>([]);
  const [provider, setProvider] = useState<string>("gemini");
  const [jobs, setJobs] = useState<PdfJobSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string>("");
  const fileInput = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      setJobs(await listPdfJobs());
    } catch (e) {
      setMessage(String(e));
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    void refresh();
    void listTranslateEngines().then(setEngines).catch(() => {});
  }, [active, refresh]);

  // 활성 작업이 있을 때만 1.5초 폴링
  useEffect(() => {
    if (!active || !jobs.some((j) => isActivePdfStatus(j.status))) return;
    const t = setInterval(() => void refresh(), 1500);
    return () => clearInterval(t);
  }, [active, jobs, refresh]);

  const uploadPaths = useCallback(async (paths: string[]) => {
    const { invoke } = await import("@tauri-apps/api/core");
    for (const p of paths) {
      const name = p.split(/[\\/]/).pop() ?? "upload.pdf";
      await invoke<string>("upload_pdf_file", {
        uploadUrl: pdfUploadUrl(), path: p, title: name,
        translateProvider: provider, translateCliModel: null,
      });
    }
  }, [provider]);

  const pickAndUpload = useCallback(async () => {
    setMessage("");
    setBusy(true);
    try {
      if (hasTauriRuntime()) {
        const { open } = await import("@tauri-apps/plugin-dialog");
        const picked = await open({
          multiple: true, filters: [{ name: "PDF", extensions: ["pdf"] }],
          title: "번역할 PDF 선택",
        });
        if (!picked) return;
        await uploadPaths(Array.isArray(picked) ? picked : [picked]);
      } else {
        fileInput.current?.click();
        return;
      }
      await refresh();
    } catch (e) {
      setMessage(`업로드 실패: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }, [refresh, uploadPaths]);

  const onBrowserFiles = useCallback(async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        await uploadPdfJob(f, f.name, provider);
      }
      await refresh();
    } catch (e) {
      setMessage(`업로드 실패: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }, [provider, refresh]);

  return (
    <div>
      <h2 style={{ fontSize: 16, marginBottom: 4 }}>스토리보드 번역</h2>
      <p style={{ color: "#94a3b8", fontSize: 12, marginBottom: 12 }}>
        납품 PDF(스토리보드)를 올리면 Dialog/Action Notes를 번역해 주석으로 입힌
        PDF를 만듭니다. 포맷은 자동 감지됩니다.
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
        <label style={{ fontSize: 12 }}>번역 엔진</label>
        <select value={provider} onChange={(e) => setProvider(e.target.value)}
          style={{ fontSize: 12 }}>
          {engines.map((eng) => (
            <option key={eng.value} value={eng.value} disabled={!eng.available}>
              {eng.label}{eng.available ? "" : " (사용 불가)"}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => void pickAndUpload()} disabled={busy}>
          {busy ? "업로드 중..." : "PDF 업로드"}
        </button>
        <input ref={fileInput} type="file" accept="application/pdf" multiple
          style={{ display: "none" }}
          onChange={(e) => void onBrowserFiles(e.target.files)} />
      </div>
      {message ? <p style={{ color: "#f87171", fontSize: 12 }}>{message}</p> : null}
      <PdfJobList jobs={jobs} onChanged={refresh} />
    </div>
  );
}

function PdfJobList({ jobs, onChanged }:
  { jobs: PdfJobSummary[]; onChanged: () => Promise<void> }) {
  if (!jobs.length) {
    return <p style={{ color: "#64748b", fontSize: 12 }}>작업이 없습니다.</p>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {jobs.map((j) => (
        <div key={j.job_id}
          style={{ border: "1px solid #334155", borderRadius: 6, padding: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <strong style={{ fontSize: 13 }}>{j.title}</strong>
            <span style={{ fontSize: 12, color: "#94a3b8" }}>
              {STATUS_LABEL[j.status] ?? j.status}
              {isActivePdfStatus(j.status) ? ` ${j.progress}%` : ""}
            </span>
          </div>
          {j.error ? (
            <p style={{ color: "#f87171", fontSize: 12 }}>{j.error}</p>
          ) : null}
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            {isActivePdfStatus(j.status) ? (
              <button type="button" onClick={() => {
                void cancelPdfJob(j.job_id).then(onChanged);
              }}>취소</button>
            ) : (
              <button type="button" onClick={() => {
                void deletePdfJob(j.job_id).then(onChanged);
              }}>삭제</button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
```

(프리뷰·다운로드 버튼은 Task 10에서 이 컴포넌트에 추가한다. 스타일은 커밋 전에 `consoleStyles.ts`에 대응 키가 있으면 그쪽으로 정리 — 인라인 하드코딩 색상은 기존 팔레트(#94a3b8/#334155 계열)를 따른다.)

- [ ] **9-4: 통과 확인 + 빌드 게이트 + 커밋**

```bash
pnpm -C apps/desktop test
pnpm -C apps/desktop build
git add apps/desktop/src/console/pdfApi.ts apps/desktop/src/console/pdfApi.test.ts apps/desktop/src/console/PdfTranslatePanel.tsx apps/desktop/src/console/types.ts apps/desktop/src/console/ConsoleNav.tsx apps/desktop/src/console/DesktopConsole.tsx
git commit -m "feat(pdf-translate): 스토리보드 번역 탭(A안) + 업로드·목록·취소 UI"
```

---

### Task 10: 프리뷰(페이지 PNG) + 다운로드 UI

**Files:**
- Create: `apps/desktop/src/console/PdfPreview.tsx`
- Modify: `apps/desktop/src/console/PdfTranslatePanel.tsx` (PdfJobList 카드에 버튼 2개 + 프리뷰 상태)

**Interfaces:**
- Consumes: `pdfPageUrl`, `pdfDownloadUrl`, `getPdfJob`, Tauri `download_to_file`(기존 커맨드 — url·path 인자), `plugin-dialog save()`

**Steps:**

- [ ] **10-1: 구현** — `PdfPreview.tsx`:

```tsx
import { useEffect, useState } from "react";
import { pdfPageUrl, type PdfJobSummary } from "./pdfApi";

// 페이지 단위 lazy 이미지 프리뷰 — 1000페이지급도 현재 페이지 1장만 로드한다.
export function PdfPreview({ job, onClose }:
  { job: PdfJobSummary; onClose: () => void }) {
  const [page, setPage] = useState(0);
  const [variant, setVariant] = useState<"source" | "translated">(
    job.status === "done" ? "translated" : "source");
  const total = job.page_count ?? 1;

  useEffect(() => { setPage(0); }, [job.job_id]);

  const clamp = (n: number) => Math.max(0, Math.min(total - 1, n));
  return (
    <div style={{ marginTop: 8, borderTop: "1px solid #334155", paddingTop: 8 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12 }}>
        <button type="button" onClick={() => setPage((p) => clamp(p - 1))}
          disabled={page <= 0}>← 이전</button>
        <span>{page + 1} / {total}</span>
        <button type="button" onClick={() => setPage((p) => clamp(p + 1))}
          disabled={page >= total - 1}>다음 →</button>
        <label>
          <input type="radio" checked={variant === "source"}
            onChange={() => setVariant("source")} /> 원본
        </label>
        <label>
          <input type="radio" checked={variant === "translated"}
            disabled={job.status !== "done"}
            onChange={() => setVariant("translated")} /> 번역본
        </label>
        <button type="button" onClick={onClose} style={{ marginLeft: "auto" }}>
          닫기
        </button>
      </div>
      <img src={pdfPageUrl(job.job_id, page, variant)}
        alt={`${job.title} p${page + 1}`}
        style={{ maxWidth: "100%", marginTop: 8, border: "1px solid #1e293b" }} />
    </div>
  );
}
```

`PdfTranslatePanel.tsx`의 `PdfJobList` 카드 버튼 영역에 추가(삭제 버튼 옆):

```tsx
            <button type="button" onClick={() =>
              setPreviewId((cur) => (cur === j.job_id ? null : j.job_id))
            }>프리뷰</button>
            {j.status === "done" ? (
              <button type="button" onClick={() => void downloadPdf(j)}>
                번역 PDF 저장
              </button>
            ) : null}
```

`PdfJobList`에 `const [previewId, setPreviewId] = useState<string | null>(null);`를 추가하고 카드 하단에 `{previewId === j.job_id ? <PdfPreview job={j} onClose={() => setPreviewId(null)} /> : null}` 렌더. `downloadPdf`:

```tsx
async function downloadPdf(job: PdfJobSummary): Promise<void> {
  const name = `${job.source_ref.replace(/\.pdf$/i, "")}_번역.pdf`;
  if (hasTauriRuntime()) {
    const { save } = await import("@tauri-apps/plugin-dialog");
    const dest = await save({ defaultPath: name,
      filters: [{ name: "PDF", extensions: ["pdf"] }] });
    if (!dest) return;
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("download_to_file", { url: pdfDownloadUrl(job.job_id), path: dest });
  } else {
    const a = document.createElement("a");
    a.href = pdfDownloadUrl(job.job_id);
    a.download = name;
    a.click();
  }
}
```

(`download_to_file`의 실제 인자명은 `apps/desktop/src-tauri/src/video_download.rs:12`의 시그니처를 확인해 camelCase 규칙에 맞춘다 — 기존 호출부 `VideoCaptionPanel.tsx:399-448`의 invoke 호출을 그대로 따른다. 주의: download_to_file은 전체 버퍼링(비스트리밍)이라 173MB급도 동작은 하지만 메모리를 그만큼 쓴다 — 슬라이스 1 수용, 문서화.)

- [ ] **10-2: 빌드 게이트 + 커밋**

```bash
pnpm -C apps/desktop test && pnpm -C apps/desktop build
git add apps/desktop/src/console/PdfPreview.tsx apps/desktop/src/console/PdfTranslatePanel.tsx
git commit -m "feat(pdf-translate): 페이지 프리뷰(원본/번역 토글) + 번역 PDF 저장"
```

---

### Task 11: 동결 번들 + 셀프테스트 + E2E + 문서

**Files:**
- Modify: `apps/server_desktop/scripts/build-server.sh` (`--collect-all rapidocr_onnxruntime` 인접에 flag 1줄 + import 검증)
- Modify: `apps/server_desktop/scripts/build-server.ps1` (같은 위치 동형 — add-data 구분자 `;` 주의)
- Modify: `apps/server_desktop/sidecar/server_entry.py` (`YESON_REPORT_SELFTEST` 처리부(:446 인근)와 동형의 `YESON_PDF_SELFTEST` 분기)
- Modify: `apps/server_desktop/scripts/smoke-server-bundle.sh` (PDF 셀프테스트 1회 추가)
- Modify: `docs/ROADMAP.md`, `docs/PRD.md` (기능 체크박스), `docs/pdf-translation-feasibility-2026-07-29.md` (슬라이스 1 완료 표시)

**Steps:**

- [ ] **11-1: 빌드 스크립트** — 두 파일 모두 `--collect-all cv2` 줄 인접에:

```
    --collect-all pymupdf \
    --hidden-import fitz \
```

build-server.sh의 cv2 재설치 가드(:38-58)와 같은 방식으로, 빌드 venv에서 `python -c "import pymupdf, fitz"` 검증을 추가(실패 시 `uv pip install --reinstall --no-cache pymupdf` 후 재검증). ps1에도 동형 추가.

- [ ] **11-2: 셀프테스트** — `server_entry.py`의 `YESON_REPORT_SELFTEST` 분기(:446) 바로 아래에 동형 분기:

```python
    if os.environ.get("YESON_PDF_SELFTEST") == "1":
        # 동결 번들에 pymupdf가 온전히 들어갔는지 1페이지 왕복으로 검증
        try:
            from pathlib import Path
            import tempfile
            from apps.server.domain.pdf_translate.backend import open_pdf
            import fitz
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "t.pdf"
                d = fitz.open(); d.new_page(width=100, height=100); d.save(p); d.close()
                doc = open_pdf(p)
                doc.add_freetext(0, (10, 10, 90, 40), "한글 셀프테스트")
                out = Path(td) / "o.pdf"
                doc.save(out); doc.close()
                assert open_pdf(out).render_png(0, dpi=36)[:8] == b"\x89PNG\r\n\x1a\n"
            print("PDF_SELFTEST_RESULT=PASS", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"PDF_SELFTEST_RESULT=FAIL {exc}", flush=True)
            raise SystemExit(1)
        raise SystemExit(0)
```
(정확한 배치·기존 분기의 exit 방식은 :446 블록을 열어 그대로 따른다.)

`smoke-server-bundle.sh`에 기존 REPORT 셀프테스트 호출과 동형으로 `YESON_PDF_SELFTEST=1` 실행 + `grep PDF_SELFTEST_RESULT=PASS` 단계 추가.

- [ ] **11-3: 재동결 + 스모크**

```bash
bash apps/server_desktop/scripts/build-server.sh
bash apps/server_desktop/scripts/smoke-server-bundle.sh
```
Expected: `PDF_SELFTEST_RESULT=PASS` 포함 전체 PASS. (재동결 없이는 데스크톱 앱에서 /pdf-jobs가 404 — 기존 함정.)

- [ ] **11-4: 실물 E2E (수동, 사람 확인 포함)**

```bash
# 터미널 1: 서버 콘솔(동결 번들 사용)
cd apps/server_desktop && pnpm tauri:dev
# 터미널 2: 데스크톱 앱
cd apps/desktop && pnpm tauri:dev
```
체크리스트:
1. "스토리보드 번역" 탭 표시 + 엔진 드롭다운(자막메이커와 동일 목록)
2. `GABE01_A3_FinalShipped.pdf`(129MB) 업로드 → 추출→번역→완료까지 진행률 표시
3. 프리뷰: 원본/번역 토글, 페이지 이동, 번역 주석이 Dialog 아래·Action 하단에 보임
4. "번역 PDF 저장" → 파일 생성, **macOS 미리보기 + (가능하면) Acrobat에서 한글 주석 렌더 확인** ← MuPDF 외 뷰어 검증(어피어런스 폰트 이식성)
5. 수작업본 `GABE01_A3_..._번역.pdf`와 배치 비교 — 위치 규칙(placement 상수) 튜닝 필요 시 `profiles/storyboard.py`의 `_GAP`/`_MIN_WIDTH`/`_estimate_height`만 조정
6. 번역 중 취소 → `cancelled`, 재업로드 정상
7. `EASA04_ColorNotes_V04.pdf` 업로드 → "지원하지 않는 PDF 포맷" 오류 표출(미지원 포맷 안내 확인)

- [ ] **11-5: 문서 갱신 + 커밋**

- `docs/pdf-translation-feasibility-2026-07-29.md` 확정 결정 섹션에 "슬라이스 1(스토리보드형) 구현 완료 — <커밋/브랜치>" 1줄.
- `docs/ROADMAP.md`·`docs/PRD.md`에 스토리보드 번역 기능 항목 추가/체크(파일을 열어 기존 섹션 구조에 맞춰).

```bash
git add -A
git commit -m "feat(pdf-translate): 동결 번들 통합(collect-all pymupdf) + PDF 셀프테스트 + 문서"
```

- [ ] **11-6: 전체 회귀 + PR**

```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest apps/server/tests -x -q
pnpm -C apps/desktop test && pnpm -C apps/desktop build
git push -u origin feat/pdf-translate-slice1
gh pr create --title "feat: 스토리보드 PDF 번역 탭 (슬라이스 1)" \
  --body "$(cat <<'EOF'
## 내용
- 스토리보드 번역 최상위 탭(A안): PDF 업로드 → Storyboard Pro 자동 감지 → Dialog/Action Notes 번역 → FreeText 주석 오버레이 → 프리뷰·다운로드
- PDF 백엔드 격리(backend.py 인터페이스, PyMuPDF 구현 교체점 1파일) + 포맷 프로파일 플러그인 구조
- 번역 프로바이더 prompt_builder 주입(기본값=자막 프롬프트, 잠금 테스트로 무변경 보장)
- PdfJob 모델 + alembic 0007 + 동결 번들(collect-all pymupdf) + PDF 셀프테스트

## 검증
- 서버: test_pdf_* 스위트 + 기존 스위트 회귀(신규 실패 0)
- 실기: GABE01_A3(129MB) 업로드→번역→프리뷰→저장 왕복, 미리보기/Acrobat 한글 주석 렌더 확인
- 남은 실기 검증: Windows 왕복(다음 릴리스 체크리스트)

설계 근거: docs/pdf-translation-feasibility-2026-07-29.md
계획: docs/superpowers/plans/2026-07-29-pdf-translate-slice1.md
EOF
)"
```
(기존 SQLite 비호환 20개 실패는 베이스라인과 동일한지 확인 — 새 실패만 0이어야 한다. PR 머지는 사용자가 `! gh pr merge` — classifier 차단 관례.)

---

## 슬라이스 1 범위 밖 (후속)

- 대본형(Final Draft)·컬러노트·리드시트 프로파일 (구조는 준비됨 — profiles/에 파일 추가)
- 컬러노트·리드시트 한국어 배치 방식 결정(본문 삽입 vs 주석 통일)
- 기존 수작업 번역본에서 원문-번역 쌍 추출 → few-shot/용어집 보강
- Windows 실기 검증(업로드→번역→프리뷰→다운로드 왕복) — 다음 릴리스 체크리스트에 포함
