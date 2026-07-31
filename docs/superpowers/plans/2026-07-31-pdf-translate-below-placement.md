# PDF 스토리보드 번역 배치 — 원문 아래 우선 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 번역 주석을 필드 박스 안 원문 **아래 전폭**에 놓고, 자리가 없을 때만 기존 우측 배치로 폴백한다.

**Architecture:** `PdfBlock`에 필드 박스 상한(`limit_y`, `limit_x1`)을 실어 보내고, `place()`가 아래 배치를 먼저 시도한다. 상한은 `extract()`가 페이지 도형(`page_rects`)에서 읽고, 못 읽으면 다음 필드 라벨 y0로, 그것도 없으면 `None`(=기존 동작)으로 떨어진다.

**Tech Stack:** Python 3.12, PyMuPDF(fitz), pytest

**설계 문서:** `docs/superpowers/specs/2026-07-31-pdf-translate-below-placement-design.md`

## Global Constraints

- 아래 배치 폰트 사다리는 **12pt → 10pt 두 단계에서 끊는다.** 그 아래는 우측이 낫다.
- `_GAP = 4.0`(원문과 주석 사이 여백), `_MIN_WIDTH = 280.0`(주석 박스 최소 폭) — 기존 상수를 그대로 재사용한다.
- **우측 경로(`_place_right_or_below`)와 패널 라벨 경로(`_place_panel_label`)는 이번에 수정하지 않는다.**
- `limit_y is None`이면 **기존과 완전히 동일한 rect**를 반환해야 한다(하위호환 회귀 잠금).
- 어떤 경로든 반환 rect는 원문 bbox와 교차하지 않고, `y1 > y0`이며, `[0, page_h]` 안이어야 한다.
- 테스트는 반드시 **`TEST_DATABASE_URL` SQLite 오버라이드 + 명시 경로**로 돌린다. `apps/server/tests/conftest.py`가 세션 시작 시 Postgres(127.0.0.1:5432)에 접속하고, 실패하면 경로를 명시해도 `pytest.Exit`로 **한 건도 수집하지 않는다**. 아래 형태를 그대로 쓴다(2026-07-31 실측: 이 프로파일 스위트는 DB를 쓰지 않아 22 passed·1 skipped):

  ```bash
  TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" \
    .venv/bin/python -m pytest <경로> -v
  ```

---

### Task 1: `place()` 아래 우선 배치

**Files:**
- Modify: `apps/server/domain/pdf_translate/profiles/base.py:30-35` (`PdfBlock`)
- Modify: `apps/server/domain/pdf_translate/profiles/storyboard.py:122-134` (`place`), 신규 `_place_below_in_box`
- Test: `apps/server/tests/test_pdf_profiles.py`

**Interfaces:**
- Consumes: 없음 (이 태스크가 시작점)
- Produces:
  - `PdfBlock(page, kind, text, bbox, limit_y: float | None = None, limit_x1: float | None = None)`
  - `_place_below_in_box(block: PdfBlock, ko_text: str, page_size: tuple[float, float]) -> Overlay | None`
  - `_BELOW_FONT_SIZES: tuple[float, ...] = (12.0, 10.0)`

- [ ] **Step 1: 실패하는 테스트 4건을 작성한다**

`apps/server/tests/test_pdf_profiles.py`의 `test_place_bottom_edge_block_returns_nondegenerate_onpage_rect` 바로 뒤에 추가:

