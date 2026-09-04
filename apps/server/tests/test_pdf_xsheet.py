"""엑스시트 프로파일 + 손글씨 전사 모듈 테스트.

실물 OCR/CLI 없이 시임으로 돈다: OCR은 xsheet._new_engine, 전사 CLI는
handwriting_transcribe._run_cli를 갈아끼운다. 실물 KOTH 샘플 검증은
YESON_PDF_SAMPLES 게이트 테스트(맨 아래)가 담당한다(기존 스토리보드 게이트와
같은 env — ~/Downloads를 가리키는 게 관례).
"""
from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from apps.server.db.models import PdfJob
from apps.server.domain.pdf_translate import handwriting_transcribe as ht
from apps.server.domain.pdf_translate import pdf_run
from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir
from apps.server.domain.pdf_translate.profiles import (
    detect_profile,
    profile_by_name,
    profile_names,
)
from apps.server.domain.pdf_translate.profiles import xsheet as xs
from apps.server.domain.pdf_translate.profiles.base import PdfBlock

# ---------------------------------------------------------------- fakes


def _png_marked(w: int, h: int, box: tuple[int, int, int, int]) -> bytes:
    """지정한 픽셀 사각형만 검은 흰 페이지 — 크롭 그림이 달라지는지 볼 때."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(im).rectangle(box, fill="black")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _png_bytes(w: int = 64, h: int = 64) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


class FakeDoc:
    """PdfDocument 프로토콜 최소 구현 — render_png는 진짜 PNG를 돌려준다
    (_decode_png가 PIL로 실제 디코드하므로 가짜 바이트로는 안 된다)."""

    def __init__(self, *, pages: int = 1, size=(792.0, 1224.0),
                 text: str = "", png: bytes | None = None,
                 scanned: bool = True):
        self._pages = pages
        self._size = size
        self._text = text
        self._png = png or _png_bytes()
        self._scanned = scanned
        self.render_calls: list[tuple[int, int]] = []

    @property
    def page_count(self) -> int:
        return self._pages

    def page_size(self, page: int):
        return self._size

    def image_rects(self, page: int):
        """스캔본이면 페이지를 덮는 이미지 하나(detect의 판정 근거)."""
        if not self._scanned:
            return []
        return [(0.0, 0.0, self._size[0], self._size[1])]

    def raw_blocks(self, page: int):
        if not self._text:
            return []
        from apps.server.domain.pdf_translate.backend import RawBlock
        return [RawBlock(text=self._text, bbox=(0, 0, 10, 10))]

    def render_png(self, page: int, *, dpi: int = 120,
                   annots: bool = True) -> bytes:
        assert annots is False, "추출·크롭은 스캔만 봐야 한다"
        self.render_calls.append((page, dpi))
        return self._png


class FakeEngine:
    """RapidOCR 시늉 — 준비된 (박스, 텍스트, conf)를 그대로 돌려준다."""

    def __init__(self, items: list[tuple[tuple[float, float, float, float], str, float]]):
        self._items = items
        self.calls = 0

    def __call__(self, arr):
        self.calls += 1
        boxes = []
        for (x0, y0, x1, y1), text, conf in self._items:
            boxes.append(([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], text, conf))
        return boxes, 0.0


@pytest.fixture(autouse=True)
def _isolate_ocr_engine():
    """OCR 엔진은 스레드 로컬 싱글턴이라 테스트 사이에 새어 나간다 —
    가짜 엔진이 남아 실물 샘플 테스트가 빈 결과를 받는 사고를 막는다."""
    xs._reset_engines()
    yield
    xs._reset_engines()


def _install_engine(monkeypatch, engine: FakeEngine) -> None:
    monkeypatch.setattr(xs, "_new_engine", lambda **kw: engine)
    xs._reset_engines()


def _px(pt: float, dpi: int) -> float:
    return pt * dpi / 72.0


def _box_px(rect_pt, dpi):
    x0, y0, x1, y1 = rect_pt
    return (_px(x0, dpi), _px(y0, dpi), _px(x1, dpi), _px(y1, dpi))


# ---------------------------------------------------------------- detect


def test_registry_has_xsheet():
    assert profile_names() == ("storyboard", "xsheet")
    assert profile_by_name("xsheet") is not None


def test_detect_skips_ocr_for_non_scanned_documents(monkeypatch):
    engine = FakeEngine([])
    _install_engine(monkeypatch, engine)
    doc = FakeDoc(text="Dialog Action Notes and plenty of body text here",
                  scanned=False)
    assert xs.XsheetProfile().detect(doc) is False
    assert engine.calls == 0  # 스캔이 아니면 OCR 비용을 쓰지 않는다


def test_detect_scanned_sheet_by_header_tokens(monkeypatch):
    dpi = xs._DETECT_DPI
    engine = FakeEngine([
        (_box_px((400, 100, 460, 112), dpi), "ANIMATOR", 0.99),
        (_box_px((470, 100, 520, 112), dpi), "DIALOG", 0.99),
    ])
    _install_engine(monkeypatch, engine)
    assert xs.XsheetProfile().detect(FakeDoc()) is True


def test_detect_scanned_but_not_a_sheet(monkeypatch):
    _install_engine(monkeypatch, FakeEngine([]))
    assert xs.XsheetProfile().detect(FakeDoc()) is False


def test_detect_profile_order_prefers_storyboard(monkeypatch):
    # 스토리보드 합성 문서(텍스트 레이어 있음)는 xsheet OCR 없이 storyboard로
    engine = FakeEngine([])
    _install_engine(monkeypatch, engine)
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((680, 460), "Dialog", fontsize=8)
    page.insert_text((72, 560), "Action Notes", fontsize=8)
    from apps.server.domain.pdf_translate.backend_mupdf import MuPdfDocument
    mu = MuPdfDocument.__new__(MuPdfDocument)
    mu._doc = doc  # 파일 저장 없이 열린 문서를 직접 주입
    profile = detect_profile(mu)
    assert profile is not None and profile.name == "storyboard"
    assert engine.calls == 0


# ---------------------------------------------------------------- extract


def _header_row(dpi, labels):
    """칸 머리글 줄 OCR 항목 — 기하 유도의 앵커."""
    out = []
    for text, x0, x1 in labels:
        out.append((_box_px((x0, 160, x1, 172), dpi), text, 1.0))
    return out


def test_extract_filters_template_and_clusters(monkeypatch):
    """머리글에서 유도한 칸 구조로 서식·음소·번호를 걸러낸다."""
    dpi = xs._OCR_DPI
    items = _header_row(dpi, [("ACTION", 100, 140), ("DIALOG", 370, 400),
                              ("EXP", 405, 425), ("CAMERA NOTES", 700, 780)])
    items += [
        # 번호 컬럼(숫자만 촘촘히) — 12개 이상 모여야 컬럼으로 인정된다
        *[(_box_px((355, 200 + i * 20, 365, 210 + i * 20), dpi), str(i + 1), 1.0)
          for i in range(14)],
        (_box_px((410, 300, 420, 310), dpi), "EH", 0.9),       # 립싱크 음소
        (_box_px((380, 400, 400, 410), dpi), "HARRIS", 0.9),   # 화자 이름은 남는다
        (_box_px((100, 1200, 130, 1210), dpi), "410", 0.99),   # 푸터 아래
        (_box_px((600, 300, 640, 310), dpi), "FOOTAGE", 0.99),
        (_box_px((50, 200, 95, 209), dpi), "SUBnE", 0.6),      # 손글씨 노트
        (_box_px((50, 212, 100, 221), dpi), "T2Em3LE", 0.55),
        (_box_px((700, 300, 760, 310), dpi), "TRUCK UP", 0.7),
        (_box_px((50, 400, 90, 410), dpi), "행크", 0.9),        # 한글 재투입 차단
        # 푸터 라벨(아래쪽) — 이게 있어야 footer_y가 잡힌다
        (_box_px((100, 1150, 160, 1162), dpi), "PROD NO", 1.0),
    ]
    _install_engine(monkeypatch, FakeEngine(items))
    blocks = xs.XsheetProfile().extract(FakeDoc())
    # 화자 스트립 의사 블록은 전사 단계의 내부 소비물 — 노트만 비교한다
    texts = sorted(b.text for b in blocks if b.kind != xs.STRIP_KIND)
    assert texts == ["HARRIS", "SUBnE T2Em3LE", "TRUCK UP"]
    stacked = next(b for b in blocks if b.text.startswith("SUBnE"))
    assert stacked.kind == xs.NOTE_KIND
    assert stacked.bbox == (50.0, 200.0, 100.0, 221.0)


class FakePagedEngine:
    """페이지마다 다른 항목을 주는 RapidOCR 시늉 — extract가 페이지 순서로
    _ocr_page를 부르므로 호출 순번 = 페이지 번호로 대응한다."""

    def __init__(self, pages_items):
        self._pages = pages_items
        self.calls = 0

    def __call__(self, arr):
        items = self._pages[min(self.calls, len(self._pages) - 1)]
        self.calls += 1
        boxes = []
        for (x0, y0, x1, y1), text, conf in items:
            boxes.append(([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], text, conf))
        return boxes, 0.0


def test_extract_recovers_handwritten_name_above_header(monkeypatch):
    """머리글 위 구역의 손글씨 이름을 회수한다 — A2 실측(2026-08-24): 사람이
    번역한 페이지 상단 빨간 원 이름(HANK 등) 27건이 `y1 < header_y` 일괄
    컷에 죽었다. p71에서 RapidOCR이 (409,22) 'HANK'를 잡았는데도 버려지는
    것을 확인. 인쇄 타이틀·고정 칸 번호와의 구분은 **페이지 간 위치 반복**
    (인쇄물은 매 페이지 같은 자리) + 알파벳 3자 미만 배제로 한다 — 작품
    종속 어휘를 박지 않기 위해서다."""
    dpi = xs._OCR_DPI
    header = [
        ((166, 105, 201, 117), "ACTION", 1.0),
        ((369, 99, 410, 116), "DIALOG", 1.0),
        ((408, 102, 429, 116), "EXP", 1.0),
        ((100, 1150, 160, 1162), "PROD NO", 1.0),   # footer_y 근거
    ]
    printed = [
        ((33, 8, 89, 30), "KONG", 1.0),             # 인쇄 타이틀(매 페이지 동일 자리)
        ((36, 51, 88, 72), "HILLZ.", 1.0),
        ((582, 83, 620, 99), "354", 1.0),           # 손글씨 씬 번호(고정 칸) — 숫자
        ((54, 81, 90, 104), "(QH)", 1.0),           # 낙서 코드 — 알파벳 2자
    ]
    pages = []
    for p in range(10):
        items = [(_box_px(r, dpi), t, c) for r, t, c in header + printed]
        if p == 3:   # 손글씨 이름은 일부 페이지에만, 자리도 조금씩 다르다
            items.append((_box_px((409, 22, 460, 53), dpi), "HANK", 0.8))
        if p == 5:
            items.append((_box_px((402, 31, 455, 60), dpi), "DALE", 0.8))
        if p == 7:
            # A2 실측 누수: OCR이 머리글 두 라벨을 한 덩어리로 읽으면
            # 어휘·반복 게이트를 다 피한다(분절이 페이지마다 달라 반복
            # 계수가 분산). 머리글 줄 밴드에 걸친 항목은 텍스트 무관 배제.
            items.append((_box_px((370, 101, 429, 116), dpi), "DIALOG EXP", 0.9))
        pages.append(items)
    _install_engine(monkeypatch, FakePagedEngine(pages))
    blocks = [b for b in xs.XsheetProfile().extract(FakeDoc(pages=10))
              if b.kind != xs.STRIP_KIND]
    texts = sorted(b.text for b in blocks)
    assert texts == ["DALE", "HANK"]                 # 타이틀·번호·코드는 안 샌다
    assert [b.page for b in blocks] == sorted(b.page for b in blocks)
    assert all(b.kind == xs.NOTE_KIND for b in blocks)


def test_extract_header_recovery_needs_multiple_pages(monkeypatch):
    """1페이지 문서는 위치 반복 판별이 불가능하다 — 회수를 끄고 옛 동작을
    유지한다(인쇄 타이틀이 통째로 새는 것보다 낫다)."""
    dpi = xs._OCR_DPI
    items = [(_box_px(r, dpi), t, c) for r, t, c in [
        ((166, 105, 201, 117), "ACTION", 1.0),
        ((369, 99, 410, 116), "DIALOG", 1.0),
        ((408, 102, 429, 116), "EXP", 1.0),
        ((100, 1150, 160, 1162), "PROD NO", 1.0),
        ((33, 8, 89, 30), "KONG", 1.0),
        ((409, 22, 460, 53), "HANK", 0.8),
    ]]
    _install_engine(monkeypatch, FakeEngine(items))
    blocks = xs.XsheetProfile().extract(FakeDoc(pages=1))
    assert [b.text for b in blocks
            if b.kind != xs.STRIP_KIND] == []        # 머리글 위는 전부 보류


def test_geometry_follows_a_different_studio_layout(monkeypatch):
    """⛔양식은 작품마다 다르다 — 좌표를 박지 않는다. BM802(titmouse) 실측
    배치처럼 대사 칸이 5쌍이면 그 **전체 구간**이 음소 칸으로 잡혀야 한다."""
    labels = [("ACTION", 126, 165), ("DIALO", 299, 329), ("EXP", 338, 359),
              ("DIAL 2", 365, 395), ("EXP", 401, 422), ("DIAL 3", 427, 459),
              ("EXP", 464, 485), ("DIAL4", 486, 520), ("EXP", 524, 544),
              ("DIAL5", 547, 580), ("EXP", 583, 604), ("TRUCK", 638, 668),
              ("CAMERA NOTES", 714, 805)]
    # _derive_geometry는 pt 좌표를 받는다(_ocr_page가 이미 환산해 넘긴다)
    items = [((x0, 160.0, x1, 172.0), text, 1.0) for text, x0, x1 in labels]
    g = xs._derive_geometry(items, 841.92, 1189.92)
    assert g is not None
    lo, hi = g.dialog_band
    assert lo < 300 and hi > 600          # 다섯 쌍 전체를 덮는다
    # KOTH 좌표(음소 밴드 370~432)만 덮는 옛 방식이었다면 DIAL4·5가 샜다
    assert xs._is_template((550, 300, 560, 310), "ay", g) is True
    assert xs._is_template((550, 300, 590, 310), "HARRIS", g) is False


def test_handwritten_dial_exp_survive_below_header():
    """⛔DIAL/EXP 텍스트 필터는 머리글 줄에만 — 본문 손글씨를 죽이면 안 된다.

    A2 실측(2026-08-24): 사람이 번역한 `EYES EXP`(→시선.표정)·`#78 DIAL`
    (→78,대화)을 위치 무관 필터가 템플릿으로 오인해 지웠다(누락 286건 중
    33건). p47에서 RapidOCR이 (177,215) 'EXP'를 **잡았는데** 필터가 죽이는
    것을 직접 확인했다."""
    labels = [("ACTION", 126, 165), ("DIALO", 299, 329), ("EXP", 338, 359),
              ("TRUCK", 638, 668)]
    items = [((x0, 160.0, x1, 172.0), text, 1.0) for text, x0, x1 in labels]
    g = xs._derive_geometry(items, 841.92, 1189.92)
    assert g is not None
    # 머리글 줄의 인쇄 라벨은 여전히 템플릿이다
    assert xs._is_template((338, 160, 359, 172), "EXP", g) is True
    # 본문(머리글 아래)의 손글씨 EXP·DIAL은 노트다 — 대사 밴드 밖 좌표
    assert xs._is_template((177, 215, 208, 229), "EXP", g) is False
    assert xs._is_template((180, 300, 220, 315), "DIAL", g) is False


# ---------------------------------------------------------------- place


def test_place_prefers_right_of_note():
    profile = xs.XsheetProfile()
    block = PdfBlock(page=0, kind=xs.NOTE_KIND, text="x",
                     bbox=(50, 200, 95, 225), limit_x1=351.6)
    ov = profile.place(block, "행크에 내내 떨림.", (792.0, 1224.0))
    assert ov.fontsize == xs._FONTSIZE
    assert ov.rect[0] == pytest.approx(98.0)         # 원문 오른쪽에서 시작
    assert ov.rect[2] <= 351.6 + 0.01        # 열 경계를 넘지 않는다
    assert ov.rect[1] == pytest.approx(200.0)


def test_place_falls_back_below_when_row_is_full():
    profile = xs.XsheetProfile()
    block = PdfBlock(page=0, kind=xs.NOTE_KIND, text="x",
                     bbox=(10, 200, 340, 210), limit_x1=351.6)
    ov = profile.place(block, "아주 긴 번역 문장이 들어간다.", (792.0, 1224.0))
    assert ov.rect[1] == pytest.approx(212.0)        # 원문 아래
    assert ov.rect[0] == pytest.approx(10.0)
    assert ov.rect[2] <= 351.6 + 0.01


# ------------------------------------------------------ transcribe module


def _note(page: int, x0: float, y0: float) -> PdfBlock:
    return PdfBlock(page=page, kind=xs.NOTE_KIND, text="raw",
                    bbox=(x0, y0, x0 + 40, y0 + 10), limit_x1=None)


def _touch_crops(job_dir: Path, blocks: list[PdfBlock]) -> None:
    crops = job_dir / ht._CROPS_DIRNAME
    crops.mkdir(parents=True, exist_ok=True)
    for b in blocks:
        (crops / ht.crop_name(b)).write_bytes(b"png")


def test_crop_name_is_stable_across_runs():
    a = _note(0, 50.2, 200.7)
    b = _note(0, 50.2, 200.7)
    assert ht.crop_name(a) == ht.crop_name(b) == "p001_50_200.png"


def test_transcribe_replaces_text_and_drops_unusable(tmp_path, monkeypatch):
    blocks = [_note(0, 50, 200), _note(0, 50, 300), _note(0, 50, 400)]
    _touch_crops(tmp_path, blocks)
    names = [ht.crop_name(b) for b in blocks]
    canned = {names[0]: "WALK\nWEST.", names[1]: "", names[2]: "147"}
    monkeypatch.setattr(ht, "_run_cli",
                        lambda prompt, cwd, engine=None: json.dumps(canned))
    out = ht.transcribe(blocks, tmp_path)
    # 빈값(마커)·숫자만(셀번호)은 떨어지고 실노트만 남는다
    assert len(out) == 1
    assert out[0].text == "WALK\nWEST."
    assert out[0].bbox == blocks[0].bbox
    # 캐시가 남아 재실행 시 CLI를 다시 부르지 않는다
    cache = json.loads((tmp_path / ht._CACHE_NAME).read_text(encoding="utf-8"))
    assert cache[names[0]] == "WALK\nWEST."

    def _boom(prompt, cwd, engine=None):
        raise AssertionError("캐시가 있으면 CLI를 부르면 안 된다")
    monkeypatch.setattr(ht, "_run_cli", _boom)
    out2 = ht.transcribe(blocks, tmp_path)
    assert [b.text for b in out2] == ["WALK\nWEST."]


def test_transcribe_batches_and_survives_one_failure(tmp_path, monkeypatch):
    # 실패 몫은 응답률 안전망(_MIN_ANSWERED) 아래로 내려가지 않을 만큼만 —
    # 10장 중 2장 실패(80% 응답)는 "일부 배치 실패"의 정상 처리 경로다.
    blocks = [_note(0, 50, 100 + i * 20) for i in range(10)]
    _touch_crops(tmp_path, blocks)
    monkeypatch.setattr(ht, "_BATCH", 2)
    monkeypatch.setattr(ht, "_workers", lambda: 1)  # 순서 단정은 직렬로
    calls: list[list[str]] = []

    def _fake(prompt, cwd, engine=None):
        batch = [n for n in (ht.crop_name(b) for b in blocks) if n in prompt]
        calls.append(batch)
        if len(calls) == 1:
            raise RuntimeError("첫 배치 실패")
        return json.dumps({n: f"NOTE {i}" for i, n in enumerate(batch)})

    monkeypatch.setattr(ht, "_run_cli", _fake)
    out = ht.transcribe(blocks, tmp_path)
    assert len(calls) == 5                      # 실패해도 다음 배치는 돈다
    assert len(out) == 8                        # 실패 배치 몫만 떨어진다


def test_transcribe_retries_empty_reads_with_upscale(tmp_path, monkeypatch):
    """빈 전사("")는 확답이 아니라 판독 실패다 — 2배 확대본으로 한 번 더
    묻는다(A2 실측 2026-08-24: 빈 전사 304장 중 114장이 사람이 번역한 노트
    자리. 슬레이트 2×/0.6× 재판독과 같은 계보). 재판독이 읽히면 채택한다."""
    blocks = [_note(0, 50, 200), _note(0, 50, 300)]
    crops = tmp_path / ht._CROPS_DIRNAME
    crops.mkdir(parents=True)
    names = [ht.crop_name(b) for b in blocks]
    for n in names:
        (crops / n).write_bytes(_png_bytes())
    calls: list[str] = []

    def _fake(prompt, cwd, engine=None):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({names[0]: "", names[1]: "WALK IN"})
        assert ht._RETRY_PREFIX + names[0] in prompt
        return json.dumps({ht._RETRY_PREFIX + names[0]: "HANK BLINKS"})

    monkeypatch.setattr(ht, "_run_cli", _fake)
    out = ht.transcribe(blocks, tmp_path)
    assert len(calls) == 2
    assert sorted(b.text for b in out) == ["HANK BLINKS", "WALK IN"]
    cache = json.loads((tmp_path / ht._CACHE_NAME).read_text(encoding="utf-8"))
    assert cache[names[0]] == "HANK BLINKS"   # 캐시도 원래 이름으로 갱신
    assert not (crops / (ht._RETRY_PREFIX + names[0])).exists()  # 확대본 정리


def test_transcribe_retries_cached_empty_once(tmp_path, monkeypatch):
    """이전 런이 캐시에 남긴 ""도 재판독한다 — 이 경로가 없으면 실물 잡의
    빈 전사(A2: 304장)는 재번역을 해도 영원히 안 읽힌다. 여전히 ""이면
    그대로 두고(같은 런에서 무한 재시도 없음) 블록은 떨어진다."""
    blocks = [_note(0, 50, 200), _note(0, 50, 300)]
    crops = tmp_path / ht._CROPS_DIRNAME
    crops.mkdir(parents=True)
    names = [ht.crop_name(b) for b in blocks]
    for n in names:
        (crops / n).write_bytes(_png_bytes())
    (tmp_path / ht._CACHE_NAME).write_text(
        json.dumps({names[0]: "WALK", names[1]: ""}), encoding="utf-8")
    calls: list[str] = []

    def _fake(prompt, cwd, engine=None):
        calls.append(prompt)
        assert names[0] not in prompt          # 확답 캐시는 다시 묻지 않는다
        return json.dumps({ht._RETRY_PREFIX + names[1]: ""})   # 여전히 빈값

    monkeypatch.setattr(ht, "_run_cli", _fake)
    out = ht.transcribe(blocks, tmp_path)
    assert len(calls) == 1                     # 재판독 한 번만
    assert [b.text for b in out] == ["WALK"]
    cache = json.loads((tmp_path / ht._CACHE_NAME).read_text(encoding="utf-8"))
    assert cache[names[1]] == ""               # 실패 기록은 남는다


def test_transcribe_cancellation(tmp_path, monkeypatch):
    blocks = [_note(0, 50, 100)]
    _touch_crops(tmp_path, blocks)
    monkeypatch.setattr(ht, "_run_cli",
                        lambda prompt, cwd, engine=None: (_ for _ in ()).throw(
                            AssertionError("취소면 CLI를 부르면 안 된다")))
    with pytest.raises(asyncio.CancelledError):
        ht.transcribe(blocks, tmp_path, should_continue=lambda: False)


def test_render_crops_writes_pngs(tmp_path):
    from PIL import Image
    doc = FakeDoc(png=_png_bytes(400, 400))
    blocks = [_note(0, 2, 2)]
    ht.render_crops(doc, blocks, tmp_path)
    dest = tmp_path / ht._CROPS_DIRNAME / ht.crop_name(blocks[0])
    assert dest.exists()
    with Image.open(dest) as im:
        assert im.size[0] > 0 and im.size[1] > 0
    assert doc.render_calls == [(0, ht._CROP_DPI)]


def test_changed_crop_range_drops_its_stale_transcript(tmp_path):
    """같은 이름인데 범위가 바뀐 크롭은 다시 굽고 **전사 캐시를 버린다**.

    크롭 이름은 (페이지, x0, y0)뿐이라, 과병합 블록이 쪼개져 위쪽 조각이
    같은 x0·y0를 유지하면 옛 크롭 그림과 옛 전사가 조용히 재사용된다 —
    그러면 지금은 이웃 노트의 것이 된 낱말이 이 노트 번역에 섞인다.
    A2 밀집 10페이지 실측: 클러스터 규칙을 고치자 302블록 중 6건(2%)이
    바로 그 충돌이었다."""
    import json as _json

    from PIL import Image
    # 큰 범위에만 들어가는 잉크 — 두 크롭 그림이 실제로 달라야 함정이 성립한다
    doc = FakeDoc(png=_png_marked(400, 400, (250, 250, 300, 300)))
    big = PdfBlock(page=0, kind=xs.NOTE_KIND, text="raw",
                   bbox=(20.0, 20.0, 90.0, 90.0), limit_x1=None)
    ht.render_crops(doc, [big], tmp_path)
    name = ht.crop_name(big)
    dest = tmp_path / ht._CROPS_DIRNAME / name
    with Image.open(dest) as im:
        before = im.size
    cache = tmp_path / ht._CACHE_NAME
    cache.write_text(_json.dumps({name: "BIG NOTE", "other.png": "KEEP"}),
                     encoding="utf-8")

    # 같은 x0·y0, 더 좁은 범위 = 쪼개진 위쪽 조각
    small = PdfBlock(page=0, kind=xs.NOTE_KIND, text="raw",
                     bbox=(20.0, 20.0, 50.0, 40.0), limit_x1=None)
    assert ht.crop_name(small) == name, "이름이 같아야 이 함정이 성립한다"
    ht.render_crops(doc, [small], tmp_path)
    with Image.open(dest) as im:
        assert im.size != before, "범위가 바뀌었으면 다시 구워야 한다"
    assert (tmp_path / ht._RECTS_NAME).exists(), "크롭 범위를 기록해 둬야 한다"
    left = _json.loads(cache.read_text(encoding="utf-8"))
    assert name not in left, "낡은 전사를 버려야 한다"
    assert left["other.png"] == "KEEP", "남의 캐시까지 지우면 안 된다"


def test_unchanged_crop_keeps_its_transcript(tmp_path):
    """반대로 그림이 그대로면 전사 캐시를 건드리지 않는다(토큰 재소모 방지)."""
    import json as _json
    doc = FakeDoc(png=_png_marked(400, 400, (250, 250, 300, 300)))
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="raw",
                 bbox=(20.0, 20.0, 90.0, 90.0), limit_x1=None)
    ht.render_crops(doc, [b], tmp_path)
    cache = tmp_path / ht._CACHE_NAME
    cache.write_text(_json.dumps({ht.crop_name(b): "NOTE"}), encoding="utf-8")
    # 같은 블록으로 한 번 더 — 페이지에 빠진 크롭이 없으니 렌더 자체를 건너뛴다
    ht.render_crops(doc, [b], tmp_path)
    assert _json.loads(cache.read_text(encoding="utf-8")) == {
        ht.crop_name(b): "NOTE"}


def test_cluster_does_not_bridge_diagonal_neighbours():
    """대각선으로만 가까운 것은 잇지 않는다 — 서로 다른 세로 스택이다.

    A2 p36 실측: `DRT`(x135-181)와 `AMBERS`(x76-129)가 가로 6.2pt·세로
    5.5pt로 비스듬히 붙어, 두 칼럼 13박스가 144×162pt 한 덩어리가 됐다
    (사람 기준 노트 4개). 그 덩어리는 번역도 13줄 낱말 기둥으로 나온다."""
    left = ((76.0, 729.0, 129.0, 750.0), "AMBERS")
    right = ((135.0, 705.0, 181.0, 723.5), "RT")
    assert len(xs._cluster([left, right])) == 2, "대각선은 다리가 아니다"

    # 같은 세로 스택(가로가 겹침)은 그대로 이어 붙는다
    stack = [((135.0, 666.0, 183.0, 682.0), "AMBER"),
             ((137.0, 680.0, 184.0, 697.0), "EYES")]
    assert len(xs._cluster(stack)) == 1
    # 같은 줄(세로가 겹침)도 그대로
    row = [((42.0, 400.0, 64.0, 414.0), "EXP"),
           ((67.0, 402.0, 79.0, 413.0), "50")]
    assert len(xs._cluster(row)) == 1


def test_xsheet_asks_for_human_line_shaping_storyboard_does_not():
    """엑스시트만 줄 나누기 규칙을 바꾼다 — 스토리보드는 원문 줄 보존.

    근거(A2 사람 납품본 실측 2026-08-27): 사람 주석 높이 중앙 13pt(=한 줄)·
    3줄 이상 3%인데 우리는 28pt·26%였다. 원인은 번역문 줄 수가 원문 줄 수를
    그대로 복사한 것(1/2/3줄 35/35/15% 대 원문 36/33/15%)."""
    from apps.server.domain.pdf_translate.profiles.storyboard import (
        StoryboardProfile,
    )
    from apps.server.domain.pdf_translate.translate_blocks import (
        build_pdf_prompt,
    )
    rule = xs.XsheetProfile.prompt_line_rule
    assert rule and "one line" in rule.lower()
    assert getattr(StoryboardProfile, "prompt_line_rule", None) is None

    xsheet_prompt = build_pdf_prompt(["LT\nHAND"], line_rule=rule)
    assert "Do NOT mirror that stacking" in xsheet_prompt
    assert "Preserve \\n line breaks" not in xsheet_prompt
    default_prompt = build_pdf_prompt(["LT\nHAND"])
    assert "Preserve \\n line breaks" in default_prompt


def test_extract_emits_speaker_strip_blocks(monkeypatch):
    """대사 칸에 음소 런이 있는 페이지마다 화자 스트립 의사 블록 1개 —
    A2 실측(2026-08-25): 화자 이름(연필 원·굵은 연필)을 RapidOCR이 전
    스케일에서 못 읽어 사람 대비 이름 누락 ~50건. 스트립을 비전 CLI가
    통째로 읽고(위치 포함) 전사 단계가 화자 블록을 합성한다. 구조는
    블록 text(JSON)에 실어 보낸다 — 프로파일은 싱글턴이라 인스턴스
    상태를 두면 잡 간에 샌다."""
    dpi = xs._OCR_DPI
    header = [
        ((166, 105, 201, 117), "ACTION", 1.0),
        ((369, 99, 410, 116), "DIALOG", 1.0),
        ((408, 102, 429, 116), "EXP", 1.0),
        ((100, 1150, 160, 1162), "PROD NO", 1.0),
    ]
    phonemes = [((375, 200 + i * 20, 395, 214 + i * 20), "EH", 0.9)
                for i in range(3)]
    pages = [
        [(_box_px(r, dpi), t, c) for r, t, c in header + phonemes],  # 런 있음
        [(_box_px(r, dpi), t, c) for r, t, c in header],             # 런 없음
    ]
    _install_engine(monkeypatch, FakePagedEngine(pages))
    blocks = xs.XsheetProfile().extract(FakeDoc(pages=2))
    strips = [b for b in blocks if b.kind == xs.STRIP_KIND]
    # 밴드가 있으면 런이 없어도 스트립을 낸다(이름은 음소 없이도 쓰인다)
    assert [s.page for s in strips] == [0, 1]
    ctx = json.loads(strips[0].text)
    assert ctx["runs"] and abs(ctx["runs"][0] - 200.0) < 2.0
    assert ctx["band"][0] < 375 < ctx["band"][1]
    assert json.loads(strips[1].text)["runs"] == []


def test_transcribe_synthesizes_speaker_blocks(tmp_path, monkeypatch):
    """스트립 스캔 결과(이름+y비율) → 런에 스냅한 화자 블록 합성.
    스트립 의사 블록 자신은 산출물에서 사라지고, 이름이 없는 연속
    페이지(페이지-톱 런)는 직전 화자를 이어 기재한다."""
    strip0 = PdfBlock(page=0, kind=xs.STRIP_KIND,
                      text=json.dumps({"runs": [200.0], "band": [367.0, 432.0],
                                       "top": 120.0}),
                      bbox=(277.0, 120.0, 472.0, 1180.0))
    strip1 = PdfBlock(page=1, kind=xs.STRIP_KIND,
                      text=json.dumps({"runs": [125.0], "band": [367.0, 432.0],
                                       "top": 120.0}),
                      bbox=(277.0, 120.0, 472.0, 1180.0))
    note = _note(0, 50, 200)
    blocks = [note, strip0, strip1]
    crops = tmp_path / ht._CROPS_DIRNAME
    crops.mkdir(parents=True)
    for b in blocks:
        (crops / ht.crop_name(b)).write_bytes(_png_bytes())
    s0, s1 = ht.crop_name(strip0), ht.crop_name(strip1)

    def _fake(prompt, cwd, engine=None):
        if s0 in prompt or s1 in prompt:
            # p1 스트립: y=0.08 → 120+0.08*1060=204.8 → 런 200에 스냅
            # p2 스트립: 이름 없음 → 연속 기재 대상
            return json.dumps({s0: [{"text": "HANK", "y": 0.08}], s1: []})
        return json.dumps({ht.crop_name(note): "WALKS"})

    monkeypatch.setattr(ht, "_run_cli", _fake)
    out = xs.XsheetProfile().transcribe_blocks(blocks, tmp_path)
    assert not [b for b in out if b.kind == xs.STRIP_KIND]   # 의사 블록 소멸
    speakers = [b for b in out if b.text in ("HANK",)]
    assert len(speakers) == 2                                # p1 스캔 + p2 연속
    assert speakers[0].page == 0 and abs(speakers[0].bbox[1] - 200.0) < 1e-6
    assert speakers[1].page == 1 and abs(speakers[1].bbox[1] - 125.0) < 1e-6
    assert [b.text for b in out if b.page == 0 and b.kind == xs.NOTE_KIND
            and b.text == "WALKS"]                           # 일반 노트 무사


def test_speaker_assignment_is_precision_capped():
    """정밀 안전판 — 런 배정은 이름이 150pt 안에 있을 때만. 멀리서
    전파하면 스캔이 놓친 이름 자리에 엉뚱한 이름이 들어간다(A2 실측:
    v2 무제한 전파가 사람 대조에서 오표기 10건 — p83 행크 자리에 데일).
    오표기가 누락보다 나쁘다."""
    strip = PdfBlock(page=0, kind=xs.STRIP_KIND,
                     text=json.dumps({"runs": [200.0, 500.0, 900.0],
                                      "band": [367.0, 432.0], "top": 120.0}),
                     bbox=(277.0, 120.0, 472.0, 1180.0))
    # HANK y≈205 → 런 200만(500은 295pt 거리라 배정 금지) · DALE y≈862 → 런 900
    scans = {ht.crop_name(strip): [{"text": "HANK", "y": 0.08},
                                   {"text": "DALE", "y": 0.70}]}
    out = xs._synthesize_speakers([strip], scans, [])
    got = sorted((round(b.bbox[1]), b.text) for b in out)
    assert got == [(200, "HANK"), (900, "DALE")]


def test_speaker_carry_requires_adjacent_page():
    """이월은 직전 페이지에서 본 화자만 — 여러 페이지를 건너뛴 이월은
    화자가 바뀌었을 위험이 커서 하지 않는다(정밀 안전판)."""
    def _strip(page):
        return PdfBlock(page=page, kind=xs.STRIP_KIND,
                        text=json.dumps({"runs": [125.0],
                                         "band": [367.0, 432.0], "top": 120.0}),
                        bbox=(277.0, 120.0, 472.0, 1180.0))
    s0, s2 = _strip(0), _strip(2)          # 1페이지 건너뜀
    scans = {ht.crop_name(s0): [{"text": "HANK", "y": 0.005}],
             ht.crop_name(s2): []}
    out = xs._synthesize_speakers([s0, s2], scans, [])
    assert [(b.page, b.text) for b in out] == [(0, "HANK")]   # p2 이월 없음


def test_decode_code_note_pure_codes_only():
    """순수 코드 노트만 결정적 해독 — 토큰 하나라도 사전 밖이면 번역기 몫.

    A2 실측(2026-08-24): 번역 LLM이 원문을 그대로 돌려주면(에코) pdf_run이
    "번역 실패"로 보아 주석을 버린다 — 사람이 오버슛·안착·표정으로 옮긴
    코드 노트 16건이 그렇게 증발했다. OUS·DVS는 OVS의 전사 오독 실측."""
    assert xs._decode_code_note("OVS\nSTL") == "오버슛\n안착"
    assert xs._decode_code_note("OUS") == "오버슛"
    assert xs._decode_code_note("DVS") == "오버슛"
    assert xs._decode_code_note("EYES EXP") == "시선 표정"
    assert xs._decode_code_note("CUSH") == "쿠션"
    assert xs._decode_code_note("LT ARM") is None      # LT가 사전 밖
    assert xs._decode_code_note("HANK") == "행크"      # 이름표(2026-09-03)
    assert xs._decode_code_note("ZORK") is None        # 사전 밖 = 번역기 몫
    assert xs._decode_code_note("") is None


def test_transcribe_predecodes_code_notes(tmp_path, monkeypatch):
    """전사 결과가 순수 코드면 block.ko를 채워 에코-드롭을 우회한다
    (판넬 약어 predecode와 같은 경로 — pdf_run이 block.ko를 우선한다)."""
    blocks = [_note(0, 50, 200), _note(0, 50, 300)]
    _touch_crops(tmp_path, blocks)
    names = [ht.crop_name(b) for b in blocks]
    canned = {names[0]: "OVS\nSTL", names[1]: "HANK WALKS"}
    monkeypatch.setattr(ht, "_run_cli",
                        lambda prompt, cwd, engine=None: json.dumps(canned))
    out = xs.XsheetProfile().transcribe_blocks(blocks, tmp_path)
    by_text = {b.text: b for b in out}
    assert by_text["OVS\nSTL"].ko == "오버슛\n안착"
    assert by_text["HANK WALKS"].ko is None


async def test_echoed_groups_retried_once_with_dedicated_prompt(monkeypatch):
    """에코(번역=원문) 그룹은 전용 프롬프트로 **한 번** 재번역한다 — 이름
    (HANK)은 LLM 기분에 따라 에코되곤 해서 확률적으로 증발했다(A2 실측
    16건). 음소(HU HU)는 재시도에서도 에코 → 여전히 드롭되어야 한다."""
    from apps.server.domain.pdf_translate.profiles.base import PdfBlock
    from apps.server.domain.pdf_translate.utterances import group_utterances

    blocks = [
        PdfBlock(page=0, kind=xs.NOTE_KIND, text="HANK", bbox=(0, 0, 10, 10)),
        PdfBlock(page=0, kind=xs.NOTE_KIND, text="HU HU", bbox=(0, 20, 10, 30)),
        PdfBlock(page=0, kind=xs.NOTE_KIND, text="WALKS", bbox=(0, 40, 10, 50)),
    ]
    groups, group_texts = group_utterances(blocks)
    builders = []

    class First:
        async def translate_batch(self, texts):
            return [t if t in ("HANK", "HU HU") else "걷는다" for t in texts]

    class Retry:
        async def translate_batch(self, texts):
            return ["행크" if t == "HANK" else t for t in texts]

    def fake_create(provider, cli_model=None, prompt_builder=None):
        builders.append(prompt_builder)
        return First() if len(builders) == 1 else Retry()

    monkeypatch.setattr(pdf_run, "create_translator", fake_create)
    ko = await pdf_run._translate_group_texts(
        xs.XsheetProfile(), blocks, groups, group_texts,
        provider="claude", cli_model=None, progress_cb=None)
    got = {g.merged_text: ko[i] for i, g in enumerate(groups)}
    assert got["HANK"] == "행크"                        # 재시도가 살림
    assert got["HU HU"].strip() == "HU HU"             # 음소는 그대로 → 드롭 유지
    assert got["WALKS"] == "걷는다"                     # 1차 결과 보존
    assert len(builders) == 2                           # 재시도 딱 한 번
    from apps.server.domain.pdf_translate.translate_blocks import (
        build_pdf_retry_prompt,
    )
    # 전용 프롬프트 사용 + 프로파일의 줄 규칙이 재시도에도 실린다
    assert builders[1].func is build_pdf_retry_prompt
    assert (builders[1].keywords["line_rule"]
            .startswith(xs.XsheetProfile.prompt_line_rule))   # + 이름표(동적 꼬리)


# ------------------------------------------------------ pipeline + API


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


@pytest.fixture
def _pipeline_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(pdf_run, "create_translator",
                        lambda provider, cli_model, prompt_builder: FakeTranslator())
    yield


async def _seed_job(db_session, admin_user, *, fmt: str | None) -> PdfJob:
    eid = uuid4()
    src = pdf_job_dir(eid) / "source.pdf"
    _make_storyboard_pdf(src)
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="sb.pdf", status="queued", source_path=str(src),
                 format=fmt)
    db_session.add(job)
    await db_session.commit()
    return job


async def test_format_hint_mismatch_fails_loudly(
        db_session, admin_user, _pipeline_env, monkeypatch):
    """스토리보드 PDF를 엑스시트 힌트로 돌리면 조용히 엉뚱한 결과 대신
    명확한 오류로 끝나야 한다."""
    _install_engine(monkeypatch, FakeEngine([]))
    job = await _seed_job(db_session, admin_user, fmt="xsheet")
    eid = job.external_id  # expire_all() 뒤 만료 속성 동기 재로드는
    # aiosqlite에서 MissingGreenlet — 만료 전에 캡처(test_pdf_run.py 선례)
    await pdf_run.run_pdf_job(eid)
    db_session.expire_all()
    row = (await db_session.execute(select(PdfJob).where(
        PdfJob.external_id == eid))).scalar_one()
    assert row.status == "error"
    assert "포맷" in (row.error or "")


async def test_format_hint_matching_profile_runs(
        db_session, admin_user, _pipeline_env):
    job = await _seed_job(db_session, admin_user, fmt="storyboard")
    eid = job.external_id
    await pdf_run.run_pdf_job(eid)
    db_session.expire_all()
    row = (await db_session.execute(select(PdfJob).where(
        PdfJob.external_id == eid))).scalar_one()
    assert row.status == "done"
    assert row.format == "storyboard"


def _tiny_pdf_bytes() -> bytes:
    import fitz
    doc = fitz.open()
    doc.new_page(width=1008, height=612)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def _api_env(monkeypatch, tmp_path):
    from apps.server.api.v1 import pdf_jobs as api_pdf
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(api_pdf, "_start_pdf_pipeline", lambda eid: None)
    monkeypatch.setattr(api_pdf, "_prune_old_jobs", lambda: None)
    yield


async def test_upload_accepts_format_hint(client, admin_user, db_session, _api_env):
    resp = await client.post(
        "/api/v1/pdf-jobs/upload",
        data={"format_hint": "xsheet"},
        files={"file": ("KOTH_1401_A1.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 201
    row = (await db_session.execute(select(PdfJob).where(
        PdfJob.external_id == UUID(resp.json()["job_id"])))).scalar_one()
    assert row.format == "xsheet"


async def test_upload_rejects_unknown_format_hint(client, admin_user, _api_env):
    resp = await client.post(
        "/api/v1/pdf-jobs/upload",
        data={"format_hint": "banana"},
        files={"file": ("a.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 422


# ------------------------------------------------- real-sample (gated)

SAMPLES = os.environ.get("YESON_PDF_SAMPLES")


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
def test_real_xsheet_sample_detect_and_extract():
    """KOTH_1401_A1 실물: detect가 xsheet를 잡고, extract가 사람 주석
    분포(페이지당 ~20개)와 같은 자릿수의 블록을 만들어야 한다.
    전체 188p는 느리므로 앞 3페이지만 훑는다."""
    from apps.server.domain.pdf_translate.backend import open_pdf

    path = (Path(SAMPLES) / "script_trans" / "1401_XSHEETS_번역"
            / "KOTH_1401_A1.pdf")
    if not path.exists():
        pytest.skip("샘플 PDF 없음")
    doc = open_pdf(path)
    try:
        profile = detect_profile(doc)
        assert profile is not None and profile.name == "xsheet"
        # extract는 전 페이지를 돌므로 여기선 페이지 2 하나만 검사
        blocks = [b for b in _extract_pages(profile, doc, pages=2)]
        page2 = [b for b in blocks if b.page == 1]
        assert 5 <= len(page2) <= 40   # 실측 13블록(±여유)
    finally:
        doc.close()


def _extract_pages(profile, doc, *, pages: int):
    """실물 테스트 전용: extract와 같은 로직을 앞 N페이지로 제한."""
    from unittest import mock
    real_count = type(doc).page_count
    with mock.patch.object(type(doc), "page_count",
                           property(lambda self: pages)):
        _ = real_count
        return profile.extract(doc)


def test_transcribe_splits_failed_batch_and_recovers(tmp_path, monkeypatch):
    """대형 배치 타임아웃(A1 p182 실측: 20장 배치 2개가 600s 초과)은 반으로
    나눠 재시도해 회복한다 — 반토막은 단조 감소라 무한 재시도가 불가능."""
    blocks = [_note(0, 50, 100 + i * 20) for i in range(4)]
    _touch_crops(tmp_path, blocks)
    monkeypatch.setattr(ht, "_BATCH", 4)
    monkeypatch.setattr(ht, "_SPLIT_MIN", 2)
    monkeypatch.setattr(ht, "_workers", lambda: 1)  # 순서 단정은 직렬로
    calls: list[int] = []

    def _fake(prompt, cwd, engine=None):
        batch = [n for n in (ht.crop_name(b) for b in blocks) if n in prompt]
        calls.append(len(batch))
        if len(batch) > 2:
            raise TimeoutError("배치가 크면 타임아웃")
        return json.dumps({n: f"NOTE {n}" for n in batch})

    monkeypatch.setattr(ht, "_run_cli", _fake)
    out = ht.transcribe(blocks, tmp_path)
    assert calls == [4, 2, 2]          # 통배치 실패 → 반쪽 2개로 회복
    assert len(out) == 4               # 잃은 노트 0


def test_transcribe_parallel_workers(tmp_path, monkeypatch):
    """워커 3이 배치들을 나란히 처리해도 결과 병합·캐시가 온전해야 한다."""
    import threading
    blocks = [_note(0, 50, 100 + i * 20) for i in range(6)]
    _touch_crops(tmp_path, blocks)
    monkeypatch.setattr(ht, "_BATCH", 2)
    monkeypatch.setenv(ht.ENV_WORKERS, "3")
    seen = set()
    barrier = threading.Barrier(3, timeout=10)

    def _fake(prompt, cwd, engine=None):
        batch = [n for n in (ht.crop_name(b) for b in blocks) if n in prompt]
        barrier.wait()  # 3배치가 실제로 동시에 떠 있어야 통과한다
        seen.update(batch)
        return json.dumps({n: f"NOTE {n}" for n in batch})

    monkeypatch.setattr(ht, "_run_cli", _fake)
    out = ht.transcribe(blocks, tmp_path)
    assert len(out) == 6 and len(seen) == 6


def test_transcribe_reports_progress_including_cache(tmp_path, monkeypatch):
    """진행률 분자에 캐시 몫이 포함돼야 재개 런의 진행률이 이어져 보인다."""
    blocks = [_note(0, 50, 100 + i * 20) for i in range(4)]
    _touch_crops(tmp_path, blocks)
    names = [ht.crop_name(b) for b in blocks]
    # 절반은 이전 런의 캐시
    (tmp_path / ht._CACHE_NAME).write_text(
        json.dumps({names[0]: "OLD A", names[1]: "OLD B"}), encoding="utf-8")
    monkeypatch.setattr(ht, "_BATCH", 1)
    monkeypatch.setattr(ht, "_workers", lambda: 1)
    monkeypatch.setattr(ht, "_run_cli", lambda prompt, cwd, engine=None: json.dumps(
        {n: f"NEW {n}" for n in names if n in prompt}))
    fracs: list[float] = []
    out = ht.transcribe(blocks, tmp_path, on_progress=fracs.append)
    assert len(out) == 4
    assert fracs == [0.75, 1.0]        # 시작점이 이미 0.5, 배치마다 +0.25


def test_render_crops_skips_fully_cached_pages(tmp_path):
    """크롭이 이미 다 있는 페이지는 렌더 자체를 건너뛴다 — A1 전량 실측에서
    재개 런이 188페이지를 300dpi로 헛렌더하며 10분+를 태웠다."""
    doc = FakeDoc(pages=2, png=_png_bytes(400, 400))
    p0 = [_note(0, 2, 2)]
    p1 = [_note(1, 2, 2)]
    ht.render_crops(doc, p0 + p1, tmp_path)
    assert sorted(doc.render_calls) == [(0, ht._CROP_DPI), (1, ht._CROP_DPI)]

    doc.render_calls.clear()
    ht.render_crops(doc, p0 + p1, tmp_path)
    assert doc.render_calls == []          # 전부 캐시 → 렌더 0회

    doc.render_calls.clear()
    (tmp_path / ht._CROPS_DIRNAME / ht.crop_name(p1[0])).unlink()
    ht.render_crops(doc, p0 + p1, tmp_path)
    assert doc.render_calls == [(1, ht._CROP_DPI)]   # 빠진 페이지만 렌더


def test_blocks_cache_roundtrip_preserves_every_field(tmp_path):
    """추출 캐시는 블록을 손실 없이 복원한다 — 한 필드만 빠져도 배치·번역이
    조용히 달라진다(limit_x1이 없으면 열 경계가 풀리고, ko가 없으면 결정적
    해독이 사라져 에코-드롭이 되살아난다)."""
    blocks = [
        PdfBlock(page=3, kind=xs.NOTE_KIND, text="RT FT\nSTEP",
                 bbox=(1.5, 2.5, 3.5, 4.5), limit_y=9.0, limit_x1=351.6,
                 ko="오른발 스텝"),
        PdfBlock(page=0, kind=xs.STRIP_KIND, text='{"band": [1, 2]}',
                 bbox=(0.0, 0.0, 1.0, 1.0)),
    ]
    pdf_run._save_cached_blocks(tmp_path, "k1", blocks)
    assert pdf_run._load_cached_blocks(tmp_path, "k1") == blocks
    # 지문이 다르면 무효 — 추출 코드가 바뀐 런이 옛 결과를 쓰면 안 된다
    assert pdf_run._load_cached_blocks(tmp_path, "k2") is None


def test_extract_cache_key_tracks_logic_and_constants():
    """지문은 상수값과 추출 로직 **둘 다** 따라간다.

    한쪽만 보면 새는 구멍이 있다: 상수는 전역 이름으로 로드되므로 값만
    바꾸면 바이트코드가 그대로고, 로직만 바꾸면 상수 문자열이 그대로다.
    지문이 안 바뀌면 수정이 조용히 무시된다(동결본 혼선과 같은 계열)."""
    prof = xs.XsheetProfile()
    base = prof.extract_cache_key()
    assert base == prof.extract_cache_key()          # 결정적
    original = xs._CLUSTER_PAD
    try:
        xs._CLUSTER_PAD = original + 1.0
        assert prof.extract_cache_key() != base      # 상수 변경 포착
    finally:
        xs._CLUSTER_PAD = original
    assert prof.extract_cache_key() == base          # 원복되면 같은 지문


def test_blocks_cache_key_needs_the_profile_hook(tmp_path):
    """훅이 없는 프로파일(스토리보드)은 캐시를 쓰지 않는다 — 텍스트 레이어
    추출은 이미 빠르고, 지문 없이 캐시하면 무효화 수단이 사라진다."""
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    assert pdf_run._blocks_cache_key(object(), src) is None
    key = pdf_run._blocks_cache_key(xs.XsheetProfile(), src)
    assert key and key.startswith(xs.XsheetProfile().extract_cache_key())
    # 원본이 바뀌면(크기) 지문도 바뀐다
    src.write_bytes(b"%PDF-1.4\n%extra\n")
    assert pdf_run._blocks_cache_key(xs.XsheetProfile(), src) != key


def test_transcribe_aborts_on_quota_refusal(tmp_path, monkeypatch):
    """쿼터 소진(rc=0 + 평문 한 줄)은 쪼개기 재시도 대상이 아니라 즉시 중단.

    A1 전량 런 실측: agy가 "Individual quota reached..."를 rc=0으로 돌려주자
    파싱 실패로 취급돼 큐만 태우고 전사가 8%에서 조용히 멎었다."""
    blocks = [_note(0, 50, 100 + i * 20) for i in range(8)]
    _touch_crops(tmp_path, blocks)
    monkeypatch.setattr(ht, "_BATCH", 2)
    monkeypatch.setattr(ht, "_workers", lambda: 1)
    calls: list[int] = []

    class FakeCompleted:
        returncode = 0
        stdout = ("Error: Individual quota reached. Please upgrade your "
                  "subscription to increase your limits. Resets in 28m13s.")
        stderr = ""

    def _fake_run(argv, **kw):
        calls.append(1)
        return FakeCompleted()

    monkeypatch.setattr(ht.subprocess, "run", _fake_run)
    monkeypatch.setattr(ht, "resolve_cli", lambda name: "/usr/bin/fake", raising=False)
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_cli.resolve_cli",
        lambda name: "/usr/bin/fake")
    with pytest.raises(ht.TranscribeFatalError) as err:
        ht.transcribe(blocks, tmp_path)
    assert "거절" in str(err.value) and "quota" in str(err.value).lower()
    assert len(calls) == 1          # 첫 거절에서 멈춘다(재시도 폭주 없음)


def test_transcribe_fails_loudly_when_mostly_unanswered(tmp_path, monkeypatch):
    """응답률이 문턱 아래면 반쪽 결과를 done으로 흘리지 않는다."""
    blocks = [_note(0, 50, 100 + i * 20) for i in range(10)]
    _touch_crops(tmp_path, blocks)
    monkeypatch.setattr(ht, "_BATCH", 1)
    monkeypatch.setattr(ht, "_SPLIT_MIN", 99)   # 쪼개기 없이 바로 실패 집계
    monkeypatch.setattr(ht, "_workers", lambda: 1)
    names = [ht.crop_name(b) for b in blocks]

    def _fake(prompt, cwd, engine=None):
        n = next(x for x in names if x in prompt)
        if names.index(n) < 3:
            return json.dumps({n: "REAL NOTE"})
        raise RuntimeError("세션 죽음")

    monkeypatch.setattr(ht, "_run_cli", _fake)
    with pytest.raises(RuntimeError, match="대부분 실패"):
        ht.transcribe(blocks, tmp_path)
    # 캐시는 남아 재번역이 이어받는다
    cache = json.loads((tmp_path / ht._CACHE_NAME).read_text(encoding="utf-8"))
    assert len([k for k in cache if k != ht._CACHE_VERSION_KEY]) == 3


def test_extract_drops_junk_crops(monkeypatch):
    """잡티(작고 원시 OCR이 한 글자)는 블록으로 만들지 않는다 — 전사 세션
    낭비. 크지만 원시 OCR이 짧은 것, 작지만 글자가 있는 것은 살린다."""
    dpi = xs._OCR_DPI
    items = _header_row(dpi, [("ACTION", 100, 140), ("DIALOG", 370, 400),
                              ("EXP", 405, 425), ("CAMERA NOTES", 700, 780)])
    items += [
        (_box_px((100, 200, 108, 210), dpi), "M", 0.5),        # 잡티 8x10pt
        (_box_px((100, 300, 118, 312), dpi), "AD", 0.6),       # 작지만 2글자
        (_box_px((100, 400, 160, 430), dpi), "X", 0.5),        # 크다 60x30
    ]
    _install_engine(monkeypatch, FakeEngine(items))
    blocks = xs.XsheetProfile().extract(FakeDoc())
    assert sorted(b.text for b in blocks
                  if b.kind != xs.STRIP_KIND) == ["AD", "X"]


def test_argv_differs_per_cli():
    """`--print-timeout`은 agy 전용 — claude에 넘기면 인자 오류로 즉사한다."""
    agy = ht._argv_for("agy", "/bin/agy", "P")
    claude = ht._argv_for("claude", "/bin/claude", "P")
    assert agy[:5] == ["/bin/agy", "-p", "P", "--add-dir", "."]
    assert "--print-timeout" in agy
    assert claude[:5] == ["/bin/claude", "-p", "P", "--add-dir", "."]
    assert "--print-timeout" not in claude
    # 모델 고정(인터랙티브 /model 상속 차단) + 사고 깊이(A/B 실측 근거)
    assert claude[5:] == ["--model", "opus", "--effort", "medium"]
    assert ht._argv_for("codex", "/bin/codex", "P")[1] == "exec"


def test_extract_json_object_tolerates_cli_chatter():
    """CLI가 JSON 앞뒤에 말을 붙여도 객체만 떼어낸다(claude 실측: 펜스 뒤
    요약 문단을 덧붙여 'Extra data'로 파싱이 깨졌다)."""
    body = '{"a.png": "WALK\\nWEST.", "b.png": ""}'
    assert ht._extract_json_object(f"```json\n{body}\n```") == {
        "a.png": "WALK\nWEST.", "b.png": ""}
    chatty = f"Here you go:\n```json\n{body}\n```\n\nI transcribed 2 crops.\n"
    assert ht._extract_json_object(chatty)["a.png"] == "WALK\nWEST."
    # 값 안의 중괄호·이스케이프된 따옴표에 속지 않는다
    tricky = '{"a.png": "SEE {NOTE}", "b.png": "SAYS \\"HI\\""}'
    assert ht._extract_json_object(f"{tricky} trailing junk") == {
        "a.png": "SEE {NOTE}", "b.png": 'SAYS "HI"'}
    with pytest.raises(ValueError, match="찾지 못했"):
        ht._extract_json_object("no json here")


def _canvas(h=200, w=200):
    import numpy as np
    return np.full((h, w, 3), 255, dtype=np.uint8)


def test_expand_to_ink_includes_whole_stroke():
    """⛔손글씨는 잘리면 안 된다 — 상자에 걸친 획은 **덩어리째** 포함한다."""
    arr = _canvas(h=300, w=300)
    arr[140:200, 100:160] = 0        # 상자 아래로 삐져나온 획
    _x0, y0, _x1, y1 = ht._expand_to_ink(arr, 90, 100, 200, 150)
    assert y1 >= 200                 # 획 끝까지 내려간다
    assert y0 <= 100


def test_expand_to_ink_ignores_neighbour_and_ruled_lines():
    """옆 노트는 별개 덩어리라 겹치지 않으므로 끌어오지 않는다 — 변에 닿은
    잉크를 세던 옛 방식이 괘선 때문에 좌우 상한까지 부풀던 자리다."""
    arr = _canvas(h=400, w=400)
    arr[100:140, 100:160] = 0        # 내 노트
    arr[100:140, 300:360] = 0        # 멀리 떨어진 옆 노트
    arr[200:204, :] = 0              # 가로 인쇄 괘선
    arr[:, 250:254] = 0              # 세로 칸 구분선
    _x0, _y0, x1, y1 = ht._expand_to_ink(arr, 95, 95, 170, 145)
    assert x1 < 250                  # 옆 노트·구분선까지 삼키지 않는다
    assert y1 < 200                  # 괘선을 따라 늘어나지도 않는다


def _wide_word(h=900, w=900):
    """상한 밖까지 이어지는 **글자 덩어리**(화살표가 아니라).

    긴 사선으로 만들면 코드가 (의도대로) 화살표로 보고 무시한다 — 잘림
    판정을 시험하려면 짧고 촘촘한, 즉 글자로 인정되는 덩어리여야 한다."""
    arr = _canvas(h=h, w=w)
    arr[420:480, 450:670] = 0        # 상자에서 시작해 탐색 구역 끝을 넘김
    return arr


def test_ink_bounds_flags_clipping_at_the_cap():
    """상한 밖까지 이어지는 글자면 clipped=True로 알린다(조용히 자르지 않는다)."""
    _box, clipped = ht.ink_bounds(_wide_word(), 300, 400, 500, 500)
    assert clipped is True
    clean = _canvas(h=300, w=300)
    clean[120:160, 120:160] = 0
    _box2, clipped2 = ht.ink_bounds(clean, 100, 100, 200, 200)
    assert clipped2 is False


def test_ink_bounds_ignores_arrows():
    """프레임을 가리키는 긴 화살표는 글자가 아니다 — 따라가면 상자가 상한까지
    부푼다(실측 87%가 그렇게 터졌다)."""
    arr = _canvas(h=900, w=900)
    arr[420:460, 420:470] = 0                     # 내 글자
    for i in range(400):                          # 길게 뻗은 얇은 화살표
        arr[460 + i // 2, 470 + i] = 0
    _x0, _y0, x1, _y1 = ht._expand_to_ink(arr, 400, 400, 500, 500)
    assert x1 < 600                               # 화살표를 따라가지 않는다


def test_expand_to_ink_is_bounded():
    """글자 덩어리가 상한 밖까지 이어져도 상자는 상한에서 멈춘다."""
    _x0, _y0, x1, _y1 = ht._expand_to_ink(_wide_word(), 300, 400, 500, 500)
    assert x1 - 500 <= ht._MAX_GROW_PX


@pytest.mark.skipif(not SAMPLES, reason="실물 샘플 경로(YESON_PDF_SAMPLES) 미지정")
def test_real_samples_two_studios_geometry():
    """⛔작품마다 양식이 다르다 — 두 실물에서 칸 구조가 각자 맞게 유도돼야 한다.

    KOTH(792×1224, 대사 1쌍) / BM802(A3 841.92×1189.92, titmouse, 대사 5쌍).
    좌표를 박아 두면 BM802에서 DIAL4·5의 립싱크 음소가 통째로 번역 대상이
    된다(2026-08-20 실측으로 확인된 실패).
    """
    from apps.server.domain.pdf_translate.backend import open_pdf
    from apps.server.domain.pdf_translate.profiles import xsheet as xsm

    cases = [
        (Path(SAMPLES) / "script_trans" / "1401_XSHEETS_번역" / "KOTH_1401_A1.pdf",
         1, (375.0, 425.0)),          # 실측 367.6~432.4 (대사 1쌍)
        (Path(SAMPLES) / "xsheet_ocr_test" / "BM802_XSHEETS_SECTION_A_041224.pdf",
         0, (310.0, 590.0)),          # 실측 296.6~607.0 (대사 5쌍)
    ]
    for path, page, (lo_max, hi_min) in cases:
        if not path.exists():
            pytest.skip(f"샘플 없음: {path.name}")
        doc = open_pdf(path)
        try:
            pw, ph = doc.page_size(page)
            geom = xsm._derive_geometry(
                xsm._ocr_page(doc, page, xsm._OCR_DPI), pw, ph)
            assert geom is not None, f"{path.name}: 머리글 줄 미검출"
            assert geom.dialog_band is not None
            lo, hi = geom.dialog_band
            assert lo <= lo_max and hi >= hi_min, (path.name, lo, hi)
            assert 0 < geom.header_y < ph * 0.25
            assert geom.footer_y > ph * 0.8
            assert len(geom.num_bands) >= 1
        finally:
            doc.close()


def test_transcribe_aborts_on_permission_denial(tmp_path, monkeypatch):
    """헤드리스 권한 거부도 즉시 중단 대상 — 쪼개 재시도해도 같은 거부다.
    실측: agy는 신뢰 워크스페이스로 등록해도 headless read_file을 자동 거부한다."""
    blocks = [_note(0, 50, 100 + i * 20) for i in range(4)]
    _touch_crops(tmp_path, blocks)
    monkeypatch.setattr(ht, "_BATCH", 2)
    monkeypatch.setattr(ht, "_workers", lambda: 1)

    class Denied:
        returncode = 0
        stdout = ('Error: permission check failed for read_file "/x/a.png": '
                  "user denied permission for read_file(/x/a.png)")
        stderr = ""

    monkeypatch.setattr(ht.subprocess, "run", lambda argv, **kw: Denied())
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_cli.resolve_cli",
        lambda name: "/usr/bin/fake")
    with pytest.raises(ht.TranscribeFatalError) as err:
        ht.transcribe(blocks, tmp_path)
    assert "권한" in str(err.value)          # 조치 가능한 안내가 붙는다


def test_pick_cli_follows_selected_translation_engine(monkeypatch):
    """화면의 엔진 선택 하나가 번역과 전사 **둘 다**를 정해야 한다 —
    사용자가 클로드를 고르면 전사도 클로드다(전엔 전사만 agy로 가서
    '클로드 골랐는데 왜 agy 권한 오류냐'가 됐다)."""
    monkeypatch.delenv(ht.ENV_CLI, raising=False)
    assert ht._pick_cli("claude") == "claude"
    assert ht._pick_cli("agy") == "agy"
    # 이미지 입력이 안 되는 엔진이면 기본값으로 — 번역만 그 엔진이 맡는다
    assert ht._pick_cli("apple") == "agy"
    assert ht._pick_cli("qwen9b") == "agy"
    assert ht._pick_cli(None) == "agy"
    # ⛔gemini는 API라 전사에 쓰지 않는다(비용)
    assert ht._pick_cli("gemini") == "agy"
    # 운영 오버라이드가 최우선
    monkeypatch.setenv(ht.ENV_CLI, "codex")
    assert ht._pick_cli("claude") == "codex"


def test_transcribe_passes_engine_to_cli(tmp_path, monkeypatch):
    blocks = [_note(0, 50, 100)]
    _touch_crops(tmp_path, blocks)
    monkeypatch.delenv(ht.ENV_CLI, raising=False)
    seen: list[str] = []

    def _fake(argv, **kw):
        seen.append(argv[0])
        class R:
            returncode = 0
            # claude는 인라인 단일턴(stream-json 출력)으로 간다
            stdout = _stream_json(
                json.dumps({ht.crop_name(blocks[0]): "WALK WEST."}))
            stderr = ""
        return R()

    monkeypatch.setattr(ht.subprocess, "run", _fake)
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_cli.resolve_cli",
        lambda name: f"/usr/bin/{name}")
    out = ht.transcribe(blocks, tmp_path, engine="claude")
    assert out and seen == ["/usr/bin/claude"]


def _stream_json(result: str, *, is_error: bool = False) -> str:
    """`claude -p --output-format stream-json`의 출력 흉내 — result 이벤트 하나."""
    lines = [{"type": "system", "subtype": "init"},
             {"type": "result", "subtype": "success", "is_error": is_error,
              "result": result,
              "usage": {"input_tokens": 1, "cache_creation_input_tokens": 2,
                        "cache_read_input_tokens": 0, "output_tokens": 3},
              "total_cost_usd": 0.001}]
    return "\n".join(json.dumps(x) for x in lines) + "\n"


def _inline_env(monkeypatch, responses: list):
    """subprocess.run 가짜 — argv·stdin을 기록하고 responses를 차례로 돌려준다."""
    import subprocess as sp
    calls: list[tuple[list[str], dict]] = []

    def _fake(argv, **kw):
        calls.append((argv, kw))
        rc, out = responses.pop(0)
        return sp.CompletedProcess(argv, rc, stdout=out, stderr="")

    monkeypatch.setattr(ht.subprocess, "run", _fake)
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_cli.resolve_cli",
        lambda name: f"/usr/bin/{name}")
    monkeypatch.delenv(ht.ENV_CLI, raising=False)
    monkeypatch.delenv(ht.ENV_INLINE, raising=False)
    monkeypatch.delenv(ht.ENV_EXTRA_ARGS, raising=False)
    monkeypatch.setattr(ht, "_lean_ok", True)
    return calls


def test_inline_prompt_variants_keep_file_list():
    """두 프롬프트 변형 모두 "Files:" 목록으로 끝난다 — 테스트 seam(이름 in
    prompt)과 인라인 첨부(_FILE_RE)가 같은 계약에 기댄다."""
    names = ["p001_10_20.png", "2x_p002_30_40.png"]
    for inline in (False, True):
        assert ht._FILE_RE.findall(ht._build_prompt(names, inline=inline)) == names
        assert ht._FILE_RE.findall(
            ht._build_strip_prompt(names, inline=inline)) == names
    assert "file-reading tool" in ht._build_prompt(names)
    assert "file-reading tool" not in ht._build_prompt(names, inline=True)
    assert "shell commands" not in ht._build_strip_prompt(names, inline=True)


def test_claude_inline_sends_images_in_one_message(tmp_path, monkeypatch):
    """claude 전사 = 크롭을 base64 이미지 블록으로 첨부한 **단일턴** —
    도구 0(`--tools ""`)·설정 0(`--setting-sources ""`)·파일 읽기 경로 없음.
    실측: 도구 루프 대비 컨텍스트 5,009→269/크롭·출력 156→19(1603 120장)."""
    blocks = [_note(0, 50, 100), _note(0, 50, 200)]
    _touch_crops(tmp_path, blocks)
    names = [ht.crop_name(b) for b in blocks]
    crops = tmp_path / ht._CROPS_DIRNAME
    calls = _inline_env(monkeypatch, [
        (0, _stream_json(json.dumps({n: "WALK" for n in names})))])
    out = ht._run_cli(ht._build_prompt(names, inline=True), crops, "claude")
    assert ht._extract_json_object(out) == {n: "WALK" for n in names}
    argv, kw = calls[0]
    assert argv[0] == "/usr/bin/claude"
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    for flag in ("--tools", "--setting-sources", "--no-session-persistence",
                 "--system-prompt", "--model", "--effort"):
        assert flag in argv
    assert "--add-dir" not in argv
    msg = json.loads(kw["input"])
    content = msg["message"]["content"]
    images = [c for c in content if c["type"] == "image"]
    assert len(images) == 2
    assert all(c["source"]["media_type"] == "image/png" for c in images)
    labels = [c["text"] for c in content if c["type"] == "text"]
    assert labels[0].startswith("The attached images")
    assert [f"filename: {n}" for n in names] == labels[1:]


def test_transcribe_picks_prompt_variant_by_engine(tmp_path, monkeypatch):
    """transcribe 루프가 엔진에 맞는 프롬프트를 고른다 — claude는 첨부형,
    agy(기본)는 파일 읽기형. 환경변수 0이면 claude도 옛 루프로."""
    blocks = [_note(0, 50, 100)]
    _touch_crops(tmp_path, blocks)
    monkeypatch.delenv(ht.ENV_CLI, raising=False)
    monkeypatch.delenv(ht.ENV_INLINE, raising=False)
    prompts: list[str] = []

    def _fake(prompt, cwd, engine=None):
        prompts.append(prompt)
        return json.dumps({ht.crop_name(blocks[0]): "WALK"})

    monkeypatch.setattr(ht, "_run_cli", _fake)
    cache = tmp_path / ht._CACHE_NAME          # 캐시가 적중하면 CLI를 안 부른다
    ht.transcribe(blocks, tmp_path, engine="claude")
    cache.unlink()
    ht.transcribe(blocks, tmp_path, engine=None)
    cache.unlink()
    monkeypatch.setenv(ht.ENV_INLINE, "0")
    ht.transcribe(blocks, tmp_path, engine="claude")
    assert "attached images" in prompts[0]
    assert "file-reading tool" in prompts[1]
    assert "file-reading tool" in prompts[2]


def test_claude_inline_disabled_by_env_uses_tool_loop(tmp_path, monkeypatch):
    blocks = [_note(0, 50, 100)]
    _touch_crops(tmp_path, blocks)
    names = [ht.crop_name(b) for b in blocks]
    crops = tmp_path / ht._CROPS_DIRNAME
    calls = _inline_env(monkeypatch, [(0, json.dumps({names[0]: "WALK"}))])
    monkeypatch.setenv(ht.ENV_INLINE, "0")
    out = ht._run_cli(ht._build_prompt(names), crops, "claude")
    assert ht._extract_json_object(out) == {names[0]: "WALK"}
    argv, kw = calls[0]
    assert "--add-dir" in argv and "--input-format" not in argv
    assert kw.get("input") is None


def test_claude_inline_retries_without_lean_flags_when_not_logged_in(
        tmp_path, monkeypatch):
    """`--setting-sources ""`가 설정 기반 인증까지 끊는 환경 — 미로그인 응답이면
    설정을 적재하는 모드로 한 번 재시도하고 이후 세션도 그 모드로 간다."""
    blocks = [_note(0, 50, 100)]
    _touch_crops(tmp_path, blocks)
    names = [ht.crop_name(b) for b in blocks]
    crops = tmp_path / ht._CROPS_DIRNAME
    calls = _inline_env(monkeypatch, [
        (1, _stream_json("Not logged in · Please run /login", is_error=True)),
        (0, _stream_json(json.dumps({names[0]: "WALK"})))])
    out = ht._run_cli(ht._build_prompt(names, inline=True), crops, "claude")
    assert ht._extract_json_object(out) == {names[0]: "WALK"}
    assert len(calls) == 2
    assert "--setting-sources" in calls[0][0]
    assert "--setting-sources" not in calls[1][0]
    assert "--input-format" in calls[1][0]           # 여전히 인라인 단일턴
    assert ht._lean_ok is False


def test_claude_inline_quota_refusal_is_fatal(tmp_path, monkeypatch):
    blocks = [_note(0, 50, 100)]
    _touch_crops(tmp_path, blocks)
    names = [ht.crop_name(b) for b in blocks]
    crops = tmp_path / ht._CROPS_DIRNAME
    _inline_env(monkeypatch, [
        (1, _stream_json("Error: quota reached. Resets in 2h", is_error=True))])
    with pytest.raises(ht.TranscribeFatalError, match="quota"):
        ht._run_cli(ht._build_prompt(names, inline=True), crops, "claude")


def test_claude_inline_skips_missing_files(tmp_path, monkeypatch):
    """없는 크롭은 첨부에서 빠지고 응답에도 없다 — 호출자가 미응답으로 센다."""
    blocks = [_note(0, 50, 100), _note(0, 50, 200)]
    _touch_crops(tmp_path, [blocks[0]])
    names = [ht.crop_name(b) for b in blocks]
    crops = tmp_path / ht._CROPS_DIRNAME
    calls = _inline_env(monkeypatch, [
        (0, _stream_json(json.dumps({names[0]: "WALK"})))])
    ht._run_cli(ht._build_prompt(names, inline=True), crops, "claude")
    content = json.loads(calls[0][1]["input"])["message"]["content"]
    assert sum(1 for c in content if c["type"] == "image") == 1


def _ko_note(src: str) -> PdfBlock:
    return PdfBlock(page=0, kind=xs.NOTE_KIND, text=src,
                    bbox=(0.0, 0.0, 10.0, 10.0), limit_x1=None)


@pytest.mark.parametrize(("src", "ko", "want"), [
    # ⚠want의 줄바꿈: 합계 12자 이하 다중줄은 refine_ko가 **한 줄로 접는다**
    # (2026-08-31, 1603 사람 납품본 2,868건이 전부 1줄 — 근거는 refine_ko 주석)
    # 무조건 규칙 — KOTH_1401_A2 사람 납품본 전수 대조(2026-08-21)
    ("BLINK", "눈 깜빡", "눈깜박"),
    ("BLINK", "깜빡임", "눈깜박"),
    ("BLINK", "눈을 깜빡인다", "눈깜박"),
    ("DIAL", "대사", "대화"),
    ("DIAL", "다이얼", "대화"),          # DIAL=dialogue를 dial로 읽은 오역
    ("TURNS", "바비가 돈다", "바비가 턴한다"),
    ("STEPS", "뒤로 걸음", "뒤로 스텝"),
    ("GESTURE", "제스처로", "제스쳐로"),
    ("OVERSHOOT", "오버슈트", "오버슛"),
    ("HEAD", "머리", "고개"),
    # 원문 조건부 — 같은 한국어라도 원문이 무엇이냐로 사람 표기가 갈린다
    ("HEAD TILT", "머리 기울임", "고개 기웃"),
    ("TILT", "틸트", "기웃"),            # 음역 오역
    ("LEANS", "기댄다", "기울인다"),
    ("LEAN", "기댐", "기울인다"),
    ("STL", "정지", "안착"),
    ("STL & SETTLE", "스틸", "안착"),    # STL=settle을 still로 읽은 오역
    # 2026-08-25 품질 전수 대조 추가분(사람 1,942쌍)
    ("ANTIC", "앤티시페이션 아래로", "준비동작 아래로"),
    ("STEP", "발스텝", "스텝"),
    ("ACCENT", "악센트", "액센트"),
    ("BOOM UP", "붐하우어 위로", "붐 위로"),      # 원문 약칭을 풀네임으로 부풀림
    ("STOP", "정지", "멈춤"),
    ("PILLOW W/W ACTION", "베개를 따라 움직임", "베개 액션맞춰 움직임"),
    ("BLANKETS W/W", "담요와 함께 움직임 & 안착", "담요 액션맞춰 움직임 & 안착"),
    # ON n'S(n콤마) — 사용자 실물 지적(2026-08-25): 음역 `온 원스` 금지
    ("ACTION\nON 1S", "액션\n온 원스", "액션 1콤마에"),
    ("SHRUG\nGESTURE\nONS", "제스쳐로\n온 원스", "제스쳐로 1콤마에"),  # 숫자 없는 약칭
    # STLS(복수형)도 settle — 기존 `[&+]\s*세틀` 규칙이 접속사째 걷는다
    ("HAIR\nO'LAP\n& STLS", "머리카락\n오버랩\n& 세틀", "머리카락 오버랩 안착"),
    # 잉여 음역 `발스텝`은 붙어 쓴 단일 토큰일 때만 걷는다
    ("0X\nSTEP", "0X 발스텝", "0X 스텝"),
    # LLM 겹말 `발 + 발스텝`은 뒤엣것만 줄인다(FT의 `발`은 남는다)
    ("RT\nFT\nSTEP", "오른\n발\n발스텝", "오른발 스텝"),     # 사람 두 문서 모두 붙여 씀(왼발 28·왼 발 0)
    ("ACTION\nON (1)S", "액션 온 원스", "액션 1콤마에"),
    # ⛔원문이 FT(foot)면 `발`은 정당한 낱말 — 지우면 내용이 사라진다.
    # 실측 사고(2026-08-25): 무조건 `발\\s*스텝` 규칙이 A2 계획 3건에서
    # `오른|발|스텝`을 `오른|스텝`으로 깎았다. 접혀도 `발`은 남아야 한다.
    ("HANK\nLT\nFT\nSTEP", "행크\n왼\n발\n스텝", "행크 왼발 스텝"),
    ("RT\nFT\nSTEP", "오른\n발\n스텝", "오른발 스텝"),
    ("RT\nFT\nSTEP", "오른발\n스텝", "오른발 스텝"),
    ("WHEELS\nSPIN\nON\n2'S", "바퀴\n회전\n온\n투스", "바퀴 회전 2콤마에"),
])
def test_refine_ko_applies_house_terms(src, ko, want):
    assert xs.XsheetProfile().refine_ko(_ko_note(src), ko) == want


@pytest.mark.parametrize(("src", "ko"), [
    # `머리카락`은 사람·우리 4건씩 일치하는 정상 낱말 — `고개카락`이 되면 안 된다
    ("HAIR O/LAP", "머리카락 오버랩"),
    ("WALK", "걸음걸이"),                # `스텝걸이`가 되면 안 된다
    # STL 단어 경계: 부분 문자열로 찾으면 이것들이 settle 노트로 오인된다
    ("HUSTLE", "정지"),
    ("CASTLE", "스틸"),
    # 원문이 TILT/LEAN이 아니면 건드리지 않는다
    ("BOBBY UP", "기울임"),
    ("LEANS", "기울임"),
    # 원문 조건 밖 — BOOM·STOP·W/W가 없으면 그대로 둔다
    ("BOOMHAUER", "붐하우어"),           # 원문 자체가 풀네임이면 존중
    ("FREEZE", "정지"),
    ("PILLOW ACTION", "베개를 따라 움직임"),
    # 원문에 ON n'S가 없으면 음역이라도 건드리지 않는다 (ON HIS ≠ ON 1'S)
    ("HANDS ON HIS HIPS", "온 원스"),
    # 붙여 쓴 `오른발스텝`을 줄이면 `오른스텝`이 되어 발이 사라진다 — 잠금
    ("RT\nFT\nSTEP", "오른발스텝"),
])
def test_refine_ko_leaves_unrelated_text(src, ko):
    assert xs.XsheetProfile().refine_ko(_ko_note(src), ko) == ko


def _ink_png(w: int, h: int, bands: list[tuple[int, int, int, int]]) -> bytes:
    """흰 바탕 + 지정한 사각형만 검게 칠한 PNG(손글씨 자리 흉내)."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in bands:
        d.rectangle([x0, y0, x1, y1], fill="black")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _place_note(bbox, ko, limit_x1=None):
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="SRC", bbox=bbox,
                 limit_x1=limit_x1)
    return xs.XsheetProfile().place(b, ko, (792.0, 1224.0))


