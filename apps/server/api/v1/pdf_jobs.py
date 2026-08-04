"""PDF 스토리보드 번역 작업 API — video_jobs.py와 동형의 얇은 라우트."""
from __future__ import annotations

import asyncio
import shutil
import threading
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.api.v1.video_jobs import _default_owner_id
from apps.server.db.models import PdfJob
from apps.server.db.session import get_session
from apps.server.domain.pdf_translate.backend import open_pdf
from apps.server.domain.pdf_translate.overlay_plan import (
    ManualLabel,
    add_manual,
    compose,
    delete_manual,
    is_usable_rect,
    load_edits,
    load_plan,
    load_plan_status,
    panels_resolver,
    patch_manual,
    purge_dangling,
    repoint_manual,
    save_edits,
    upsert_override,
)
from apps.server.domain.pdf_translate.panel_ocr import decode_panel_label_lines
from apps.server.domain.pdf_translate.pdf_run import (
    prune_old_pdf_jobs,
    rebake_pdf_job,
    run_pdf_job,
)
from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir
from apps.server.domain.pdf_translate.pdf_tasks import (
    cancel_pdf_task,
    is_rebaking,
    start_background_task,
    start_pdf_task,
)
from apps.server.domain.pdf_translate.profiles import profile_by_name
from apps.server.domain.pdf_translate.profiles.storyboard import PANEL_ADDRESS_REV
from apps.server.domain.video_captions.ingest import save_upload
from apps.server.domain.video_captions.translate_cli import list_translate_engines

router = APIRouter(tags=["pdf-jobs"], prefix="/pdf-jobs")

# 엔진 목록에서 자동 도출 — video_jobs와 동일 이유(하드코딩 드리프트 방지)
_PROVIDER_PATTERN = "^(" + "|".join(
    e["value"] for e in list_translate_engines()) + ")$"

_TERMINAL = ("done", "error", "cancelled")

# v1에서 사람이 고칠 수 있는 종류. 명세 Goal이 "**판넬 라벨**을 추가·수정·이동·
# 삭제"라 정확히 여기까지다 — dialog/action 주석은 목록에 읽기 전용으로 보인다.
_EDITABLE_KIND = "panel_label"

# 잡별 편집 파일 락.
#
# `threading.Lock`인 이유: `asyncio.Lock`은 코루틴만 배제한다. 편집 파일 IO와
# JSON 파싱을 이벤트 루프에서 하면 이 리포가 명시적으로 금지한 패턴이 되므로
# (`pdf_run.py:104-106` — "실시간 자막 WebSocket이 수십ms 멎는다") `to_thread`로
# 내리는데, 그러면 상호배제도 스레드 수준이어야 한다.
_EDIT_LOCKS: dict[str, threading.Lock] = {}
_EDIT_LOCKS_GUARD = threading.Lock()


class _VersionConflict(Exception):
    """낙관적 동시성 충돌 — 다른 창이 먼저 저장했다."""

    def __init__(self, current: int):
        super().__init__(current)
        self.current = current


def _edit_lock(job_id: UUID) -> threading.Lock:
    with _EDIT_LOCKS_GUARD:
        return _EDIT_LOCKS.setdefault(str(job_id), threading.Lock())


def _mutate_edits(job_id: UUID, expected_version: int | None, fn):
    """편집 파일 read-modify-write — **워커 스레드에서** 잡별 락을 쥐고 돈다.

    `fn`은 순수 함수여야 한다(편집 in → 편집 out). 판넬 좌표처럼 문서가 필요한
    값은 락에 들어오기 **전에** 라우트가 계산해 넘긴다 — 원본 PDF는 이 구간에
    바뀌지 않으므로 락 밖에서 읽어도 안전하다.
    """
    job_dir = pdf_job_dir(job_id)
    with _edit_lock(job_id):
        edits = load_edits(job_dir, job_id=str(job_id))
        if expected_version is not None and edits.edits_version != expected_version:
            raise _VersionConflict(edits.edits_version)
        updated = fn(edits)
        save_edits(job_dir, updated)
        return updated