```python
def test_place_below_when_field_box_has_room():
    """필드 박스 안 원문 아래에 여유가 있으면 좁은 우측 칸 대신 아래 전폭
    12pt로 놓는다(사람 납품본 관례 — GABE01 373p 실측: 원문
    (27.0, 546.7, 790.3, 557.8), 박스 하단 588.0, 사람은 (27.4, 557.1)).
    원문 오른쪽 여유가 _MIN_RIGHT_WIDTH를 넘어도 아래가 우선이다."""
    block = PdfBlock(page=0, kind="action",
                     text="Bobby does the Three Amigos Salute.",
                     bbox=(27.0, 546.7, 790.3, 557.8),
                     limit_y=588.0, limit_x1=985.1)
    ov = StoryboardProfile().place(block, "바비는 오른손을 가슴에 얹는다.",
                                   (1008.0, 612.0))
    assert ov.rect[1] >= block.bbox[3]      # 원문 아래
    assert ov.rect[0] == block.bbox[0]      # 원문과 같은 좌측 정렬
    assert ov.rect[2] > 700.0               # 좁은 우측 칸이 아니라 전폭
    assert ov.rect[3] <= 588.0              # 필드 박스 안
    assert ov.fontsize == 12.0
    assert not _rects_intersect(ov.rect, block.bbox)


def test_place_below_shrinks_to_10pt_when_12pt_does_not_fit():
    """아래 여유가 12pt엔 모자라고 10pt엔 충분하면 아래 배치를 유지하되
    10pt로 줄인다(사다리는 10pt에서 끊는다)."""
    block = PdfBlock(page=0, kind="action", text="a" * 60,
                     bbox=(27.0, 500.0, 790.3, 512.0),
                     limit_y=560.0, limit_x1=985.1)
    ov = StoryboardProfile().place(block, "가" * 130, (1008.0, 612.0))
    assert ov.fontsize == 10.0
    assert ov.rect[1] >= block.bbox[3]
    assert ov.rect[3] <= 560.0


def test_place_falls_back_to_right_when_box_has_no_room_below():
    """박스 아래 여유가 10pt로도 부족하면 기존 우측 경로로 폴백한다
    (실물 Dialog 필드처럼 원문이 박스를 꽉 채운 경우 — 21p 실측:
    원문 하단 516.5, 박스 하단 522.7 → 여유 2.2pt)."""
    block = PdfBlock(page=0, kind="dialog", text="a" * 40,
                     bbox=(27.0, 481.3, 300.0, 516.5),
                     limit_y=522.7, limit_x1=985.1)
    ov = StoryboardProfile().place(
        block, "행크:(노래하며) 밖에서 요리를 하고 싶다면", (1008.0, 612.0))
    assert ov.rect[0] >= block.bbox[2]      # 원문 오른쪽
    assert ov.rect[1] == block.bbox[1]      # 우측 경로의 y 정렬
    assert not _rects_intersect(ov.rect, block.bbox)


def test_place_without_limit_y_keeps_legacy_right_placement():
    """limit_y를 모르면(도형도 다음 라벨도 없는 PDF) 판단 근거가 없으므로
    기존 배치 규칙 그대로 — 상한 없이 아래로 놓으면 박스를 넘어 다음 필드를
    침범한다. 하위호환 회귀 잠금."""
    block = PdfBlock(page=0, kind="dialog", text="If you wanna go, then go.",
                     bbox=(72.0, 400.0, 300.0, 420.0))
    assert block.limit_y is None
    ov = StoryboardProfile().place(block, "가고 싶다면 가세요", (1008.0, 612.0))
    assert ov.rect[0] >= block.bbox[2]      # 우측(기존 규칙)
    assert ov.rect[1] == block.bbox[1]
    assert ov.fontsize == 12.0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/python -m pytest apps/server/tests/test_pdf_profiles.py -k "below_when_field_box or shrinks_to_10pt or no_room_below or without_limit_y" -v`

Expected: 앞 3건이 `TypeError: PdfBlock.__init__() got an unexpected keyword argument 'limit_y'`로 FAIL. (`without_limit_y`는 현재도 통과한다 — 회귀 잠금용이라 정상이다.)

- [ ] **Step 3: `PdfBlock`에 상한 필드를 추가한다**

`apps/server/domain/pdf_translate/profiles/base.py`:

```python
@dataclass(frozen=True)
class PdfBlock:
    page: int   # 0-based
    kind: str   # 프로파일 정의 값 (storyboard: "dialog" | "action")
    text: str   # 정규화된 원문
    bbox: tuple[float, float, float, float]
    # 이 블록이 속한 필드 박스의 하단 y(pt). 배치가 "원문 아래"를 쓸 수
    # 있는지 판정하는 상한이다. 모르면 None — 그때는 기존 배치 규칙 그대로
    # (상한 없이 아래로 놓으면 박스를 넘어 다음 필드를 침범한다).
    limit_y: float | None = None
    # 같은 박스의 우측 x(pt). 아래 배치의 전폭 계산에 쓴다. 모르면 None →
    # page_w - 8(기존 아래 경로와 동일한 값).
    limit_x1: float | None = None
```

- [ ] **Step 4: `place()`에 아래 우선 분기를 넣는다**

`apps/server/domain/pdf_translate/profiles/storyboard.py` — 상수 블록(`_FONT_SIZES` 아래)에 추가:

```python
_BELOW_FONT_SIZES = (12.0, 10.0)  # 아래 배치 사다리 — 10pt에서 끊는다
```