def test_place_fits_box_to_text_not_to_available_space():
    """상자는 남은 자리가 아니라 글에 맞춘다.

    A2 전수에서 옛 코드는 가용 폭을 통째로 먹어 폭 중앙값이 104.8pt였다
    (사람 30pt). 넓은 상자에 글이 왼쪽 정렬로 앉으면서 번역문이 원문에서
    떨어지거나 옆 노트 위로 올라탔다."""
    ov = _place_note((150.0, 360.0, 190.0, 400.0), "SO\nSMU\n남자",
                     limit_x1=560.0)
    width = ov.rect[2] - ov.rect[0]
    assert width < 45.0, width           # 가용 폭은 370pt였다
    assert ov.rect[0] == pytest.approx(193.0)   # 원문 오른쪽에 붙는다


def test_place_left_anchors_to_the_note_not_the_page_edge():
    """왼쪽 배치는 원문에 붙인다 — 페이지 좌단(8.0) 고정이 아니다.

    옛 코드는 상자를 늘 x=8.0에서 시작해, 원문이 오른쪽에 있을수록 번역문이
    멀어졌다(전수 45.4%가 페이지 가장자리, 사람은 0.1%)."""
    # 오른쪽이 칸 경계로 막혀 왼쪽으로 밀리는 노트
    ov = _place_note((300.0, 700.0, 350.0, 740.0), "고개\n기웃", limit_x1=352.0)
    assert ov.rect[2] == pytest.approx(297.0)   # 원문 시작 바로 왼쪽
    assert ov.rect[0] > 100.0                   # 페이지 끝에 붙지 않았다