async def _require_editable(db: AsyncSession, job_id: UUID) -> PdfJob:
    """편집 가능한 상태인가 — 진행 중이면 막는다(파이프라인이 계획을 다시 쓴다)."""
    job = await _get_job(db, job_id)
    if job.status != "done":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "작업이 진행 중입니다 — 끝난 뒤에 편집하세요")
    if not job.format or profile_by_name(job.format) is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "이 작업은 편집을 지원하지 않습니다")
    if not job.source_path or not Path(job.source_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "원본 PDF가 없습니다")
    return job


def _page_panel_rects(source_path: str, fmt: str, page: int) -> tuple:
    """그 페이지의 판넬 rect들 — 수동 라벨의 주소를 절대 좌표로 풀 때 쓴다."""
    profile = profile_by_name(fmt)
    hook = getattr(profile, "panels", None) if profile is not None else None
    if hook is None:
        return ()
    doc = open_pdf(Path(source_path))
    try:
        if not (0 <= page < doc.page_count):
            raise IndexError(page)
        return tuple(hook(doc, page))
    finally:
        doc.close()


def _start_pdf_pipeline(external_id: UUID) -> None:  # test seam
    start_pdf_task(external_id, run_pdf_job(external_id))


def _start_rebake(external_id: UUID) -> None:  # test seam
    start_pdf_task(external_id, rebake_pdf_job(external_id))


def _prune_old_jobs() -> None:  # test seam
    # 새 작업이 생길 때마다 최근 RETENTION_KEEP개만 유지 (개수 상한 정책).
    # 응답을 막지 않도록 fire-and-forget — 방금 만든 작업은 queued(in-flight)라
    # 삭제 대상에서 제외된다. video_jobs._prune_old_jobs 미러.
    start_background_task(prune_old_pdf_jobs())


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


class _DecodeBody(BaseModel):
    texts: list[str]


# ⚠ 리터럴 세그먼트라 `/{job_id}` 계열보다 먼저 등록한다(FastAPI는 등록 순서로
# 매칭한다). 현재 POST `/{job_id}`가 없어 실제 충돌은 없지만 관례를 지킨다.
@router.post("/decode-panel-label")
async def decode_panel_label(body: _DecodeBody) -> dict:
    """영문 판넬 약어 → 사람 납품본과 같은 줄 구성의 한글(해독 실패 시 null).

    작업과 무관하고 DB도 건드리지 않는다 — 입력칸 옆 미리보기 전용이다.
    자동 라벨과 **같은 함수**를 쓰므로 표기가 갈라지지 않는다.
    """
    lines = decode_panel_label_lines([t for t in body.texts if t.strip()])
    return {"lines": lines}


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
    _prune_old_jobs()
    _start_pdf_pipeline(external_id)
    return {"job_id": str(external_id)}


def _edit_badges(external_ids: list[UUID]) -> list[dict]:
    """목록 배지(`has_edits`·`stale`) — **`overlay_plan.json`은 절대 열지 않는다.**

    이 라우트는 활성 잡이 있으면 프런트가 1.5초마다 친다
    (`PdfTranslatePanel.tsx:71-75`, `isActivePdfStatus`에 `overlaying`이 있어
    재굽기 내내 폴링한다). 여기서 400KB급 계획을 파싱하면 폴링마다 이벤트
    루프가 멎는다 — 그래서 파이프라인이 함께 쓰는 소형 `plan_status.json`과
    사람 저작물인 `label_edits.json`(수동 라벨 수십 건 ≈ 수 KB)만 읽는다.
    """
    out: list[dict] = []
    for eid in external_ids:
        job_dir = pdf_job_dir(eid)
        edits = load_edits(job_dir, job_id=str(eid))
        st = load_plan_status(job_dir)
        out.append({
            "has_edits": edits.item_count() > 0,
            "stale": (st is not None
                      and st.get("baked_edits_version") != edits.edits_version),
        })
    return out