`place()` 본문을 교체:

```python
    def place(self, block: PdfBlock, ko_text: str,
              page_size: tuple[float, float]) -> Overlay:
        """필드(dialog/action)는 **필드 박스 안 원문 아래**를 우선하고, 자리가
        없으면 오른쪽, 패널 콜아웃 라벨은 라벨 바로 위 우선으로 분기한다.

        아래 우선(2026-07-31): 사람 납품본은 아래 여유가 있으면 원문 바로
        아래 전폭 12pt로 쓴다(GABE01 전 1037페이지 실측) — 좁은 우측 칸에
        여러 줄로 접히는 것보다 읽기 쉽다. 2026-07-30에 우측을 우선으로 둔
        이유였던 '원문 가림'은 아래 경로의 시프트업을 제거하면서(fd7b1cd,
        allow_shift=False) 이미 해소됐다 — 지금의 아래 경로는 원문을 덮지
        않는다. 아래가 안 되면 기존 우측 경로 그대로."""
        if block.kind == _PANEL_LABEL_KIND:
            return self._place_panel_label(block, ko_text, page_size)
        below = _place_below_in_box(block, ko_text, page_size)
        if below is not None:
            return below
        return _place_right_or_below(block, ko_text, page_size,
                                     min_right_width=_MIN_RIGHT_WIDTH)
```

`_place_right_or_below` 정의 **바로 위**에 새 함수를 추가:

```python
def _place_below_in_box(block: PdfBlock, ko_text: str,
                        page_size: tuple[float, float]) -> Overlay | None:
    """필드 박스 안 원문 **아래**에 전폭으로 놓을 수 있으면 그 Overlay를,
    자리가 없으면 None(호출부가 기존 우측 경로로 폴백)을 돌려준다.

    상한(block.limit_y)을 모르면 None — 상한 없이 아래로 놓으면 박스를 넘어
    다음 필드를 침범한다. 폭 규칙(_MIN_WIDTH 하한, 원문 우측 상한)은 기존
    아래 경로와 같은 것을 쓴다."""
    if block.limit_y is None:
        return None
    page_w, page_h = page_size
    bx0, _by0, bx1, by1 = block.bbox
    y0 = by1 + _GAP
    limit = min(block.limit_y, page_h - 4.0)
    room = limit - y0
    if room <= 0:
        return None
    right = (block.limit_x1 - 8.0) if block.limit_x1 is not None \
        else (page_w - 8.0)
    x1 = min(right, max(bx1, bx0 + _MIN_WIDTH))
    if x1 <= bx0:
        return None
    for fontsize in _BELOW_FONT_SIZES:
        height = _estimate_height(ko_text, x1 - bx0, fontsize)
        if height <= room:
            rect = _clamp_nondegenerate(bx0, y0, x1, y0 + height, page_h)
            return Overlay(page=block.page, rect=rect, text=ko_text,
                           fontsize=fontsize)
    return None
```

- [ ] **Step 5: 새 테스트 4건이 통과하는지 확인한다**

Run: `TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/python -m pytest apps/server/tests/test_pdf_profiles.py -k "below_when_field_box or shrinks_to_10pt or no_room_below or without_limit_y" -v`

Expected: 4 passed

- [ ] **Step 6: 기존 배치 테스트가 전부 그대로인지 확인한다**

Run: `TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/python -m pytest apps/server/tests/test_pdf_profiles.py -v`

Expected: 전부 PASS. 기존 `place()` 테스트는 모두 `PdfBlock`을 직접 만들어 `limit_y`가 `None`이므로 경로가 바뀌지 않는다. 단 `test_place_returns_rect_within_page_and_not_intersecting_source`만 `extract()`를 거치는데, Task 3 전까지는 `extract()`가 아직 `limit_y`를 채우지 않으므로 이 시점에도 통과한다.

- [ ] **Step 7: 커밋**

```bash
git add apps/server/domain/pdf_translate/profiles/base.py \
        apps/server/domain/pdf_translate/profiles/storyboard.py \
        apps/server/tests/test_pdf_profiles.py
git commit -m "feat(pdf-translate): 번역 주석 필드 박스 안 아래 우선 배치"
```

---

### Task 2: 백엔드 `page_rects`

**Files:**
- Modify: `apps/server/domain/pdf_translate/backend.py:42-54` (`PdfDocument` Protocol)
- Modify: `apps/server/domain/pdf_translate/backend_mupdf.py:100-108` (`producer` 다음)
- Test: `apps/server/tests/test_pdf_backend.py`