def test_place_vertical_ladder_is_absolute_not_note_height():
    """세로 탐색은 절대값이다 — 노트 높이에 비례시키면 긴 노트에서 번역문이
    통째로 떨어져 나간다(개발 중 실측: y362 노트의 번역이 y530으로)."""
    tall = (150.0, 360.0, 190.0, 690.0)      # 높이 330pt짜리 긴 노트
    rects = [r for r, _fs, _d in xs.XsheetProfile()._candidates(
        PdfBlock(page=0, kind=xs.NOTE_KIND, text="S", bbox=tall,
                 limit_x1=560.0), "고개\n기웃", (792.0, 1224.0))]
    # 노트 **상자**와의 세로 간격으로 잰다 — 마지막 후보(아래 배치)는 노트
    # 하단에 붙으므로 상단 기준으로 재면 노트 높이만큼이 그대로 편차로 잡힌다.
    gaps = [max(tall[1] - r[3], r[1] - tall[3], 0.0) for r in rects]
    assert max(gaps) <= 52.0, max(gaps)      # 사람 9분위(53.7pt) 안


def test_place_with_doc_moves_off_the_handwriting():
    """빈 자리가 있으면 손글씨를 피해 앉는다."""
    # 100dpi 기준: 원문 오른쪽(x193~240pt ≈ 268~333px)에 잉크를 깔아 둔다
    png = _ink_png(1100, 1700, [(260, 480, 345, 560)])
    doc = FakeDoc(png=png)
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S",
                 bbox=(150.0, 360.0, 190.0, 400.0), limit_x1=560.0)
    prof = xs.XsheetProfile()
    plain = prof.place(b, "고개\n기웃", (792.0, 1224.0))
    avoided = prof.place_with_doc(b, "고개\n기웃", (792.0, 1224.0), doc)
    assert xs._ink_ratio(prof._page_ink(doc, 0), plain.rect, 1224.0) > xs._INK_OK
    assert xs._ink_ratio(prof._page_ink(doc, 0), avoided.rect, 1224.0) <= xs._INK_OK


