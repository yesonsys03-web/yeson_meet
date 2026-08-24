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
    texts = sorted(b.text for b in blocks)
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
    blocks = xs.XsheetProfile().extract(FakeDoc(pages=10))
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
    assert [b.text for b in blocks] == []            # 머리글 위는 전부 보류


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
    assert len(cache) == 3


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
    assert sorted(b.text for b in blocks) == ["AD", "X"]


def test_argv_differs_per_cli():
    """`--print-timeout`은 agy 전용 — claude에 넘기면 인자 오류로 즉사한다."""
    agy = ht._argv_for("agy", "/bin/agy", "P")
    claude = ht._argv_for("claude", "/bin/claude", "P")
    assert agy[:5] == ["/bin/agy", "-p", "P", "--add-dir", "."]
    assert "--print-timeout" in agy
    assert claude == ["/bin/claude", "-p", "P", "--add-dir", "."]
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
            stdout = json.dumps({ht.crop_name(blocks[0]): "WALK WEST."})
            stderr = ""
        return R()

    monkeypatch.setattr(ht.subprocess, "run", _fake)
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_cli.resolve_cli",
        lambda name: f"/usr/bin/{name}")
    out = ht.transcribe(blocks, tmp_path, engine="claude")
    assert out and seen == ["/usr/bin/claude"]


def _ko_note(src: str) -> PdfBlock:
    return PdfBlock(page=0, kind=xs.NOTE_KIND, text=src,
                    bbox=(0.0, 0.0, 10.0, 10.0), limit_x1=None)


@pytest.mark.parametrize(("src", "ko", "want"), [
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
    rects = [r for r, _fs in xs.XsheetProfile()._candidates(
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