@router.get("")
async def list_pdf_jobs(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    rows = (await db.execute(
        select(PdfJob).order_by(PdfJob.created_at.desc(), PdfJob.id.desc())
    )).scalars().all()
    items = [_summary(j) for j in rows]
    # 파일 읽기는 한 번의 to_thread로 묶어 이벤트 루프를 막지 않는다.
    badges = await asyncio.to_thread(_edit_badges, [j.external_id for j in rows])
    for item, badge in zip(items, badges):
        item.update(badge)
    return {"items": items}


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


def _page_panels(path: str, page: int, profile_name: str) -> dict:
    """판넬 주소 열거 — `_render_page`와 같은 open→use→close 패턴.

    프로파일은 `job.format`으로 되찾는다(재감지 없음). `panel_layout` 훅이
    없는 프로파일이면 `LookupError` — 호출부가 409로 끝낸다.
    """
    profile = profile_by_name(profile_name)
    layout = getattr(profile, "panel_layout", None) if profile is not None else None
    if layout is None:
        raise LookupError(profile_name)
    doc = open_pdf(Path(path))
    try:
        if page < 0 or page >= doc.page_count:
            raise IndexError(page)
        width, height = doc.page_size(page)
        is_panel_page, rects = layout(doc, page)
        return {
            "page_size": [width, height],
            "is_panel_page": is_panel_page,
            "panels": [{"index": i, "rect": list(r)} for i, r in enumerate(rects)],
        }
    finally:
        doc.close()


@router.get("/{job_id}/page/{page}/panels")
async def get_pdf_page_panels(
    job_id: UUID, page: int,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """이 페이지의 판넬 칸 — 수동 라벨이 붙을 주소.

    좌표는 **원본 기준 pt**다(원점 좌상단). 화면 픽셀 변환은 클라이언트가 한다
    — pt를 유일한 진실로 두고 px는 파생값으로 유지하기 위해서다.
    """
    job = await _get_job(db, job_id)
    if not job.source_path or not Path(job.source_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "원본 PDF가 없습니다")
    if not job.format:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "이 작업은 편집을 지원하지 않습니다")
    try:
        return await asyncio.to_thread(
            _page_panels, job.source_path, page, job.format)
    except IndexError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "페이지 범위 밖입니다")
    except LookupError:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "이 작업은 편집을 지원하지 않습니다")


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
    rebaking = is_rebaking(job_id)
    cancel_pdf_task(job_id)
    # 최종 상태의 **유일한 저자는 이 라우트**다. `cancel_pdf_task`가
    # `task.cancel()`보다 먼저 세대를 밀고(`pdf_tasks.py:61-67`) 그 뒤 커밋까지
    # await 지점이 없으므로, 뒤늦게 끝난 태스크의 상태 쓰기는 세대 가드에
    # 막힌다 — 누가 먼저 쓰느냐는 순서 가정이 필요 없다.
    if rebaking or (job.translated_path and Path(job.translated_path).exists()):
        # 재굽기·재번역 취소: 멀쩡한 번역본이 디스크에 그대로 있다.
        # `cancelled`로 굳히면 /download가 영구 409가 되고(status != "done")
        # 편집·rebake·retranslate가 전부 막히며, in-flight가 아니게 되어
        # 다음 업로드의 프루닝이 그 폴더를 rmtree 후보로 삼는다.
        job.status = "done"
        job.progress = 100
        job.error = ("재굽기를 취소했습니다" if rebaking
                     else "다시 번역이 취소됐습니다")
    else:
        job.status = "cancelled"
        job.progress = 0
    await db.commit()
    return {"status": job.status}


@router.post("/{job_id}/rebake")
async def rebake_pdf(
    job_id: UUID, db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """계획 + 편집을 합성해 번역본만 다시 굽는다 — 번역기를 부르지 않는다."""
    job = await _get_job(db, job_id)
    if job.status != "done":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "아직 번역이 끝나지 않았습니다")
    if not job.source_path or not Path(job.source_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "원본 PDF가 없습니다")
    if load_plan(pdf_job_dir(job_id)) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "이 작업에는 편집 정보가 없습니다 — 다시 번역을 실행하세요")
    job.status = "overlaying"
    job.progress = 0
    job.error = None
    await db.commit()
    _start_rebake(job_id)
    return {"status": "overlaying"}