def test_place_with_doc_avoids_existing_annotations():
    """이미 놓인 주석 자리도 점유 공간으로 피한다 — A2 실측(2026-08-25):
    잉크만 피하던 배치가 이웃 블록과 같은 빈자리를 골라 **주석끼리 심한
    겹침(30%+) 91쌍**(사람 0쌍, p4 `페기 턴`+`돌아서 향한다` 포개짐 실물).
    사람은 잉크엔 겹쳐 써도 주석끼리는 절대 안 겹친다."""
    png = _ink_png(1100, 1700, [])           # 빈 페이지 — 잉크 제약 없음
    doc = FakeDoc(png=png)
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S",
                 bbox=(150.0, 360.0, 190.0, 400.0), limit_x1=560.0)
    prof = xs.XsheetProfile()
    ov1 = prof.place_with_doc(b, "고개\n기웃", (792.0, 1224.0), doc)
    ov2 = prof.place_with_doc(b, "페기\n턴한다", (792.0, 1224.0), doc,
                              occupied=(ov1.rect,))
    a, c = ov1.rect, ov2.rect
    ix = min(a[2], c[2]) - max(a[0], c[0])
    iy = min(a[3], c[3]) - max(a[1], c[1])
    inter = max(ix, 0) * max(iy, 0)
    smaller = min((a[2]-a[0])*(a[3]-a[1]), (c[2]-c[0])*(c[3]-c[1]))
    assert inter / smaller <= 0.05, (a, c)   # 심한 겹침 금지