**Interfaces:**
- Consumes: 없음
- Produces: `PdfDocument.page_rects(page: int) -> list[tuple[float, float, float, float]]` — 중복 제거 + `(y0, x0)` 오름차순

**주의:** `PdfDocument`는 구조적 Protocol이고 프로덕션 구현은 `MuPdfDocument` 하나뿐이다(테스트에도 가짜 문서 구현이 없다 — 전부 `open_pdf`로 실제 합성 PDF를 연다). 백엔드 교체(pypdfium2) 시 이 메서드도 함께 구현해야 한다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`apps/server/tests/test_pdf_backend.py` 맨 끝에 추가:

```python
def test_page_rects_returns_drawn_rectangles(tmp_path):
    """필드 박스 판정의 원재료 — 페이지의 벡터 도형 경계 사각형.
    좌표는 실물(GABE01) Action Notes 박스와 같은 값을 쓴다."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.draw_rect(fitz.Rect(24.0, 525.7, 985.1, 588.0),
                   color=(0, 0, 0), width=1)
    path = tmp_path / "boxes.pdf"
    doc.save(path)
    doc.close()

    pdf = open_pdf(path)
    try:
        rects = pdf.page_rects(0)
        assert any(abs(r[0] - 24.0) < 1.0 and abs(r[1] - 525.7) < 1.0
                   and abs(r[2] - 985.1) < 1.0 and abs(r[3] - 588.0) < 1.0
                   for r in rects)
    finally:
        pdf.close()


def test_page_rects_empty_without_drawings(tmp_path):
    """도형이 없는 텍스트-only 페이지 → 빈 리스트(프로파일이 폴백을 타야 함)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((72, 500), "Dialog", fontsize=8)
    path = tmp_path / "notext.pdf"
    doc.save(path)
    doc.close()

    pdf = open_pdf(path)
    try:
        assert pdf.page_rects(0) == []
    finally:
        pdf.close()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/python -m pytest apps/server/tests/test_pdf_backend.py -k page_rects -v`

Expected: FAIL — `AttributeError: 'MuPdfDocument' object has no attribute 'page_rects'`

- [ ] **Step 3: Protocol에 선언을 추가한다**

`apps/server/domain/pdf_translate/backend.py`의 `PdfDocument`에서 `raw_blocks` 다음 줄에 추가:

```python
    def page_rects(self, page: int) -> list[tuple[float, float, float, float]]: ...
```

- [ ] **Step 4: MuPDF 구현을 추가한다**

`apps/server/domain/pdf_translate/backend_mupdf.py`의 `producer()` 바로 위에 추가:

```python
    def page_rects(self, page: int) -> list[tuple[float, float, float, float]]:
        """페이지 벡터 도형의 경계 사각형들 — 프로파일이 '필드 박스'를 찾는
        원재료다. 어떤 것이 필드 박스인지(폭·높이 문턱, 포함 관계)는 포맷별
        관례라 프로파일이 판단한다. 중복은 제거하고 (y0, x0) 오름차순."""
        seen: set[tuple[float, float, float, float]] = set()
        for d in self._doc[page].get_drawings():
            r = d["rect"]
            seen.add((r.x0, r.y0, r.x1, r.y1))
        return sorted(seen, key=lambda r: (r[1], r[0]))
```

- [ ] **Step 5: 통과를 확인한다**

Run: `TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/python -m pytest apps/server/tests/test_pdf_backend.py -v`

Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add apps/server/domain/pdf_translate/backend.py \
        apps/server/domain/pdf_translate/backend_mupdf.py \
        apps/server/tests/test_pdf_backend.py