class _ManualBody(BaseModel):
    page: int
    panel_index: int
    rel: tuple[float, float]
    size: tuple[float, float] | None = None
    source_text: str = ""
    text: str
    fontsize: float = 10.0
    edits_version: int


class _PatchBody(BaseModel):
    text: str | None = None
    rect: tuple[float, float, float, float] | None = None
    edits_version: int


class _RepointBody(BaseModel):
    page: int
    panel_index: int
    rel: tuple[float, float] | None = None
    edits_version: int


class _VersionBody(BaseModel):
    edits_version: int


def _read_labels(job_id: UUID, source_path: str, fmt: str) -> dict:
    """합성 목록 — **요청당 문서를 최대 한 번만** 열고, 수동 라벨이 있을 때만 연다."""
    job_dir = pdf_job_dir(job_id)
    plan = load_plan(job_dir)
    edits = load_edits(job_dir, job_id=str(job_id))
    if plan is None:
        # 이 기능 이전에 만들어진 잡 — 계획이 없다. 편집 조작을 잠그고 안내한다.
        return {"items": [], "total": 0, "plan_missing": True, "stale": True,
                "edits_version": edits.edits_version,
                "dangling": [], "unresolved": []}
    profile = profile_by_name(fmt or "")
    doc = None
    try:
        if edits.manual and profile is not None:
            doc = open_pdf(Path(source_path))
            resolve = panels_resolver(doc, profile)
        else:
            def resolve(_page):
                return None
        composed = compose(plan, edits, resolve)
    finally:
        if doc is not None:
            doc.close()
    st = load_plan_status(job_dir) or {}
    return {
        "plan_missing": False,
        "edits_version": edits.edits_version,
        "stale": st.get("baked_edits_version") != edits.edits_version,
        "composed": composed,
    }


@router.get("/{job_id}/labels")
async def list_pdf_labels(
    job_id: UUID, db: Annotated[AsyncSession, Depends(get_session)],
    kind: str = _EDITABLE_KIND, page: int | None = None,
    q: str | None = None, offset: int = 0, limit: int = 200,
) -> dict:
    """합성 목록 + 무효 항목.

    무효 항목은 **개수가 아니라 목록**으로 싣는다(`page`·`text` 포함) — 개수만
    주면 사람이 무엇을 다시 입력해야 하는지 알 수 없어 "조용히 사라지지
    않는다"를 형식적으로만 만족한다.
    """
    job = await _get_job(db, job_id)
    if not job.source_path or not Path(job.source_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "원본 PDF가 없습니다")
    raw = await asyncio.to_thread(
        _read_labels, job_id, job.source_path, job.format or "")
    if raw.get("plan_missing"):
        return raw
    composed = raw.pop("composed")
    items = [{
        "id": p.item_id, "origin": p.origin, "kind": p.kind, "page": p.page,
        "panel_index": p.panel_index, "rect": list(p.rect),
        "fontsize": p.fontsize, "source_text": p.source_text,
        "text": p.text, "edited": p.edited,
        "editable": p.kind == _EDITABLE_KIND,
    } for p in composed.placed]
    if kind != "all":
        items = [i for i in items if i["kind"] == kind]
    if page is not None:
        items = [i for i in items if i["page"] == page]
    if q:
        needle = q.lower()
        items = [i for i in items
                 if needle in i["text"].lower()
                 or needle in i["source_text"].lower()]
    total = len(items)
    limit = max(1, min(limit, 500))
    return {**raw, "total": total,
            "items": items[offset:offset + limit],
            "dangling": [{"target": o.target, "page": o.page, "text": o.text}
                         for o in composed.dangling],
            "unresolved": [{"id": m.id, "page": m.page,
                            "panel_index": m.panel_index, "text": m.text}
                           for m in composed.unresolved]}