def test_place_with_doc_stays_beside_instead_of_fleeing():
    """원문 곁을 지킨다 — 사다리 끝으로 도망가지 않는다.

    ⚠2026-08-26 새 설계(좌우 1순위 인접)에 맞춰 기대를 고쳤다. 옛 기대는
    "오른쪽 dy0에 x=193"이었는데, 그건 앵커가 **느슨한 OCR 클러스터 상자**
    이던 시절의 좌표다. 지금은 **잉크에 타이트한 사각형**을 앵커로 쓰므로
    좌표가 달라진다. 지키려는 성질(곁에 남는다)은 그대로다 — 옛 코드는
    "잉크 2% 이하인 첫 후보"라 곁이 조금만 지저분해도 -52pt로 도망갔다."""
    png = _ink_png(1100, 1700, [
        (208, 500, 264, 556),          # 원문 자체의 잉크(pt 150~190 × 360~400)
        (300, 500, 360, 556),          # 오른쪽 곁에 옅지 않은 덩어리
    ])
    doc = FakeDoc(png=png)
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S",
                 bbox=(150.0, 360.0, 190.0, 400.0), limit_x1=560.0)
    ov = xs.XsheetProfile().place_with_doc(b, "고개\n기웃", (792.0, 1224.0), doc)
    # ⚠2026-08-27: 오른쪽이 막힌 이 픽스처의 정답이 **왼쪽에서 아래로** 바뀌었다
    # (사람 실측 왼쪽 8% 대 아래 26%). 이 테스트가 지키는 성질은 방향이 아니라
    # "곁에 남는다"이므로, 어느 변이든 **인접**만 확인한다.
    gap_x = max(150.0 - ov.rect[2], ov.rect[0] - 190.0, 0.0)
    gap_y = max(360.0 - ov.rect[3], ov.rect[1] - 400.0, 0.0)
    assert (gap_x ** 2 + gap_y ** 2) ** 0.5 <= 20.0, "사다리 끝으로 도망갔다"
    assert ov.fontsize == xs._FONTSIZE


def test_place_with_doc_below_is_first_class():
    """아래 배치는 최후 예비가 아니라 정식 후보다 — 양옆이 막히면 제
    크기(9pt)로 원문 바로 아래에 앉는다(전수 감사: 사람 below 13% 대
    우리 5%, 옛 코드는 아래를 최소 폰트 한 자리만 만들었다)."""
    # ⚠픽스처를 새 설계에 맞게 고쳤다(2026-08-26): 앵커가 **잉크에 타이트한
    # 사각형**이라 원문 자리에 잉크가 없으면 앵커가 방해물 가장자리로 붙어
    # 엉뚱한 자리를 잰다. 원문 자신의 잉크를 그리고, 좌우를 넉넉히 막는다.
    png = _ink_png(1100, 1700, [
        (208, 500, 264, 556),          # 원문 잉크(pt 150~190 × 360~400)
        (270, 420, 520, 640),          # 오른쪽 막힘
        (20, 420, 200, 640),           # 왼쪽 막힘 (아래는 비워 둔다)
    ])
    doc = FakeDoc(png=png)
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S",
                 bbox=(150.0, 360.0, 190.0, 400.0), limit_x1=560.0)
    ov = xs.XsheetProfile().place_with_doc(b, "고개\n기웃", (792.0, 1224.0), doc)
    assert ov.rect[1] > 400.0                    # 원문 아래로 내려간다
    assert ov.rect[1] - 400.0 <= 20.0            # 그래도 바로 아래(멀리 X)
    assert ov.fontsize == xs._FONTSIZE           # 7pt 축소 없이


def test_place_with_doc_tall_stack_prefers_side_over_below():
    """긴 세로 스택은 아래보다 옆이 낫다 — 실물 지적(2026-08-25, p5 SMU
    9줄 노트): 옆의 작화 웨이브 선(옅은 잉크) 탓에 번역이 스택 아래로
    밀려 읽기 시작점에서 150pt 떨어졌다. 사람은 선 위에 겹쳐 왼쪽에
    병기한다. 아래 변위는 블록 상단 기준(틈 + 높이 절반)이라 긴 노트
    에서 자연히 밀린다."""
    # 왼쪽 후보 지대(x≈357~412px)에 세로 작화선 흉내 2줄(~4% 잉크)
    png = _ink_png(1100, 1700, [
        (380, 600, 380, 820), (395, 600, 395, 820),
    ])
    doc = FakeDoc(png=png)
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S",
                 bbox=(300.0, 500.0, 340.0, 650.0), limit_x1=352.0)  # 우측 막힘
    ov = xs.XsheetProfile().place_with_doc(
        b, "SMU\n학생들이\n걷는다", (792.0, 1224.0), doc)
    assert ov.rect[2] == pytest.approx(297.0)    # 원문 왼쪽에 병기
    assert ov.rect[1] == pytest.approx(500.0)    # 스택 상단 높이 그대로
    assert ov.fontsize == xs._FONTSIZE
    # 이 예외를 지탱하는 상수 — 이보다 높은 노트에서만 왼쪽이 아래보다 앞선다
    assert b.bbox[3] - b.bbox[1] > xs._TALL_H