git commit -m "feat(pdf-translate): 백엔드 page_rects (필드 박스 판정 원재료)"
```

---

### Task 3: `extract()`가 상한을 배선한다

**Files:**
- Modify: `apps/server/domain/pdf_translate/profiles/storyboard.py:80-120` (`extract`), `215-267` (`_field_content`), 신규 `_field_box` · `_next_label_y0`
- Test: `apps/server/tests/test_pdf_profiles.py`

**Interfaces:**
- Consumes: `PdfDocument.page_rects` (Task 2), `PdfBlock.limit_y` / `limit_x1` (Task 1)
- Produces:
  - `_next_label_y0(raws: list[RawBlock], next_label: str | None) -> float | None`
  - `_field_box(rects: list[tuple[float, float, float, float]], bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float] | None`

- [ ] **Step 1: 실패하는 테스트 2건 + fixture를 작성한다**

`apps/server/tests/test_pdf_profiles.py`의 `_make_storyboard_pdf_empty_dialog` 정의 바로 뒤에 fixture를 추가:

```python
def _make_storyboard_pdf_with_field_boxes(tmp_path: Path) -> Path:
    """실물(GABE01) 템플릿을 흉내 낸 합성 페이지 — 필드 박스 사각형까지
    그린다(좌표도 실물과 동일). 기존 _make_storyboard_pdf는 도형이 없어
    (get_drawings() == []) limit_y가 라벨 폴백으로만 정해진다."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.draw_rect(fitz.Rect(24.0, 460.3, 985.1, 522.7),
                   color=(0, 0, 0), width=1)   # Dialog 박스
    page.draw_rect(fitz.Rect(24.0, 525.7, 985.1, 588.0),
                   color=(0, 0, 0), width=1)   # Action Notes 박스
    page.insert_text((27, 474), "Dialog", fontsize=12)
    page.insert_text((27, 492), "HANK walks in.", fontsize=10)
    page.insert_text((27, 540), "Action Notes", fontsize=12)
    page.insert_text((27, 556), "Bobby does the Salute.", fontsize=10)
    path = tmp_path / "sb_boxes.pdf"
    doc.save(path)
    doc.close()
    return path
```

그리고 Task 1에서 추가한 테스트들 뒤에 추가:

```python
def test_extract_sets_limit_from_field_box_rectangle(tmp_path):
    """도형이 있는 페이지: 필드 블록의 limit_y/limit_x1이 그 블록을 감싸는
    필드 박스에서 온다(Dialog 박스 하단 522.7 / Action Notes 588.0)."""
    doc = open_pdf(_make_storyboard_pdf_with_field_boxes(tmp_path))
    try:
        blocks = StoryboardProfile().extract(doc)
        dialog = next(b for b in blocks if b.kind == "dialog")
        action = next(b for b in blocks if b.kind == "action")
        assert dialog.limit_y == pytest.approx(522.7, abs=1.0)
        assert action.limit_y == pytest.approx(588.0, abs=1.0)
        assert dialog.limit_x1 == pytest.approx(985.1, abs=1.0)
    finally:
        doc.close()


def test_extract_falls_back_to_next_label_when_no_drawings(tmp_path):
    """도형이 없는 PDF: Dialog는 다음 라벨(Action Notes) y0 - _GAP를 상한으로
    받고, 마지막 필드(Action Notes)는 근거가 없어 None으로 남는다
    (= 그 필드는 기존 우측 배치 그대로)."""
    doc = open_pdf(_make_storyboard_pdf(tmp_path))
    try:
        blocks = StoryboardProfile().extract(doc)
        dialog = next(b for b in blocks if b.kind == "dialog")
        action = next(b for b in blocks if b.kind == "action")
        assert dialog.limit_y is not None and dialog.limit_y < 551.4
        assert dialog.limit_x1 is None
        assert action.limit_y is None
    finally:
        doc.close()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/python -m pytest apps/server/tests/test_pdf_profiles.py -k "limit_from_field_box or falls_back_to_next_label" -v`

Expected: FAIL — `assert None == approx(522.7 ± 1.0)` (extract가 아직 상한을 채우지 않는다)

- [ ] **Step 3: 상한 산출 헬퍼 2개를 추가한다**

`apps/server/domain/pdf_translate/profiles/storyboard.py` — 상수 블록에 추가:

```python
# 필드 박스로 인정할 사각형의 최소 크기 — 패널 테두리·표 셀 같은 작은
# 도형을 후보에서 뺀다(GABE01 실측: 필드 박스는 폭 961pt·높이 62pt).
_FIELD_BOX_MIN_WIDTH = 300.0
_FIELD_BOX_MIN_HEIGHT = 15.0
```

`_field_content` 정의 **바로 위**에 추가:

```python
def _next_label_y0(raws: list[RawBlock], next_label: str | None) -> float | None:
    """다음 필드 라벨의 y0 — 라벨+내용이 한 블록으로 붙어 나오는 변형도
    경계로 인정한다. _field_content의 창 상한과 배치 상한이 **같은 규칙**을
    쓰도록 한 곳에 모은 것이다(규칙이 갈라지면 내용 창과 배치 상한이 어긋난다)."""
    if next_label is None:
        return None
    for b in raws:
        t = normalize_ws(b.text)
        if t == next_label or (t.startswith(next_label)
                               and len(t) > len(next_label)):
            return b.bbox[1]
    return None


def _field_box(rects: list[tuple[float, float, float, float]],
               bbox: tuple[float, float, float, float],
               ) -> tuple[float, float, float, float] | None:
    """원문 bbox를 감싸는 **가장 작은** 필드 박스 사각형 — 없으면 None.
    가장 작은 것을 고르는 이유: 페이지 테두리처럼 전체를 감싸는 큰 사각형이
    같이 잡히면 상한이 페이지 하단까지 열려 다음 필드를 침범한다."""
    x0, y0, x1, y1 = bbox
    best = None
    for r in rects:
        if (r[2] - r[0] < _FIELD_BOX_MIN_WIDTH
                or r[3] - r[1] < _FIELD_BOX_MIN_HEIGHT):
            continue
        if (r[0] <= x0 + 1.0 and r[1] <= y0 + 1.0
                and x1 <= r[2] + 1.0 and y1 <= r[3] + 1.0):
            if best is None or (r[3] - r[1]) < (best[3] - best[1]):
                best = r
    return best
```

- [ ] **Step 4: `_field_content`가 헬퍼를 쓰도록 바꾼다**

`_field_content` 안의 `upper_bound` 계산 블록(주석 포함 `upper_bound = None` ~ `break`까지)을 한 줄로 교체:

```python
    upper_bound = _next_label_y0(raws, next_label)
```

교체로 사라지는 주석의 요지(다음 라벨이 내용과 붙어 나오는 변형도 경계로 인정해야 크로스필드 누수가 없다)는 `_next_label_y0` docstring이 이어받는다.

- [ ] **Step 5: `extract()`가 상한을 실어 보내게 한다**

`extract()`의 페이지 루프에서 `raws = repair_corrupt_words(...)` 다음 줄에 추가:

```python
            rects = doc.page_rects(page)
```

같은 루프의 `out.append(PdfBlock(page=page, kind=kind, ...))` 블록을 교체:

```python
                box = _field_box(rects, content.bbox)
                if box is not None:
                    limit_y, limit_x1 = box[3], box[2]
                else:
                    # 도형이 없으면 다음 필드 라벨을 상한으로. 마지막 필드는
                    # 뒤에 라벨이 없어 None으로 남는다 = 기존 우측 배치 그대로.
                    next_y0 = _next_label_y0(raws, next_label)
                    limit_y = None if next_y0 is None else next_y0 - _GAP
                    limit_x1 = None
                out.append(PdfBlock(page=page, kind=kind, text=text,
                                    bbox=content.bbox,
                                    limit_y=limit_y, limit_x1=limit_x1))
```

- [ ] **Step 6: 새 테스트 2건이 통과하는지 확인한다**

Run: `TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/python -m pytest apps/server/tests/test_pdf_profiles.py -k "limit_from_field_box or falls_back_to_next_label" -v`

Expected: 2 passed

- [ ] **Step 7: 경로가 바뀐 기존 테스트의 docstring을 고친다**

`test_place_returns_rect_within_page_and_not_intersecting_source`는 이제 `extract()`가 `limit_y`(≈547.4)를 채우므로 **아래 경로**를 탄다(여유 62.4pt, 12pt 1줄 22.5pt). 단언은 전부 그대로 통과하지만 docstring이 "오른쪽 배치가 선택된다"라고 말하고 있어 사실과 어긋난다. docstring을 교체:

```python
    """이 fixture의 Dialog 블록은 다음 라벨(Action Notes)이 상한이 되어
    아래 여유가 충분하므로 2026-07-31 배치 규칙상 **아래** 배치가 선택된다.
    경로와 무관한 불변식만 검증한다(원문 비교차 + 페이지 안 + 12pt)."""
```

- [ ] **Step 8: PDF 스위트 전체를 돌린다**

Run: `TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/python -m pytest apps/server/tests/test_pdf_profiles.py apps/server/tests/test_pdf_backend.py apps/server/tests/test_pdf_run.py apps/server/tests/test_pdf_translate_blocks.py -v`

Expected: 전부 PASS

- [ ] **Step 9: 린트**

Run: `.venv/bin/python -m ruff check apps/server/domain/pdf_translate apps/server/tests/test_pdf_profiles.py apps/server/tests/test_pdf_backend.py`

Expected: `All checks passed!`

- [ ] **Step 10: 커밋**

```bash
git add apps/server/domain/pdf_translate/profiles/storyboard.py \
        apps/server/tests/test_pdf_profiles.py
git commit -m "feat(pdf-translate): extract가 필드 박스 상한을 배치에 배선"
```

---

### Task 4: 실물 검증 (GABE01_A1)

**Files:**
- Create: `/private/tmp/claude-500/-Users-usabatch-coding-yeson-dev-yeson-meet/d2ea7df3-f1f2-40e9-b8d6-f2fb6ac6104c/scratchpad/verify_below.py` (스크래치패드 — 리포에 커밋하지 않는다)
- Modify: `docs/superpowers/specs/2026-07-31-pdf-translate-below-placement-design.md` (상태 줄 + §9 정정)

**Interfaces:**
- Consumes: Task 1~3 전부
- Produces: 없음(검증 태스크)

**전제:** 원본 `/Users/usabatch/Downloads/1601_콘티번역/GABE01_A1_FinalShipped.pdf`, 사람 납품본 `.../GABE01_A1_FinalShipped_번역.pdf`. 실행 인터프리터는 pymupdf가 있는 `target/server-build-venv/bin/python`을 쓰고, `PYTHONPATH`에 리포 루트를 준다.

- [ ] **Step 1: 검증 스크립트를 작성한다**

```python
"""실물 3페이지 배치 검증 — 373p(아래로 가야 함) / 2p·21p(우측 유지)."""
import os
os.environ.setdefault("YESON_PDF_PANEL_OCR", "0")  # 패널 OCR 없이 배치만 본다

from apps.server.domain.pdf_translate.backend import open_pdf
from apps.server.domain.pdf_translate.profiles.storyboard import StoryboardProfile

SRC = "/Users/usabatch/Downloads/1601_콘티번역/GABE01_A1_FinalShipped.pdf"
KO = {
    "action": "바비는 오른손을 가슴에 얹고 왼손을 반대쪽 가슴에 댄 다음 "
              "엉덩이를 앞으로 튕겨\"삼총사 경례\"를 한다.죠셉도 그와 같이 한다.",
    "dialog": "행크:(노래하며)밖에서 요리를 하고 싶다면...",
}

doc = open_pdf(SRC)
profile = StoryboardProfile()
try:
    for pno in (372, 1, 20):
        blocks = [b for b in profile.extract(doc)
                  if b.page == pno and b.kind in ("dialog", "action")]
        print(f"\n=== page {pno + 1} ===")
        for b in blocks:
            ov = profile.place(b, KO[b.kind], doc.page_size(pno))
            where = "아래" if ov.rect[1] >= b.bbox[3] else "우측"
            print(f"  {b.kind:6s} 원문={tuple(round(v, 1) for v in b.bbox)}")
            print(f"         limit_y={b.limit_y} limit_x1={b.limit_x1}")
            print(f"         → {where} rect={tuple(round(v, 1) for v in ov.rect)}"
                  f" {ov.fontsize}pt")
finally:
    doc.close()
```

- [ ] **Step 2: 실행하고 기대값과 대조한다**

Run:
```bash
PYTHONPATH=/Users/usabatch/coding/yeson_dev/yeson_meet \
  target/server-build-venv/bin/python \
  /private/tmp/claude-500/-Users-usabatch-coding-yeson-dev-yeson-meet/d2ea7df3-f1f2-40e9-b8d6-f2fb6ac6104c/scratchpad/verify_below.py
```

Expected:
- **373p action** — `limit_y=588.0`, `limit_x1=985.1`, **아래**, `rect ≈ (27.0, 561.8, 790.3, 584.3)`, `12.0pt`.
  사람 납품본은 `(27.4, 557.1, 747.4, 569.3)`이다. x0는 ±1pt 안, y0는 `_GAP`(4pt) 때문에 약 4.7pt 아래로 내려온다 — **의도된 차이**이므로 좌표 완전 일치를 요구하지 말고 다음만 확인한다: 원문 아래 · `rect[3] <= 588.0` · 12pt · 좌측 정렬(x0 ≈ 27).
- **2p action** — 영문 3줄이 박스를 채워 아래 여유가 없다 → **우측** 유지.
- **21p dialog** — 원문 하단 516.5 / 박스 하단 522.7(여유 2.2pt) → **우측** 유지.

기대와 다르면 멈추고 보고한다(특히 373p가 우측으로 남거나, 2p·21p가 아래로 바뀌는 경우).

- [ ] **Step 3: 설계 문서의 상태와 §9를 정정한다**

`docs/superpowers/specs/2026-07-31-pdf-translate-below-placement-design.md`:

1. 상태 줄을 `- 상태: 설계 승인 완료(2026-07-31) · 구현 완료`로 바꾼다.
2. §8 표의 마지막 행 설명 `신규 4건 + 기존 우측 테스트 fixture 조정` → `신규 6건 + 기존 테스트 docstring 정정`으로 바꾼다.
3. §9의 다음 문장을 삭제하고 아래 문장으로 교체한다.

   삭제: "기존 `test_place_prefers_right_side_when_room_available`은 fixture가 이제 아래로 가므로 `limit_y`를 좁게 준 '아래 불가' 조건으로 바꿔 우측 경로 커버리지를 유지한다"

   교체: "기존 `place()` 테스트는 모두 `PdfBlock`을 직접 만들어 `limit_y`가 `None`이므로 경로가 바뀌지 않는다 — 조정이 필요한 것은 `extract()`를 거치는 `test_place_returns_rect_within_page_and_not_intersecting_source`의 docstring뿐이다. 실제 상한이 있는 우측 경로는 신규 `test_place_falls_back_to_right_when_box_has_no_room_below`가 덮는다."

   (설계 당시엔 `limit_y`에 기본값을 두는 결정 전이라 기존 테스트가 깨질 것으로 봤는데, 기본값 `None` 덕에 깨지지 않는다.)

- [ ] **Step 4: 커밋**

```bash
git add docs/superpowers/specs/2026-07-31-pdf-translate-below-placement-design.md
git commit -m "docs(pdf-translate): 아래 배치 설계 상태·테스트 계획 정정"
```

- [ ] **Step 5: 재동결 안내**

번들 서버에 반영하려면 재동결이 필요하다고 사용자에게 보고한다(자동 실행하지 않는다):

```bash
bash apps/server_desktop/scripts/build-server.sh
```

`apps/server` 소스 변경은 프로즌 번들을 다시 만들고 서버 앱을 재시작해야 반영된다.

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 항목 | 태스크 |
|---|---|
| §6.1 아래 우선, 박스 하단 상한 | Task 1 Step 4 (`_place_below_in_box`) |
| §6.2 사다리 10pt에서 끊기 | Task 1 Step 4 (`_BELOW_FONT_SIZES`) + 테스트 2 |
| §6.3 아래 불가 → 기존 우측 | Task 1 Step 4 (`return None` → 폴백) + 테스트 3 |
| §6.4 도형에서 박스 읽기 + 3단 폴백 | Task 2 전체, Task 3 Step 3·5 |
| §6.5 우측 경로 박스 초과는 범위 밖 | 손대지 않음 (Global Constraints에 명시) |
| §6.6 패널 라벨 무수정 | `place()`의 `_PANEL_LABEL_KIND` 분기를 그대로 둠 |
| §7.1 `PdfBlock.limit_y` | Task 1 Step 3 |
| §7.2 `page_rects` | Task 2 Step 3·4 |
| §7.3 extract 3단 폴백 | Task 3 Step 5 |
| §7.4 place 분기 | Task 1 Step 4 |
| §9 단위 검증 6종 | Task 1 테스트 4건 + Task 3 테스트 2건 |
| §9 실물 검증 | Task 4 |

빠진 항목 없음.

**2. 플레이스홀더** — 모든 코드 스텝에 실제 코드가 있고, "적절히 처리" 류 문구 없음.

**3. 타입 일관성** — `limit_y` / `limit_x1` / `_place_below_in_box` / `_BELOW_FONT_SIZES` / `_field_box` / `_next_label_y0` / `_FIELD_BOX_MIN_WIDTH` / `_FIELD_BOX_MIN_HEIGHT`가 정의부와 사용부에서 동일하게 쓰임. `_field_box`는 `tuple | None`을 돌려주고 Task 3 Step 5가 `None` 분기를 처리함. `_next_label_y0`는 `float | None`을 돌려주고 `_field_content`(창 상한)와 `extract`(배치 상한) 양쪽에서 같은 시그니처로 쓰임.

**스펙과의 차이 1건** — Task 4 Step 3에서 문서에 반영한다: 설계 §9는 기존 우측 테스트 fixture를 고쳐야 한다고 했으나, `limit_y` 기본값 `None` 덕에 기존 테스트는 깨지지 않는다. 실제 상한이 있는 우측 경로 커버리지는 신규 테스트가 담당한다.