@router.post("/{job_id}/labels", status_code=status.HTTP_201_CREATED)
async def add_pdf_label(
    job_id: UUID, body: _ManualBody,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await _require_editable(db, job_id)
    if not body.text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "라벨 텍스트가 비어 있습니다")
    try:
        panels = await asyncio.to_thread(
            _page_panel_rects, job.source_path, job.format, body.page)
    except IndexError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "페이지 범위 밖입니다")
    if not (0 <= body.panel_index < len(panels)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "그 페이지에 없는 판넬 번호입니다")
    panel = panels[body.panel_index]
    size = body.size or (
        # 기본 크기: 판넬 폭의 40%, 높이는 줄 수 기준. 사람이 곧바로 드래그로
        # 다듬을 수 있으므로 "대충 맞는" 값이면 충분하다.
        round((panel[2] - panel[0]) * 0.4, 2),
        round(body.fontsize * (body.text.count("\n") + 1) * 1.25, 2))
    label = ManualLabel(
        id=uuid4().hex[:12], page=body.page, panel_index=body.panel_index,
        rel=body.rel, size=size, fontsize=body.fontsize,
        source_text=body.source_text, text=body.text,
        panel_rect=tuple(panel), address_rev=PANEL_ADDRESS_REV)
    try:
        updated = await asyncio.to_thread(
            _mutate_edits, job_id, body.edits_version,
            lambda e: add_manual(e, label))
    except _VersionConflict as conflict:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"다른 창에서 먼저 저장했습니다 (현재 {conflict.current})")
    return {"id": label.id, "edits_version": updated.edits_version}


def _rect_to_rel(rect, panel) -> tuple[float, float]:
    return (round((rect[0] - panel[0]) / max(1e-6, panel[2] - panel[0]), 4),
            round((rect[1] - panel[1]) / max(1e-6, panel[3] - panel[1]), 4))


@router.patch("/{job_id}/labels/{item_id}")
async def patch_pdf_label(
    job_id: UUID, item_id: str, body: _PatchBody,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """텍스트·위치 수정. **수동 라벨의 rect는 `rel`로 역변환해 저장한다** —
    절대 좌표를 저장하면 재번역 후에도 옛 자리에 찍혀 주소 재부착이 무너진다."""
    job = await _require_editable(db, job_id)
    job_dir = pdf_job_dir(job_id)
    edits = await asyncio.to_thread(load_edits, job_dir, job_id=str(job_id))
    manual = next((m for m in edits.manual if m.id == item_id), None)

    if manual is not None:
        rel = panel = None
        if body.rect is not None:
            try:
                panels = await asyncio.to_thread(
                    _page_panel_rects, job.source_path, job.format, manual.page)
            except IndexError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "페이지 범위 밖입니다")
            if not (0 <= manual.panel_index < len(panels)):
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "이 라벨은 주소를 잃었습니다 — 판넬 재지정을 쓰세요")
            panel = panels[manual.panel_index]
            rel = _rect_to_rel(body.rect, panel)
            if not (0.0 <= rel[0] <= 1.0 and 0.0 <= rel[1] <= 1.0):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    "판넬 밖으로 나갔습니다")
        mutate = (lambda e: patch_manual(e, item_id, text=body.text, rel=rel,
                                         panel_rect=None if panel is None
                                         else tuple(panel)))
    else:
        plan = await asyncio.to_thread(load_plan, job_dir)
        target = next((i for i in (plan.items if plan else [])
                       if i.id == item_id), None)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "라벨을 찾을 수 없습니다")
        if target.kind != _EDITABLE_KIND:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "이 종류는 아직 편집할 수 없습니다")
        if body.rect is not None and not is_usable_rect(
                body.rect, tuple(plan.page_sizes[target.page])):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "위치가 페이지를 벗어났습니다")
        mutate = (lambda e: upsert_override(e, item_id, page=target.page,
                                            text=body.text, rect=body.rect))
    try:
        updated = await asyncio.to_thread(
            _mutate_edits, job_id, body.edits_version, mutate)
    except _VersionConflict as conflict:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"다른 창에서 먼저 저장했습니다 (현재 {conflict.current})")
    return {"edits_version": updated.edits_version}