def test_place_with_doc_escapes_when_every_near_slot_is_taken():
    """이웃 주석이 곁을 다 메우면 **멀리라도** 겹치지 않는 자리로 간다.

    실측 계기(A3 116p): 과병합으로 생긴 거대 주석(높이 557pt)이 칸을 메운
    페이지에서 작은 주석의 후보가 전멸해, 폴백이 그 안에 앉으며 심한 겹침
    4쌍이 났다(A2 0쌍). 넓은 사다리는 **막혔을 때만** 켜진다."""
    png = _ink_png(1100, 1700, [])           # 잉크 제약 없음
    doc = FakeDoc(png=png)
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S",
                 bbox=(300.0, 500.0, 340.0, 520.0), limit_x1=560.0)
    # 원문 주변(좌·우·아래 ±52pt)을 통째로 덮는 이웃 주석 하나
    wall = (200.0, 400.0, 460.0, 640.0)
    ov = xs.XsheetProfile().place_with_doc(b, "행크", (792.0, 1224.0), doc,
                                           occupied=(wall,))
    assert xs._occupied_frac(ov.rect, (wall,)) <= xs._OCC_OK, ov.rect
    # 그래도 원문에서 멀리 도망가지는 않는다(넓은 사다리 상한 안)
    assert abs(ov.rect[1] - 500.0) <= 260.0, ov.rect


def test_place_with_doc_falls_back_when_render_fails(monkeypatch):
    """페이지 그림을 못 얻으면 기본 배치로 — 주석을 잃지 않는다."""
    class Broken(FakeDoc):
        def render_png(self, page, *, dpi=120, annots=True):
            raise RuntimeError("render 실패")
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S",
                 bbox=(150.0, 360.0, 190.0, 400.0), limit_x1=560.0)
    prof = xs.XsheetProfile()
    ov = prof.place_with_doc(b, "고개\n기웃", (792.0, 1224.0), Broken())
    assert ov.rect == prof.place(b, "고개\n기웃", (792.0, 1224.0)).rect


# ── 크롭 경계 절단 회수 ────────────────────────────────────────────
# 탐지가 놓친 줄은 어느 크롭에도 안 들어가 통째로 사라지고, 번역기가 잘린
# 원문을 그럴듯하게 완성해 오역까지 만든다. 전량 실측(A3 116p·A2 135p)에서
# 크롭의 23~31%가 코앞에 주인 없는 손글씨를 두고 있었다.

def _cut_geom(header_y=20.0, footer_y=1200.0):
    return xs._Geometry(header_y=header_y, footer_y=footer_y,
                                dialog_band=None, num_bands=(),
                                col_edges=(700.0,))


def _cut_doc(arr):
    """이 배열을 300dpi 렌더로 돌려주는 문서."""
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return FakeDoc(png=buf.getvalue())


def _cut_block(bbox):
    return PdfBlock(page=0, kind=xs.NOTE_KIND, text="X", bbox=bbox)


def _word(arr, x_pt, y_pt, w_pt, h_pt):
    """pt 좌표에 글자 덩어리 하나(300dpi 픽셀로 환산해 칠한다)."""
    s = 300 / 72.0
    arr[int(y_pt * s):int((y_pt + h_pt) * s),
        int(x_pt * s):int((x_pt + w_pt) * s)] = 0


def test_absorb_cut_ink_recovers_the_line_below():
    """`ANIM PANTS` 아래 `W/W ACTION`처럼, 주인 없는 아랫줄을 되찾는다."""
    arr = _canvas(h=1600, w=1600)
    _word(arr, 100, 100, 40, 12)              # 내 노트
    _word(arr, 100, 118, 40, 12)              # 잘려나간 아랫줄(틈 6pt)
    blocks = [_cut_block((100.0, 100.0, 140.0, 112.0))]
    out = xs._absorb_cut_ink(_cut_doc(arr), 0, blocks, _cut_geom())
    assert out[0].bbox[3] >= 129.0, "아랫줄까지 상자가 내려와야 한다"
    assert out[0].bbox[1] <= 100.0, "위쪽은 그대로"


def test_absorb_cut_ink_leaves_ink_that_has_an_owner():
    """옆 노트의 크롭이 이미 물고 있는 잉크는 가져오지 않는다 — 이게 없으면
    `_expand_to_ink`가 옆 노트를 안 무는 설계가 무너진다."""
    arr = _canvas(h=1600, w=1600)
    _word(arr, 100, 100, 40, 12)
    _word(arr, 100, 118, 40, 12)              # 아래 줄 = 별개 블록의 것
    blocks = [_cut_block((100.0, 100.0, 140.0, 112.0)),
              _cut_block((100.0, 118.0, 140.0, 130.0))]
    out = xs._absorb_cut_ink(_cut_doc(arr), 0, blocks, _cut_geom())
    assert out[0].bbox == blocks[0].bbox
    assert out[1].bbox == blocks[1].bbox


def test_absorb_cut_ink_refuses_to_swallow_a_neighbour():
    """고아는 이웃을 안 물어도 **합집합은 사각형**이라 이웃을 덮을 수 있다
    (A3 p065 실측: 거대 블록이 이웃 3개를 새로 물었다). 그러면 기각한다."""
    arr = _canvas(h=1600, w=1600)
    _word(arr, 100, 100, 40, 12)
    _word(arr, 220, 118, 40, 12)              # 오른쪽 아래 주인 없는 줄
    _word(arr, 170, 118, 30, 12)              # 그 사이에 있는 이웃 노트
    blocks = [_cut_block((100.0, 100.0, 140.0, 112.0)),
              _cut_block((170.0, 118.0, 200.0, 130.0))]
    out = xs._absorb_cut_ink(_cut_doc(arr), 0, blocks, _cut_geom())
    assert out[0].bbox == blocks[0].bbox, "이웃을 덮는 확장은 버린다"


def test_absorb_cut_ink_ignores_far_and_tiny_ink():
    """멀거나(>15pt) 낱말이 못 되는(<20pt) 잉크는 남의 것·마커로 본다."""
    arr = _canvas(h=1600, w=1600)
    _word(arr, 100, 100, 40, 12)
    _word(arr, 100, 150, 40, 12)              # 틈 38pt — 남의 노트
    far = xs._absorb_cut_ink(_cut_doc(arr), 0,
                                     [_cut_block((100.0, 100.0, 140.0, 112.0))],
                                     _cut_geom())
    assert far[0].bbox[3] < 129.0

    arr2 = _canvas(h=1600, w=1600)
    _word(arr2, 100, 100, 40, 12)
    _word(arr2, 100, 118, 10, 12)             # 동그라미 마커 크기
    tiny = xs._absorb_cut_ink(_cut_doc(arr2), 0,
                                      [_cut_block((100.0, 100.0, 140.0, 112.0))],
                                      _cut_geom())
    assert tiny[0].bbox[3] < 129.0


def test_absorb_cut_ink_stays_inside_the_note_area():
    """머리글 위 잉크는 노트가 아니다(양식 활자·로고)."""
    arr = _canvas(h=1600, w=1600)
    _word(arr, 100, 100, 40, 12)
    _word(arr, 100, 82, 40, 12)               # 머리글 위
    out = xs._absorb_cut_ink(
        _cut_doc(arr), 0, [_cut_block((100.0, 100.0, 140.0, 112.0))],
        _cut_geom(header_y=98.0))
    assert out[0].bbox[1] >= 100.0


def test_cut_recovery_render_dpi_matches_the_transcriber():
    """크롭 사각형 판정은 전사가 굽는 것과 **같은 해상도**여야 한다."""
    assert xs._CROP_DPI_CUT == ht._CROP_DPI


def test_extract_cache_key_covers_cut_recovery(monkeypatch):
    """⚠지문이 계약이다 — 경계 회수 상수가 바뀌면 추출 캐시가 무효화돼야
    한다. 안 그러면 수정이 조용히 무시된다."""
    profile = xs.XsheetProfile()
    before = profile.extract_cache_key()
    monkeypatch.setattr(xs, "_CUT_MAX_GAP_PT",
                        xs._CUT_MAX_GAP_PT + 1.0)
    assert profile.extract_cache_key() != before


# ── 화면 방향(EAST/WEST/NORTH/SOUTH) ────────────────────────────────
# 시트의 방위어는 나침반이 아니라 화면 방향이다(2026-08-26 사용자 지적).
# A1 실측: 항목의 14.6%(512/3,496)가 `동쪽`·`서쪽`으로 오역돼 있었다.

def _refine(src: str, ko: str) -> str:
    return xs.XsheetProfile().refine_ko(
        PdfBlock(page=0, kind=xs.NOTE_KIND, text=src, bbox=(0, 0, 10, 10)), ko)


def test_screen_directions_are_not_compass_points():
    """`HEAD EAST`는 고개를 **오른쪽으로**이지 동쪽이 아니다."""
    assert _refine("133 TILTS HEAD EAST",
                   "133 고개를\n동쪽으로 기울인다") == "133 고개를\n오른쪽으로 기울인다"
    assert _refine("PEGGY TURNS HEAD WEST", "고개를 서쪽으로") == "고개를 왼쪽으로"


def test_screen_direction_keeps_every_particle_form():
    """`동/서`는 `오른쪽·왼쪽`도 `쪽`으로 끝나 어간만 갈면 조사가 살아난다."""
    for ko, want in (("동쪽에서", "오른쪽에서"), ("서쪽을", "왼쪽을"),
                     ("동쪽의", "오른쪽의"), ("서쪽과", "왼쪽과")):
        assert _refine("LOOKS EAST WEST", ko) == want


def test_north_south_fix_the_particle_too():
    """`북쪽으로`를 어간만 갈면 `위으로`가 된다 — 조사형을 먼저 처리한다."""
    assert _refine("PAN NORTH", "북쪽으로") == "위로"
    assert _refine("MOVE SOUTH", "남쪽으로") == "아래로"
    assert _refine("NORTH", "북쪽을") == "위를"
    assert _refine("SOUTH", "남쪽에서") == "아래에서"


def test_screen_direction_catches_abbreviations():
    """원문이 `W.` 약칭이어도 잡아야 한다 — A1에서 실제로 2건 있었다.
    (원문 조건부였다면 놓쳤을 것들이라 이 규칙은 무조건이다.)"""
    assert _refine("W.\nSHIFT\nHEAD", "서쪽.\n이동") == "왼쪽. 이동"
    assert _refine("OF PAN\nFRAME.\nOFF WE", "서쪽으로 벗어나") == "왼쪽으로 벗어나"


def test_transcribe_argv_uses_medium_effort_for_claude():
    """Claude Code 기본 effort는 xhigh라 손글씨 판독엔 과하다 — A/B 실측으로
    medium이 출력 −56%·시간 −55%·빈값 −6·일치 ±0(모든 축 지배)."""
    argv = ht._argv_for("claude", "/bin/claude", "PROMPT")
    assert argv[argv.index("--effort") + 1] == "medium"


def test_effort_flag_is_claude_only():
    """agy엔 `--effort`가 없다 — 넘기면 인자 오류로 즉사한다."""
    assert "--effort" not in ht._argv_for("agy", "/bin/agy", "PROMPT")


def test_transcribe_argv_pins_model_for_claude(monkeypatch):
    """모델을 고정하지 않으면 헤드리스 claude가 사용자의 인터랙티브 /model
    기본값을 상속한다 — 대화 세션의 모델 변경이 파이프라인 단가를 조용히
    바꾼다(실측 기준은 전부 opus 등급). env는 운영 오버라이드."""
    argv = ht._argv_for("claude", "/bin/claude", "PROMPT")
    assert argv[argv.index("--model") + 1] == "opus"
    monkeypatch.setenv(ht.ENV_MODEL, "sonnet")
    argv = ht._argv_for("claude", "/bin/claude", "PROMPT")
    assert argv[argv.index("--model") + 1] == "sonnet"
    monkeypatch.setenv(ht.ENV_MODEL, "   ")     # 빈 값은 기본값으로
    assert ht._model() == "opus"
    assert "--model" not in ht._argv_for("agy", "/bin/agy", "PROMPT")


def test_effort_env_override_ignores_typos(monkeypatch):
    """오타 하나로 문서당 3시간짜리 잡이 죽으면 안 된다 — 기본값으로 수렴."""
    monkeypatch.setenv(ht.ENV_EFFORT, "low")
    assert ht._effort() == "low"
    monkeypatch.setenv(ht.ENV_EFFORT, "lo")
    assert ht._effort() == "medium"


def test_settle_spelled_out_is_also_안착():
    """원문이 철자 그대로 `SETTLE`이어도 사람은 `안착`을 쓴다 — A1 사람
    납품본 실측: 안착 167 · 세틀 0. 우리 산출물엔 `세틀`이 50건 남았었다."""
    assert _refine("EYEBROWS SETTLE.", "눈썹 세틀.") == "눈썹 안착."
    assert _refine("HEAD SETTLES", "고개 스틸") == "고개 안착"
    # 약칭 경로는 그대로 살아 있어야 한다(`& 세틀`은 기존 규칙이 `&`째 흡수)
    assert _refine("& STLS", "& 세틀") == "안착"
    # ⚠단어 경계 계약: HUSTLE·CASTLE이 STL 노트로 오인되면 안 된다
    assert _refine("HUSTLE", "정지") == "정지"


def test_left_placement_pays_for_its_horizontal_distance():
    """왼쪽 상자는 폭만큼 더 멀어지는데 그 거리가 변위에 없었다 — 그래서
    잉크 없는 여백(프레임 번호 칸)이 늘 이겨 주석이 왼쪽으로 몰렸다
    (A2 실측: 우리 왼쪽 34% 대 사람 10%)."""
    p = xs.XsheetProfile()
    blk = PdfBlock(page=0, kind=xs.NOTE_KIND, text="X",
                   bbox=(300.0, 300.0, 340.0, 312.0), limit_x1=600.0)
    cands = list(p._candidates(blk, "왼쪽으로 몰리는지 본다", (792.0, 1224.0)))
    left = [c for c in cands if c[0][2] <= blk.bbox[0] + 1]
    right = [c for c in cands if c[0][0] >= blk.bbox[2] - 1]
    assert left and right
    # 같은 dy(0)에서 왼쪽이 오른쪽보다 비싸야 한다
    lo_l = min(c[2] for c in left)
    lo_r = min(c[2] for c in right)
    assert lo_l > lo_r, "왼쪽 후보가 가로 거리를 물어야 한다"


def test_ink_cost_outweighs_a_short_move():
    """손글씨를 덮는 자리는 조금 더 멀더라도 피해야 한다 — `_W_INK`가 250일
    때 우리는 사람보다 더 겹쳤다(A1 픽셀 실측: 사람 0% 페이지에서 우리 10~12%).
    ⚠하드 게이트는 기각 — `_BLOCKED`가 넓은 사다리 탈출을 켜서 주석이 원문에서
    200pt 넘게 달아났다(99분위 207pt)."""
    assert xs._W_INK >= 3000.0
    # 잉크 5%인 가까운 자리(변위 0)보다 깨끗한 먼 자리(변위 50)가 싸야 한다
    dirty = 0.0 + xs._W_INK * max(0.05 - xs._INK_OK, 0.0)
    clean = 50.0
    assert dirty > clean


# ── 좌우 1순위 인접 배치(2026-08-26 사용자 설계) ──────────────────────
# "손글씨를 타이트하게 감싸고 같은 크기 상자를 인접하게, 좌우를 1순위로,
#  칸 사이에, 다른 글씨와 안 겹치게." 지그재그로 좌우를 번갈아 쓴다.

def _grid_png(w=1100, h=1700, extra=()):
    """가로 괘선 40줄 + 세로 칸선 2개를 그린 시트 흉내 페이지."""
    import numpy as np
    from PIL import Image
    a = np.full((h, w, 3), 255, dtype=np.uint8)
    for i in range(40):
        y = 100 + i * 18                      # 균일 간격 = 시트 관례
        a[y:y + 2, 60:900] = 0
    for x in (60, 900):                       # 세로 칸선(진짜 칸 경계)
        # ⚠_RULE_FILL(0.45)은 **열의 45% 이상**이 어두울 때 괘선으로 본다 —
        # 짧게 그리면 검출이 안 된다(처음에 y 100~820만 그려 42%로 미달).
        a[60:1640, x:x + 2] = 0
    for x0, y0, x1, y1 in extra:
        a[y0:y1, x0:x1] = 0
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, format="PNG")
    return buf.getvalue()


def test_row_grid_is_uniform_and_derived_from_rules():
    """시트 칸은 전부 같은 간격이다(사용자 확인) — 개별 괘선 좌표 대신
    피치를 재면 굵은 줄이 2px로 잡히거나 흐린 줄이 빠져도 안 흔들린다."""
    doc = FakeDoc(png=_grid_png())
    p = xs.XsheetProfile()
    p._page_ink(doc, 0)
    assert p._row_grid is not None
    _origin, pitch = p._row_grid
    assert pitch == pytest.approx(18.0 * 72.0 / xs._INK_DPI, abs=0.5)


def test_column_edges_come_from_vertical_rules_not_header_labels():
    """칸 경계는 **세로 괘선**에서 뽑는다.

    `geom.col_edges`는 머리글 라벨의 글자 시작 x 기반이라 `ACTION`처럼 가운데
    정렬된 라벨이 칸 한복판에 가짜 경계를 만든다 — A1 p54 실측: limit_x1이
    163pt인데 노트가 x=201까지 뻗어 **오른쪽 여유가 음수**였고, 그래서 주석이
    전부 왼쪽으로 몰렸다."""
    doc = FakeDoc(png=_grid_png())
    p = xs.XsheetProfile()
    p._page_ink(doc, 0)
    edges = p._col_edges
    assert edges, "세로 괘선을 못 찾았다"
    s = 72.0 / xs._INK_DPI
    assert min(edges) == pytest.approx(60 * s, abs=1.0)
    assert max(edges) == pytest.approx(900 * s, abs=1.0)


