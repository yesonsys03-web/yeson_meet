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
                 text: str = "", png: bytes | None = None):
        self._pages = pages
        self._size = size
        self._text = text
        self._png = png or _png_bytes()
        self.render_calls: list[tuple[int, int]] = []

    @property
    def page_count(self) -> int:
        return self._pages

    def page_size(self, page: int):
        return self._size

    def raw_blocks(self, page: int):
        if not self._text:
            return []
        from apps.server.domain.pdf_translate.backend import RawBlock
        return [RawBlock(text=self._text, bbox=(0, 0, 10, 10))]

    def render_png(self, page: int, *, dpi: int = 120) -> bytes:
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


def test_detect_skips_ocr_for_text_documents(monkeypatch):
    engine = FakeEngine([])
    _install_engine(monkeypatch, engine)
    doc = FakeDoc(text="Dialog Action Notes and plenty of body text here")
    assert xs.XsheetProfile().detect(doc) is False
    assert engine.calls == 0  # 텍스트 문서엔 OCR 비용을 쓰지 않는다


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


def test_extract_filters_template_and_clusters(monkeypatch):
    dpi = xs._OCR_DPI
    engine = FakeEngine([
        # 인쇄 템플릿(제외 대상): 헤더·프레임번호·음소·푸터·화이트리스트 단어
        (_box_px((400, 100, 460, 112), dpi), "ANIMATOR", 0.99),
        (_box_px((355, 300, 365, 310), dpi), "7", 0.99),
        (_box_px((395, 300, 405, 310), dpi), "EH", 0.9),
        (_box_px((100, 1200, 130, 1210), dpi), "410", 0.99),
        (_box_px((600, 300, 640, 310), dpi), "FOOTAGE", 0.99),
        # 손글씨 노트 1: 세로로 쌓인 두 단어 → 한 블록으로 클러스터
        (_box_px((50, 200, 95, 209), dpi), "SUBnE", 0.6),
        (_box_px((50, 212, 100, 221), dpi), "T2Em3LE", 0.55),
        # 손글씨 노트 2: 카메라 노트 구역
        (_box_px((700, 300, 760, 310), dpi), "TRUCK UP", 0.7),
        # 한글 노트(재투입 안전장치로 제외)
        (_box_px((50, 400, 90, 410), dpi), "행크", 0.9),
    ])
    _install_engine(monkeypatch, engine)
    blocks = xs.XsheetProfile().extract(FakeDoc())
    assert len(blocks) == 2
    stacked = next(b for b in blocks if b.bbox[0] < 200)
    camera = next(b for b in blocks if b.bbox[0] > 600)
    assert stacked.kind == xs.NOTE_KIND
    assert stacked.text == "SUBnE T2Em3LE"          # 원시 OCR(전사 전 임시)
    assert stacked.bbox == (50.0, 200.0, 100.0, 221.0)
    assert stacked.limit_x1 == pytest.approx(xs._ACTION_X1)
    assert camera.limit_x1 == pytest.approx(792.0 - 8.0)


# ---------------------------------------------------------------- place


def test_place_prefers_right_of_note():
    profile = xs.XsheetProfile()
    block = PdfBlock(page=0, kind=xs.NOTE_KIND, text="x",
                     bbox=(50, 200, 95, 225), limit_x1=xs._ACTION_X1)
    ov = profile.place(block, "행크에 내내 떨림.", (792.0, 1224.0))
    assert ov.fontsize == xs._FONTSIZE
    assert ov.rect[0] == pytest.approx(98.0)         # 원문 오른쪽에서 시작
    assert ov.rect[2] <= xs._ACTION_X1 + 0.01        # 열 경계를 넘지 않는다
    assert ov.rect[1] == pytest.approx(200.0)


def test_place_falls_back_below_when_row_is_full():
    profile = xs.XsheetProfile()
    block = PdfBlock(page=0, kind=xs.NOTE_KIND, text="x",
                     bbox=(10, 200, 340, 210), limit_x1=xs._ACTION_X1)
    ov = profile.place(block, "아주 긴 번역 문장이 들어간다.", (792.0, 1224.0))
    assert ov.rect[1] == pytest.approx(212.0)        # 원문 아래
    assert ov.rect[0] == pytest.approx(10.0)
    assert ov.rect[2] <= xs._ACTION_X1 + 0.01


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
                        lambda prompt, cwd: json.dumps(canned))
    out = ht.transcribe(blocks, tmp_path)
    # 빈값(마커)·숫자만(셀번호)은 떨어지고 실노트만 남는다
    assert len(out) == 1
    assert out[0].text == "WALK\nWEST."
    assert out[0].bbox == blocks[0].bbox
    # 캐시가 남아 재실행 시 CLI를 다시 부르지 않는다
    cache = json.loads((tmp_path / ht._CACHE_NAME).read_text(encoding="utf-8"))
    assert cache[names[0]] == "WALK\nWEST."

    def _boom(prompt, cwd):
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

    def _fake(prompt, cwd):
        batch = [n for n in (ht.crop_name(b) for b in blocks) if n in prompt]
        calls.append(batch)
        if len(calls) == 1:
            raise RuntimeError("첫 배치 실패")
        return json.dumps({n: f"NOTE {i}" for i, n in enumerate(batch)})

    monkeypatch.setattr(ht, "_run_cli", _fake)
    out = ht.transcribe(blocks, tmp_path)
    assert len(calls) == 5                      # 실패해도 다음 배치는 돈다
    assert len(out) == 8                        # 실패 배치 몫만 떨어진다


def test_transcribe_cancellation(tmp_path, monkeypatch):
    blocks = [_note(0, 50, 100)]
    _touch_crops(tmp_path, blocks)
    monkeypatch.setattr(ht, "_run_cli",
                        lambda prompt, cwd: (_ for _ in ()).throw(
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

    def _fake(prompt, cwd):
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

    def _fake(prompt, cwd):
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
    monkeypatch.setattr(ht, "_run_cli", lambda prompt, cwd: json.dumps(
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

    def _fake(prompt, cwd):
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
    engine = FakeEngine([
        (_box_px((100, 200, 108, 210), dpi), "M", 0.5),        # 잡티: 8x10pt 1글자
        (_box_px((100, 300, 118, 312), dpi), "AD", 0.6),       # 작지만 2글자 → 유지
        (_box_px((100, 400, 160, 430), dpi), "X", 0.5),        # 크다(60x30) → 유지
    ])
    _install_engine(monkeypatch, engine)
    blocks = xs.XsheetProfile().extract(FakeDoc())
    texts = sorted(b.text for b in blocks)
    assert texts == ["AD", "X"]


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