@router.patch("/{job_id}/labels/{item_id}/panel")
async def repoint_pdf_label(
    job_id: UUID, item_id: str, body: _RepointBody,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """주소를 잃은 수동 라벨을 사람이 직접 다른 판넬로 다시 붙인다."""
    job = await _require_editable(db, job_id)
    try:
        panels = await asyncio.to_thread(
            _page_panel_rects, job.source_path, job.format, body.page)
    except IndexError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "페이지 범위 밖입니다")
    if not (0 <= body.panel_index < len(panels)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "그 페이지에 없는 판넬 번호입니다")
    panel = tuple(panels[body.panel_index])
    try:
        updated = await asyncio.to_thread(
            _mutate_edits, job_id, body.edits_version,
            lambda e: repoint_manual(e, item_id, page=body.page,
                                     panel_index=body.panel_index,
                                     rel=body.rel, panel_rect=panel))
    except _VersionConflict as conflict:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"다른 창에서 먼저 저장했습니다 (현재 {conflict.current})")
    return {"edits_version": updated.edits_version}


@router.delete("/{job_id}/labels/{item_id}")
async def delete_pdf_label(
    job_id: UUID, item_id: str, body: _VersionBody,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """수동 = 레코드 제거, 자동 = `deleted` override(계획은 파이프라인 소유라
    사람이 지울 수 없다 — 대신 합성에서 빠진다)."""
    await _require_editable(db, job_id)
    job_dir = pdf_job_dir(job_id)
    edits = await asyncio.to_thread(load_edits, job_dir, job_id=str(job_id))
    if any(m.id == item_id for m in edits.manual):
        mutate = (lambda e: delete_manual(e, item_id))
    else:
        plan = await asyncio.to_thread(load_plan, job_dir)
        target = next((i for i in (plan.items if plan else [])
                       if i.id == item_id), None)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "라벨을 찾을 수 없습니다")
        if target.kind != _EDITABLE_KIND:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "이 종류는 아직 편집할 수 없습니다")
        mutate = (lambda e: upsert_override(e, item_id, page=target.page,
                                            deleted=True))
    try:
        updated = await asyncio.to_thread(
            _mutate_edits, job_id, body.edits_version, mutate)
    except _VersionConflict as conflict:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"다른 창에서 먼저 저장했습니다 (현재 {conflict.current})")
    return {"edits_version": updated.edits_version}


@router.post("/{job_id}/labels/purge-dangling")
async def purge_dangling_overrides(
    job_id: UUID, body: _VersionBody,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """계획에 없는 target을 겨냥한 override만 정리한다.

    **수동 라벨은 어떤 상태에서도 이 경로로 삭제되지 않는다** — 주소를 잃은
    수동 라벨은 판넬이 되돌아오면 자동 복귀하는데 여기서 지우면 영구 소멸한다.
    """
    await _require_editable(db, job_id)
    job_dir = pdf_job_dir(job_id)
    plan = await asyncio.to_thread(load_plan, job_dir)
    known = {i.id for i in (plan.items if plan else [])}
    try:
        updated = await asyncio.to_thread(
            _mutate_edits, job_id, body.edits_version,
            lambda e: purge_dangling(e, known))
    except _VersionConflict as conflict:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"다른 창에서 먼저 저장했습니다 (현재 {conflict.current})")
    return {"edits_version": updated.edits_version,
            "manual_count": len(updated.manual)}


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pdf_job(
    job_id: UUID, db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    job = await _get_job(db, job_id)
    cancel_pdf_task(job_id)
    await db.delete(job)
    await db.commit()
    shutil.rmtree(pdf_job_dir(job_id), ignore_errors=True)