def test_blocked_right_falls_to_below_not_left():
    """오른쪽이 막히면 **아래**로 간다 — 왼쪽은 맨 뒤다.

    2026-08-27 사람 대조(A2 1,577건, 블록 bbox 기준): 사람은 오른쪽 27% ·
    아래 26% · 위 14% · **왼쪽 8%**를 쓴다. 우리는 왼쪽이 27%였는데, 원인은
    좌우를 번갈아 보던 지그재그였다(블록의 절반이 왼쪽부터 탐색). 지그재그를
    빼고 왼쪽을 맨 뒤로 미루자 사람과 같은 자리가 22.8→30.8%로 올랐고,
    지그재그가 막으려던 주석끼리 충돌은 A2 전 구간 0쌍 그대로였다."""
    doc = FakeDoc(png=_grid_png(extra=[(300, 300, 360, 340),
                                       (365, 295, 460, 345)]))
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S",
                 bbox=(300 * 0.72, 300 * 0.72, 360 * 0.72, 340 * 0.72),
                 limit_x1=600.0)
    p = xs.XsheetProfile()
    ov = p.place_with_doc(b, "가나", (792.0, 1224.0), doc, occupied=())
    assert ov.rect[1] >= b.bbox[3], "오른쪽이 막혔으면 아래여야 한다"
    assert ov.rect[2] > b.bbox[0], "왼쪽 여백으로 도망가지 않는다"


def test_narrow_side_slot_is_skipped_for_long_text():
    """좁은 틈에 번역문을 세로로 흘리지 않는다 — 사람은 그때 아래로 간다.

    옆자리는 **번역문 가장 긴 줄이 줄바꿈 없이 들어갈 때만** 쓴다
    (`_SIDE_MIN_W`, 글이 더 짧으면 그 글 폭까지만 요구). 스윕 실측: 18pt
    허용에서 60pt 요구로 올리며 같은 자리 29.1→30.2%로 단조 개선."""
    # 원문 오른쪽 18pt 지점에 진짜 칸 경계(세로 괘선)를 세운다
    doc = FakeDoc(png=_grid_png(extra=[(300, 300, 360, 340),
                                       (390, 60, 392, 1640)]))
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S",
                 bbox=(300 * 0.72, 300 * 0.72, 360 * 0.72, 340 * 0.72),
                 limit_x1=600.0)
    p = xs.XsheetProfile()
    ov = p.place_with_doc(b, "가나다라마바사아", (792.0, 1224.0), doc,
                          occupied=())
    assert ov.rect[1] >= b.bbox[3], "좁은 오른쪽에 끼워 넣지 않는다"


def test_side_placement_never_covers_handwriting():
    """손글씨를 덮는 자리는 후보에서 **탈락**한다(하드) — 사용자 지적:
    "원문과 겹치는 순간 가독성이 망가진다"."""
    # 원문 오른쪽 곁을 손글씨 크기 덩어리로 막는다
    doc = FakeDoc(png=_grid_png(extra=[(300, 300, 360, 340),
                                       (365, 295, 460, 345)]))
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S",
                 bbox=(300 * 0.72, 300 * 0.72, 360 * 0.72, 340 * 0.72),
                 limit_x1=600.0)
    p = xs.XsheetProfile()
    ov = p.place_with_doc(b, "가나", (792.0, 1224.0), doc, occupied=())
    ink = p._page_ink(doc, 0)
    assert xs._ink_ratio(ink, ov.rect, 1224.0) <= xs._SIDE_INK_OK


# ── 줄 단위 배치·기둥 방지·빈자리 탐색(2026-09-03) ────────────────────

def test_ink_rows_splits_handwriting_rows():
    """블록 안 손글씨 행을 가로 투영으로 나누고 행마다 x 범위를 잰다."""
    import numpy as np
    ink = np.zeros((300, 300), dtype=bool)
    ink[100:112, 40:120] = True          # 1행: x 40~120px
    ink[124:136, 40:90] = True           # 2행(12px 틈): x 40~90px
    ink[145:146, 200:260] = True         # 1px 잡티(10px 떨어짐) — 행이 아니다
    rows = xs._ink_rows(ink, (20 / (100 / 72.0), 90 / (100 / 72.0),
                              280 / (100 / 72.0), 150 / (100 / 72.0)))
    assert len(rows) == 2
    s = 72.0 / 100
    assert rows[0][1] == pytest.approx(100 * s) and rows[0][2] == pytest.approx(120 * s)
    assert rows[1][1] == pytest.approx(124 * s) and rows[1][2] == pytest.approx(90 * s)


def test_width_for_lines_widens_only_when_needed():
    """짧은 글은 자연폭 그대로, 긴 글은 max_lines 줄이 되는 폭까지만 넓힌다."""
    short = "표정"
    assert xs._width_for_lines(short, 9.0, 2) <= xs._natural_width(short, 9.0)
    long = "코니, 조셉, 나무에 새도우 효과 + 나무 드롭 섀도"
    w2 = xs._width_for_lines(long, 9.0, 2)
    w4 = xs._width_for_lines(long, 9.0, 4)
    assert len(xs._wrap_ko(long, w2, 9.0)) <= 2
    assert len(xs._wrap_ko(long, w4, 9.0)) <= 4
    assert w4 < w2 <= xs._natural_width(long, 9.0)


def test_side_box_widens_to_avoid_a_tower():
    """원문 폭이 좁고 번역이 길면 옆자리 상자가 max_lines 안에 들도록 넓어진다
    (실측 기둥: 28자 번역이 40pt 상자에서 7줄). 짧은 번역은 원문 폭 그대로."""
    anchor = (100.0, 100.0, 140.0, 148.0)          # 폭 40pt·4행 높이
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S", bbox=anchor,
                 limit_x1=600.0)
    long = "코니, 조셉, 나무에 새도우 효과 + 나무 드롭 섀도"
    old = next(iter(xs._side_candidates(anchor, b, long, (792.0, 1224.0))))
    new = next(iter(xs._side_candidates(anchor, b, long, (792.0, 1224.0),
                                        max_lines=4)))
    # 옛 상한 = max(원문 폭 40, _SIDE_MIN_W 60) = 60pt → 28자가 5줄+로 흐른다
    assert old[0][2] - old[0][0] == pytest.approx(xs._SIDE_MIN_W)
    assert len(xs._wrap_ko(long, old[0][2] - old[0][0], 9.0)) > 4  # 기둥
    assert new[0][2] - new[0][0] > old[0][2] - old[0][0]
    assert len(xs._wrap_ko(long, new[0][2] - new[0][0], 9.0)) <= 4
    short = next(iter(xs._side_candidates(anchor, b, "표정", (792.0, 1224.0),
                                          max_lines=4)))
    assert short[0][2] - short[0][0] == pytest.approx(40.0)      # 짧으면 그대로


def _rows_doc(blockers=()):
    """2행 손글씨 원문(px 300~360, y 300~318 / 330~348) + 방해 잉크."""
    return FakeDoc(png=_grid_png(extra=[(300, 300, 360, 318), (300, 330, 360, 348),
                                        *blockers]))


def test_place_lines_puts_each_line_beside_its_row():
    """통상자 곁이 막히면 다중줄 번역을 **행마다 한 줄씩** 곁에 앉힌다(사람
    관례: `STEPS/BACK` → `뒤로.`·`스텝.`을 각 행 옆에). 행 사이 틈에 잉크를
    두어 2줄 통상자는 오른쪽에 못 앉지만 행마다의 오른쪽은 비어 있다."""
    doc = _rows_doc(blockers=[(365, 319, 460, 329),      # 행 사이 틈의 잉크
                              (200, 290, 295, 360),      # 왼쪽 막힘
                              (300, 352, 460, 400),      # 아래 막힘
                              (300, 240, 460, 296)])     # 위 막힘
    s = 0.72
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="AMBER\nLEANS",
                 bbox=(300 * s, 300 * s, 360 * s, 348 * s), limit_x1=600.0)
    p = xs.XsheetProfile()
    out = p.place_with_doc(b, "앰버,\n기울인다.", (792.0, 1224.0), doc)
    assert isinstance(out, list) and [o.text for o in out] == ["앰버,", "기울인다."]
    ink = p._page_ink(doc, 0)
    for ov, row_top in zip(out, (300 * s, 330 * s)):
        assert ov.rect[0] >= 360 * s                       # 행 오른쪽 곁
        assert abs(ov.rect[1] - row_top) <= 8.0            # 제 행에 붙는다
        assert xs._ink_ratio(ink, ov.rect, 1224.0) <= xs._SIDE_INK_OK
    a, c = out[0].rect, out[1].rect
    assert min(a[3], c[3]) - max(a[1], c[1]) <= 0.0 or xs._occupied_frac(c, [a]) <= xs._OCC_OK


def test_place_lines_needs_a_row_per_line():
    """줄이 행보다 많거나 한 줄이라도 자리가 없으면 줄 단위 배치는 포기하고
    통상자 경로로 간다(반쪽 배치 금지) — 결과는 여전히 Overlay 하나."""
    doc = _rows_doc()
    s = 0.72
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="AMBER\nLEANS",
                 bbox=(300 * s, 300 * s, 360 * s, 348 * s), limit_x1=600.0)
    p = xs.XsheetProfile()
    ink = p._page_ink(doc, 0)
    assert p._place_lines(b, "가\n나\n다", (792.0, 1224.0), ink, ()) is None
    # 오른쪽 전부를 이웃 주석이 차지 → 행 오른쪽·왼쪽 다 막힘 → None
    wall = (360 * s + 1.0, 200.0, 600.0, 400.0)
    assert p._place_lines(b, "앰버,\n기울인다.", (792.0, 1224.0), ink,
                          [wall, (8.0, 200.0, 300 * s - 1.0, 400.0)]) is None
    out = p.place_with_doc(b, "앰버,\n기울인다.", (792.0, 1224.0), doc,
                           occupied=[wall, (8.0, 200.0, 300 * s - 1.0, 400.0)])
    assert not isinstance(out, list)


def test_free_slot_near_finds_the_nearest_clean_rect():
    """사다리가 전부 막힌 페이지에서 마스크 위의 가까운 빈 사각형을 찾는다."""
    import numpy as np
    ink = np.ones((1700, 1100), dtype=bool)         # 전부 잉크
    ink[520:600, 520:700] = False                   # 원문 오른쪽 아래의 빈 자리
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S",
                 bbox=(300.0, 360.0, 340.0, 380.0), limit_x1=600.0)
    p = xs.XsheetProfile()
    got = p._free_slot_near(b, "고개 기웃", (792.0, 1224.0), ink, ())
    assert got is not None
    cost, ov = got
    assert xs._ink_ratio(ink, ov.rect, 1224.0) <= xs._SIDE_INK_OK
    assert ov.rect[1] >= 520 * 0.72 and ov.rect[3] <= 600 * 0.72
    assert cost < xs._BLOCKED
    # 그 빈자리를 이웃 주석이 차지하면 못 찾는다
    assert p._free_slot_near(b, "고개 기웃", (792.0, 1224.0), ink,
                             [(520 * 0.72, 520 * 0.72, 700 * 0.72, 600 * 0.72)]) is None


def test_place_with_doc_prefers_a_near_free_slot_over_far_ladder():
    """막힌 블록의 최후 수단 — 넓은 사다리의 먼 자리(±80pt+)보다 반경 안의
    가까운 빈자리가 이긴다."""
    import numpy as np
    s = 0.72
    # 원문 주변을 손글씨로 빽빽하게 채우고, 원문 바로 오른쪽 아래 한 곳만 비운다
    blockers = []
    for y in range(240, 520, 24):
        for x in range(60, 900, 70):
            blockers.append((x, y, x + 60, y + 14))
    blockers = [r for r in blockers
                if not (410 <= r[0] <= 560 and 380 <= r[1] <= 420)]   # 빈 구멍
    doc = FakeDoc(png=_grid_png(extra=[(300, 300, 360, 340), *blockers]))
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S",
                 bbox=(300 * s, 300 * s, 360 * s, 340 * s), limit_x1=600.0)
    p = xs.XsheetProfile()
    ov = p.place_with_doc(b, "가나다라마바", (792.0, 1224.0), doc, occupied=())
    assert not isinstance(ov, list)
    ink = p._page_ink(doc, 0)
    assert xs._ink_ratio(ink, ov.rect, 1224.0) <= xs._FB_INK_HARD
    assert abs(ov.rect[1] - b.bbox[1]) <= 80.0            # 먼 사다리로 도망가지 않았다
    assert isinstance(ink, np.ndarray)


def test_build_plan_accepts_several_overlays_per_block():
    """줄 단위 배치는 한 블록을 여러 주석으로 돌려준다 — 계획은 각각을
    항목으로 싣고(같은 source_text) 뒤 블록의 점유 판정에도 넣는다."""
    from apps.server.domain.pdf_translate.overlay_plan import build_plan
    from apps.server.domain.pdf_translate.profiles.base import Overlay
    doc = FakeDoc(png=_grid_png())
    blocks = [_note(0, 100, 300), _note(0, 100, 400)]
    seen_occupied = []

    class P:
        name = "xsheet"
        def refine_ko(self, block, ko): return ko
        def place(self, block, ko, page_size): raise AssertionError
        def place_with_doc(self, block, ko, page_size, doc, occupied=()):
            seen_occupied.append(list(occupied))
            if block.bbox[1] == 300:
                return [Overlay(page=0, rect=(150.0, 300.0, 190.0, 312.0), text="앰버,", fontsize=9.0),
                        Overlay(page=0, rect=(150.0, 314.0, 220.0, 326.0), text="기울인다.", fontsize=9.0)]
            return Overlay(page=0, rect=(150.0, 400.0, 190.0, 412.0), text=ko, fontsize=9.0)

    plan = build_plan(doc, P(), blocks, ["앰버,\n기울인다.", "표정"], job_id="t")
    assert [it.text for it in plan.items] == ["앰버,", "기울인다.", "표정"]
    assert {it.source_text for it in plan.items[:2]} == {"raw"}
    assert len(seen_occupied[1]) == 2            # 두 줄 다 점유로 넘어갔다


# ── 과병합 분리 S3(2026-09-03): 빈 줄 조각 → 크롭 잉크 행 → 하위 블록 ──────

def _rows_png(path: Path, rows_px: list[tuple[int, int, int, int]], size=(300, 260),
              origin_pt: tuple[float, float] | None = None) -> None:
    """흰 크롭에 검은 '행' 사각형들을 그린 300dpi PNG(+원점 메타데이터)."""
    from PIL import Image, ImageDraw, PngImagePlugin
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in rows_px:
        d.rectangle([x0, y0, x1, y1], fill="black")
    info = PngImagePlugin.PngInfo()
    if origin_pt is not None:
        info.add_text(ht._CROP_META_KEY, f"{origin_pt[0]:.2f},{origin_pt[1]:.2f},0,0")
    im.save(path, pnginfo=info)


def test_crop_ink_rows_ignores_ruled_lines_and_specks(tmp_path):
    p = tmp_path / "c.png"
    _rows_png(p, [(20, 20, 200, 50), (0, 70, 300, 72),      # 행 + 가로 괘선
                  (20, 100, 120, 130), (150, 200, 152, 203)])  # 행 + 잡티
    rows, size = ht._crop_ink_rows(p)
    assert size == (300, 260)
    assert [(r[0], r[1], r[2]) for r in rows] == [(20, 20, 201), (20, 100, 121)]


def test_split_multinote_maps_segments_to_rows_and_page_coords(tmp_path):
    """빈 줄 조각 2개 ↔ 잉크 행 3개(2+1): 줄 수 합이 행 수와 같으니 정확 매핑.
    하위 bbox는 메타데이터 원점 + 행 픽셀/300dpi 로 페이지 좌표가 된다."""
    p = tmp_path / "c.png"
    _rows_png(p, [(20, 20, 200, 50), (20, 70, 180, 100), (20, 150, 100, 180)],
              origin_pt=(100.0, 200.0))
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="raw", bbox=(105.0, 205.0, 150.0, 240.0))
    parts = ht._split_multinote(b, "HEAD\nTURN\n\nOVS", p)
    assert [x.text for x in parts] == ["HEAD\nTURN", "OVS"]
    s = 72.0 / 300.0
    assert parts[0].bbox == pytest.approx((100 + 20 * s, 200 + 20 * s, 100 + 201 * s, 200 + 101 * s))
    assert parts[1].bbox == pytest.approx((100 + 20 * s, 200 + 150 * s, 100 + 101 * s, 200 + 181 * s))
    assert all(x.page == 0 and x.kind == xs.NOTE_KIND for x in parts)


def test_split_multinote_reorders_by_row_width(tmp_path):
    """모델이 위 노트를 뒤에 쓴 경우(1603 p4 실측) 행 폭↔글자 수 대응으로 바로잡는다:
    윗행 2개는 길고(넓은 잉크) 아랫행 1개는 짧다."""
    p = tmp_path / "c.png"
    _rows_png(p, [(20, 20, 280, 50), (20, 70, 260, 100), (20, 150, 60, 180)],
              origin_pt=(0.0, 0.0))
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="raw", bbox=(0, 0, 60, 40))
    parts = ht._split_multinote(b, "SO\n\nMATCH CUT\nTV/BG/CHYRON", p)
    assert [x.text for x in parts] == ["MATCH CUT\nTV/BG/CHYRON", "SO"]
    assert parts[0].bbox[1] < parts[1].bbox[1]


def test_split_multinote_refuses_when_rows_are_fewer_than_segments(tmp_path):
    """좌우 병렬 노트(행 1개에 조각 2개)는 쪼개지 않고 개행 하나로 합친다."""
    p = tmp_path / "c.png"
    _rows_png(p, [(20, 20, 280, 50)], origin_pt=(0.0, 0.0))
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="raw", bbox=(0, 0, 60, 12))
    parts = ht._split_multinote(b, "SI\n\nOVS", p)
    assert [x.text for x in parts] == ["SI\nOVS"] and parts[0].bbox == b.bbox
    # 조각이 하나면 그대로
    assert ht._split_multinote(b, "HEAD\nTURN", p)[0].text == "HEAD\nTURN"


def test_split_multinote_without_metadata_centres_on_block(tmp_path):
    """옛 잡의 크롭(원점 메타데이터 없음)은 블록 bbox 둘레 대칭 성장으로 근사."""
    p = tmp_path / "c.png"
    _rows_png(p, [(20, 20, 200, 50), (20, 150, 100, 180)], size=(300, 260))
    s = 72.0 / 300.0
    # 크롭 300×260px = 72×62.4pt; 블록 52×42.4pt → 좌우·상하 10pt씩 자란 셈
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="raw", bbox=(110.0, 210.0, 162.0, 252.4))
    parts = ht._split_multinote(b, "A B\n\nC", p)
    assert parts[0].bbox[0] == pytest.approx(100.0 + 20 * s)
    assert parts[0].bbox[1] == pytest.approx(200.0 + 20 * s)


def test_transcribe_splits_multinote_and_reasks_old_cache(tmp_path, monkeypatch):
    """전사 결과에 빈 줄이 있으면 하위 블록 2개가 나오고, 마커 없는 옛 캐시는
    행 3+ 크롭만 다시 묻는다(행 1개짜리 캐시는 그대로)."""
    blocks = [_note(0, 50, 100), _note(0, 50, 300)]
    crops = tmp_path / ht._CROPS_DIRNAME
    crops.mkdir(parents=True)
    names = [ht.crop_name(b) for b in blocks]
    _rows_png(crops / names[0], [(20, 20, 200, 50), (20, 70, 180, 100), (20, 150, 100, 180)],
              origin_pt=(45.0, 95.0))                       # 행 3 → 재전사 대상
    _rows_png(crops / names[1], [], origin_pt=(45.0, 295.0))  # 잉크 행 0 → 유지(v3=행 1+ 재전사)
    (tmp_path / ht._CACHE_NAME).write_text(
        json.dumps({names[0]: "HEAD\nTURN\nOVS", names[1]: "BLINK"}), encoding="utf-8")
    asked: list[str] = []

    def _fake(prompt, cwd, engine=None):
        asked.extend(n for n in names if n in prompt)
        assert "Vocabulary" not in prompt                   # vocab 미지정이면 힌트 없음
        return json.dumps({names[0]: ["HEAD\nTURN", "OVS"]})   # 파일당 배열

    monkeypatch.setattr(ht, "_run_cli", _fake)
    out = ht.transcribe(blocks, tmp_path)
    assert asked == [names[0]]                              # 잉크 없는 크롭은 안 물었다
    assert [b.text for b in out] == ["HEAD\nTURN", "OVS", "BLINK"]
    assert out[0].bbox[3] < out[1].bbox[1]                  # 위·아래로 나뉘었다
    cache = json.loads((tmp_path / ht._CACHE_NAME).read_text(encoding="utf-8"))
    assert cache[ht._CACHE_VERSION_KEY] == "2"
    # 마커가 있으면 다시 묻지 않는다
    asked.clear()
    out2 = ht.transcribe(blocks, tmp_path)
    assert asked == [] and [b.text for b in out2] == ["HEAD\nTURN", "OVS", "BLINK"]


def test_render_crops_writes_origin_metadata(tmp_path):
    from PIL import Image
    doc = FakeDoc(png=_png_bytes(400, 400))
    blocks = [_note(0, 20, 20)]
    ht.render_crops(doc, blocks, tmp_path)
    with Image.open(tmp_path / ht._CROPS_DIRNAME / ht.crop_name(blocks[0])) as im:
        meta = im.text[ht._CROP_META_KEY]
    x0, y0, x1, y1 = (float(v) for v in meta.split(","))
    assert x0 <= 20.0 and y0 <= 20.0 and x1 >= 60.0 and y1 >= 30.0   # 여백 포함


def test_join_continuations_keeps_phrases_together():
    """`/`·`+`로 시작하거나 `TO`·`/`로 끝나는 조각은 구절의 이어짐 — 되붙인다
    (대조군 실측: `BODY +HEAD / TO` | `H EXP.`, `TURN + STEP` | `/ STOP`)."""
    assert ht._join_continuations(["BODY\n+HEAD\n/ TO", "H EXP."]) == ["BODY\n+HEAD\n/ TO\nH EXP."]
    assert ht._join_continuations(["TURN +\nSTEP", "/ STOP"]) == ["TURN +\nSTEP\n/ STOP"]
    assert ht._join_continuations(["IN", "/ BODY\nSHIFT", "H"]) == ["IN\n/ BODY\nSHIFT", "H"]
    assert ht._join_continuations(["ARMS\nCONT.\nUP", "OVS"]) == ["ARMS\nCONT.\nUP", "OVS"]
    assert ht._as_text(["A", " ", "B"]) == "A\n\nB" and ht._as_text("X") == "X"
    assert ht._as_text(3) is None


# ── 1605_A1 실물 결함 2종(2026-09-03) ──────────────────────────────────

def test_font_ladder_middle_step_scales_with_page():
    """글꼴 사다리 가운데 단이 페이지 스케일을 탄다 — 2200pt 판형에서 8pt(정상 25pt)
    로 굽힌 항목이 12.8%였다. 792 판형에선 예전 그대로 8pt."""
    b = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S", bbox=(100.0, 100.0, 140.0, 112.0),
                 limit_x1=600.0)
    sizes = sorted({fs for _r, fs, _d in xs.XsheetProfile()._candidates(
        b, "가나다라마바사아자차카타", (792.0, 1224.0))}, reverse=True)
    assert sizes == [9.0, 8.0, 7.2]
    big = PdfBlock(page=0, kind=xs.NOTE_KIND, text="S", bbox=(300.0, 300.0, 400.0, 330.0),
                   limit_x1=1600.0)
    sizes = sorted({round(fs, 1) for _r, fs, _d in xs.XsheetProfile()._candidates(
        big, "가나다라마바사아자차카타", (2200.0, 3400.0))}, reverse=True)
    xs._apply_page_scale(792.0)                          # 다른 테스트를 위해 원복
    assert min(sizes) > 15.0 and len(sizes) == 3
    assert sizes[1] == pytest.approx(8.0 * 2200 / 792, abs=0.1)


def test_refine_ko_clears_leftover_codes_like_a_human():
    """낱말과 섞인 STL·OVS는 옮기고 SI는 지운다(사람 납품본 관례). 원문에 그 코드가
    없으면 건드리지 않는다."""
    p = xs.XsheetProfile()
    def r(src, ko): return p.refine_ko(PdfBlock(page=0, kind=xs.NOTE_KIND, text=src,
                                                bbox=(0, 0, 40, 10)), ko)
    # (12자 이하 다중줄은 기존 규칙대로 한 줄로 접힌다)
    assert r("STL\nBACK", "STL\n뒤로.") == "안착 뒤로."
    assert r("OVS\nSLIGHT", "OVS 약간") == "오버슛 작게"       # SLIGHT→작게(1605)
    assert r("LEAN.\nTURNS\nHEAD\n(SI", "기울이며 고개\n돌린다 (SI") == "기울이며 고개 돌린다"
    assert r("HANDS\nGEST\nOUT\nSI", "양손 밖으로\n제스쳐, SI.") == "두손 밖으로 제스쳐."
    assert r("SI\nHEAD\nDN", "SI 고개\n아래로.") == "고개 아래로."
    assert r("RT. HAND\nBRUSH\nLACQUER\nSI\n(H)", "오른손,\n브러시로 락커\n칠한다.\nSI\n(H)") == "오른손,\n붓으로 광택제\n칠한다.\n(H)"
    assert r("POSE\nTO\nEYES\nLEAD\nTO\nSI", "눈이 리드하며\n포즈로,\nSI.") == "눈이 리드하며 포즈로."
    assert r("HUSTLE", "허슬 STL") == "허슬 STL"          # 원문에 STL 코드 없음
    assert r("SIT DOWN", "앉는다 SI") == "앉는다 SI"       # SIT는 SI가 아니다


# ── 등장인물 이름표(2026-09-03, 1605_A1 사용자 검수) ────────────────────

def test_cast_names_decode_prompt_and_fix(monkeypatch, tmp_path):
    """이름만 있는 노트는 결정적 해독, 프롬프트엔 이름표, 원문에 이름이 있으면
    오음역(차네·챈·샌드·에미)을 표기로 교정. 운영자 파일이 내장값을 덮는다."""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    assert xs._decode_code_note("CHANE") == "체인"
    assert xs._decode_code_note("EMI") == "에밀리오"
    assert xs._decode_code_note("SAND\nOVS") == "산드라\n오버슛"
    assert xs._decode_code_note("CHANE DRIVES") is None            # 낱말 섞이면 번역기 몫
    rule = xs.XsheetProfile().prompt_line_rule_now()
    assert "CHANE → 체인" in rule and "SAND → 산드라" in rule and "EMI → 에밀리오" in rule
    p = xs.XsheetProfile()
    def r(src, ko): return p.refine_ko(PdfBlock(page=0, kind=xs.NOTE_KIND, text=src,
                                                bbox=(0, 0, 40, 10)), ko)
    assert r("CHANE DRIVES\nCAR", "차네가 차를\n몰고 간다.") == "체인이 차를 몰고 간다."
    assert r("CHANE\nLOOKS", "차네는 본다.") == "체인은 본다."
    assert r("EMI\nHANDS", "에미의 두 손.") == "에밀리오의 두손."
    assert r("A CHAN\nTURNS", "A 찬\n턴한다.") == "A 체인 턴한다."
    assert r("SAND\nCLEAN", "샌드\n닦는다.") == "산드라 닦는다."           # 12자 이하 접힘
    assert r("KICKS", "찬다.") == "발찬다."                            # 이름 없음(KICK 규칙만)
    assert r("CHANE", "찬다.") == "찬다."                              # 한글 낱말 안의 찬은 보존
    (tmp_path / "xsheet_cast.txt").write_text("# 작품별\nCHANE => 차니\nZOE = 조이\n", encoding="utf-8")
    assert xs._decode_code_note("CHANE") == "차니" and xs._decode_code_note("ZOE") == "조이"


def test_refine_ko_handles_s1_variant_underscore_and_miguel():
    """전사가 SI를 `S1`로 읽어도 지우고, 밑줄 연결은 `&`로, MIGUEL은 미구엘."""
    p = xs.XsheetProfile()
    def r(src, ko): return p.refine_ko(PdfBlock(page=0, kind=xs.NOTE_KIND, text=src,
                                                bbox=(0, 0, 40, 10)), ko)
    assert r("CHANE\nLT. HAND\nS1", "체인 왼손\nS1.") == "체인 왼손"
    assert r("S1", "슬로우 인") == ""                       # S1 단독 = SI 단독
    assert r("2\nS1", "2 슬로우 인") == ""
    assert r("SAND_CLOTH", "산드라_옷") == "산드라&옷"
    assert r("MIGUEL_PHONE", "미겔_전화") == "미구엘&전화"
    assert xs._decode_code_note("MIGUEL") == "미구엘"


def test_refine_ko_1605_terms_and_artifacts():
    """1605 사람 대조 전수(3,156쌍)에서 검증한 원문 조건부 용어 + 인공물 정리."""
    p = xs.XsheetProfile()
    def r(src, ko): return p.refine_ko(PdfBlock(page=0, kind=xs.NOTE_KIND, text=src,
                                                bbox=(0, 0, 40, 10)), ko)
    assert r("EYES\nHEAD", "시선\n고개") == "두눈 고개"
    assert r("EYES\nBLINK", "눈깜박") == "눈깜박"                     # 눈깜박은 보존
    assert r("HANDS\nUP", "손 올린다.") == "두손 위로."
    assert r("ARMS\nUP", "팔\n올린다.") == "두팔 위로."
    assert r("KICK\nLEGS", "다리를\n찬다.") == "두다리를 발찬다."
    assert r("STL\nTO", "안착로.") == "안착."
    assert r("EXP.\nTO", "표정\n~로.") == "표정."
    assert r("SHIFT", "시프트.") == "이동."
    assert "표정" in r("TO\nEXP.", "익스포저로.")
    assert "붓" in r("RT. HAND\nMOVES\nBRUSH", "오른손,\n브러시를 옮긴다.")
    assert r("RIM LIT\nFLICKER\nCYCLE", "림 라이트 눈깜박\n싸이클.") == "림라이트 깜빡임 싸이클."
    assert r("CAST SHADOW FX", "캐스트 섀도우 효과") == "투영그림자 효과"
    assert r("* ADLIB\nCLOTH", "* 애드립, 천.") == "* 임의로, 행주."
    assert r("OVS\nSUBTLE", "오버슛\n약하게.") == "오버슛 은근하게."
    assert r("OVS\nSLIGHT", "오버슛 약간.") == "오버슛 작게."
    assert r("PARTY LIGHT\n(C) POP TO\nHP", "파티 조명\n(C) 팝 투 HP.") == "파티조명 (C) 팍"
    assert r("ALL\nFRAT", "프랫\n전원.") == "협회원 전원."
    assert r("DROPPER\n& BOTTLE", "드로퍼와\n병.") == "스포이드와 병."


def test_decode_passes_circled_letters_and_numbers():
    """`C BOB`·`(H) BOB`·`2034B`처럼 원문자·번호가 섞여도 해독한다 — 이런 토큰
    하나 때문에 LLM으로 넘어가 에코로 버려지던 주석 208건(1605)."""
    assert xs._decode_code_note("C BOB") == "C 바비"
    assert xs._decode_code_note("(H) BOB") == "(H) 바비"
    assert xs._decode_code_note("BOB\n2034B") == "바비\n2034B"
    assert xs._decode_code_note("OS") == "씬밖"
    assert xs._decode_code_note("C WALKS") is None


def test_transcribe_blocks_normalises_wt_to_with(tmp_path, monkeypatch):
    blocks = [_note(0, 50, 100)]
    _touch_crops(tmp_path, blocks)
    monkeypatch.setattr(ht, "_run_cli", lambda prompt, cwd, engine=None: json.dumps(
        {ht.crop_name(blocks[0]): "INTO\nPOSE\nWT\nFOOD"}))
    out = xs.XsheetProfile().transcribe_blocks(blocks, tmp_path)
    assert out[0].text == "INTO\nPOSE\nW/\nFOOD"


def test_refine_ko_markers_and_second_pass_terms():
    p = xs.XsheetProfile()
    def r(src, ko): return p.refine_ko(PdfBlock(page=0, kind=xs.NOTE_KIND, text=src,
                                                bbox=(0, 0, 40, 10)), ko)
    assert r("STL\nTO", "STL로.") == "안착."                      # 순서 버그(안착로) 재발 방지
    assert r("HU\nHU", "허\n허") == ""                           # 훅업 기호 = 드롭
    assert r("SO", "슬로우 아웃") == "" and r("HP", "HP") == ""
    assert r("SO\nHEAD\nUP", "슬로우 아웃,\n고개 위로.") == "고개 위로."
    assert r("STL\nDN", "안착\n다운.") == "안착 아래로."
    assert r("STL\nBACK", "안착\n백.") == "안착 뒤로."
    assert r("C UP", "C 업") == "C 위로"
    assert r("NEXT\nCANDLE", "다음\n초.") == "다음 양초."
    assert r("CANDLE FLAME FX", "촛불 불꽃 효과") == "양초불꽃 효과"
    assert r("RT. ARM &\nPENCIL UP", "오른 팔과\n펜슬 위로.") == "오른팔과 연필 위로."
    assert r("ENTER\nBOBBY", "바비 등장.") == "바비 들어온다."
    assert r("EYES\nTO", "두눈\n~쪽으로.") == "두눈."


def test_refine_ko_up_rule_respects_word_boundary_and_joins_left_right():
    p = xs.XsheetProfile()
    def r(src, ko): return p.refine_ko(PdfBlock(page=0, kind=xs.NOTE_KIND, text=src,
                                                bbox=(0, 0, 40, 10)), ko)
    assert r("LT. ARM\nUP", "왼 팔 들어올린다.") == "왼팔 들어올린다."     # `들어위로` 금지
    assert r("ARMS\nUP", "팔 올린다.") == "두팔 위로."
    assert r("RT. HAND\nKNIFE", "오른 손, 칼.") == "오른손, 칼."
    assert r("NOTEBOOK", "노트북") == "공책"


def test_transcribe_blocks_passes_vocabulary_hint(tmp_path, monkeypatch):
    """프로파일이 코드·인물 이름 어휘를 전사 프롬프트 힌트로 넘긴다(오독 수렴)."""
    blocks = [_note(0, 50, 100)]
    _touch_crops(tmp_path, blocks)
    seen = []
    monkeypatch.setattr(ht, "_run_cli", lambda prompt, cwd, engine=None: (
        seen.append(prompt), json.dumps({ht.crop_name(blocks[0]): "CHANE"}))[1])
    out = xs.XsheetProfile().transcribe_blocks(blocks, tmp_path)
    assert "Vocabulary" in seen[0] and "CHANE" in seen[0] and "STL" in seen[0]
    assert out[0].ko == "체인"


def test_refine_ko_ovs_misread_as_overlap():
    p = xs.XsheetProfile()
    def r(src, ko): return p.refine_ko(PdfBlock(page=0, kind=xs.NOTE_KIND, text=src,
                                                bbox=(0, 0, 40, 10)), ko)
    assert r("OVS\nSUBTLE", "오버랩\n미세하게.") == "오버슛 은근하게."
    assert r("OVS\nO.LAP", "오버슛\n오버랩.") == "오버슛 오버랩."   # 진짜 오버랩은 보존
    assert xs._decode_code_note("C DROPS") == "C 방울"


def test_refine_ko_fifth_pass_rules():
    p = xs.XsheetProfile()
    def r(src, ko): return p.refine_ko(PdfBlock(page=0, kind=xs.NOTE_KIND, text=src,
                                                bbox=(0, 0, 40, 10)), ko)
    assert r("CHAN,\nSAC\nHP", "체인,\n산드라 HP") == "체인, 포대"
    assert r("SAND\nSAC", "산드라, 포대") == "산드라, 포대"          # SAND가 있으면 보존
    assert r("(OUS)\nHOLD", "(화면 밖)\n홀드.") == "오버슛 홀드."
    assert r("LIQUID\nW/W\nACTION", "액체 웨이브\n액션.") == "액체 액션맞춰 움직인다."
