"""Video caption job endpoints.

The entire video captions API is deliberately UNAUTHENTICATED (product
decision 2026-07-06): this deployment treats the LAN as the trust boundary,
the same acceptance already made for viewer tokens. /media in particular
can never carry an Authorization header (HTML5 <video> cannot attach one),
so its unguessable job UUID acts as the capability URL — the other
endpoints extend that same trust decision rather than being a special case.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile, status)
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.ai.apple_native import APPLE_TRANSCRIBE_MODEL, apple_stt_available
from apps.server.db.models import AppUser, VideoJob, VideoSegment
from apps.server.db.session import get_session
from apps.server.domain.video_captions.ingest import save_upload
from apps.server.domain.video_captions.pipeline import (RETENTION_KEEP,
                                                        build_fingerprint_segments,
                                                        build_scene_data,
                                                        cancel_job_task, job_dir,
                                                        load_boundary_status,
                                                        load_export_status,
                                                        load_refine_status,
                                                        load_scenes,
                                                        prune_old_video_jobs,
                                                        run_boundary_check,
                                                        run_burn_job, run_scene_export,
                                                        run_scene_refine,
                                                        run_scene_scan,
                                                        run_scene_scan_fingerprint,
                                                        run_video_job,
                                                        save_boundary_status,
                                                        save_export_status,
                                                        save_refine_status,
                                                        save_scenes, start_job_task,
                                                        start_task, video_jobs_root)
from apps.server.domain.video_captions.pipeline import \
    _INFLIGHT_STATUSES as INFLIGHT_STATUSES
from apps.server.domain.video_captions.pipeline import \
    _DEFAULT_DELIMS as DEFAULT_DELIMS
from apps.server.domain.video_captions.pipeline import \
    _TOP_BAND_DEFAULT as TOP_BAND_DEFAULT
from apps.server.domain.video_captions.ffmpeg import (extract_frame,
                                                      extract_thumbnail_at,
                                                      locate_ffmpeg)
from apps.server.domain.video_captions.scene_split import FrameSample, tokenize
from apps.server.domain.video_captions.slate_ocr import (read_slate_line,
                                                         read_slate_line_rescaled)
from apps.server.domain.video_captions.slate_templates import (delete_template, list_templates, upsert_template)
from apps.server.domain.video_captions.srt import SubSegment, segments_to_srt
from apps.server.domain.video_captions.translate import (is_source_copy,
                                                         is_untranslated,
                                                         maybe_aclose_translator,
                                                         translate_segments)
from apps.server.domain.video_captions.translate_cli import (create_translator,
                                                             list_translate_engines)
from apps.server.domain.video_captions.whisper_models import get_catalog, is_downloaded

router = APIRouter(tags=["video-jobs"], prefix="/video-jobs")

# 익스포트 정리(scene_export_cleanup)가 이미 logger를 쓰고 있었는데 모듈에 정의가
# 없어, 삭제 실패라는 드문 경로에서 NameError로 500이 됐다.
logger = logging.getLogger(__name__)


def _start_pipeline(external_id: UUID) -> None:  # test seam
    start_job_task(external_id, run_video_job(external_id))


def _start_burn(external_id: UUID, position: str, margin_v: int,
                font_size: int, color: str) -> None:  # test seam
    start_job_task(external_id,
                   run_burn_job(external_id, position, margin_v, font_size, color))


def _start_scene_scan(external_id: UUID, interval_s: float) -> None:  # test seam
    start_job_task(external_id, run_scene_scan(external_id, interval_s))


def _start_scene_scan_fingerprint(external_id: UUID) -> None:  # test seam
    start_job_task(external_id, run_scene_scan_fingerprint(external_id))


def _start_scene_export(external_id: UUID, mode: str, out_dir: str | None,
                        indices: list[int] | None = None) -> None:  # test seam
    start_job_task(external_id, run_scene_export(external_id, mode, out_dir, indices))


def _start_scene_refine(external_id: UUID, mode: str) -> None:  # test seam
    start_job_task(external_id, run_scene_refine(external_id, mode))


def _start_boundary_check(external_id: UUID) -> None:  # test seam
    start_job_task(external_id, run_boundary_check(external_id))


def _prune_old_jobs() -> None:  # test seam
    # 새 작업이 생길 때마다 최근 RETENTION_KEEP개만 유지 (개수 상한 정책). 응답을
    # 막지 않도록 fire-and-forget — 방금 만든 작업은 in-flight라 삭제 대상 제외.
    start_task(prune_old_video_jobs())


# 번역 provider 검증 패턴은 list_translate_engines()가 노출하는 값에서 자동 도출한다.
# 하드코딩하면 엔진 추가 때 갱신을 빠뜨려 작업 생성이 422로 막힌다(qwen 계열이 그 사례,
# 2026-07-14) — 엔진 목록이 유일한 출처가 되도록 파생시켜 드리프트를 원천 차단한다.
_TRANSLATE_PROVIDER_PATTERN = "^(" + "|".join(
    e["value"] for e in list_translate_engines()) + ")$"


class VideoJobCreateIn(BaseModel):
    youtube_url: str
    whisper_model: str
    title: str | None = None
    translate_provider: str | None = Field(
        default=None, pattern=_TRANSLATE_PROVIDER_PATTERN)
    translate_cli_model: str | None = None


class BurnIn(BaseModel):
    position: str = Field(pattern="^(bottom|top)$")
    margin_v: int = Field(ge=0, le=300)
    font_size: int = Field(ge=8, le=72)
    color: str = Field(default="#FFFFFF", pattern="^#[0-9a-fA-F]{6}$")


class OcrRegionIn(BaseModel):
    """슬레이트 구역(프레임 대비 비율). 쇼마다 위치가 달라 사용자가 드래그로
    지정한다. 비율이라 해상도가 달라도 같은 값을 쓸 수 있다."""
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)


class SlateRuleIn(BaseModel):
    delimiters: list[str] = Field(default_factory=lambda: ["_", "-"])
    seq_tokens: list[int]
    scene_tokens: list[int] = Field(default_factory=list)
    # 최소 씬 길이(ms) — 이보다 짧은 구간은 OCR 오독 튐으로 보고 직전에 흡수한다.
    # None이면 샘플 간격에 비례한 값을 자동 적용한다(고정 2000ms는 촘촘한 스캔에서
    # 진짜 짧은 씬을 삼켰다). 사용자가 명시하면 그 값을 쓴다.
    min_ms: int | None = Field(default=None, ge=0, le=60000)


class SegmentOverride(BaseModel):
    label: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


class SegmentsOverrideIn(BaseModel):
    mode: str = Field(pattern="^(scene|sequence)$")
    segments: list[SegmentOverride]


class SceneExportIn(BaseModel):
    mode: str = Field(pattern="^(scene|sequence)$")
    out_dir: str | None = None
    # 부분 익스포트 — 다시 구울 세그먼트 인덱스. None이면 전체(기존 동작). 경계를 고친
    # 씬 하나(+맞닿은 이웃)만 재인코딩해 수백 개를 다시 굽지 않게 한다.
    indices: list[int] | None = None


class SceneExportProbeIn(BaseModel):
    # dir는 서버 로컬 경로 문자열이다 — 기존 SceneExportIn.out_dir과 신뢰 경계가 같다
    # (LAN을 신뢰 경계로 두는 이 API의 전제, 파일 상단 주석 참조).
    dir: str = Field(min_length=1)
    token: str = Field(min_length=8, max_length=64, pattern="^[0-9a-f]+$")


class BoundaryOkItem(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


class BoundaryOkIn(BaseModel):
    items: list[BoundaryOkItem]


def _iso_utc(value: datetime | None) -> str | None:
    """NAIVE UTC 저장 관례 → tz 붙여 직렬화 (sessions.py _serialize_utc와 동일 이유 —
    tz 없이 내보내면 클라 new Date가 로컬로 오해해 UTC 오프셋만큼 어긋난다)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _job_out(job: VideoJob) -> dict:
    return {
        "job_id": str(job.external_id),
        "title": job.title,
        "source_type": job.source_type,
        "source_ref": job.source_ref,
        "whisper_model": job.whisper_model,
        "translate_provider": job.translate_provider,
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
        "created_at": _iso_utc(job.created_at),
    }


def _require_model(name: str) -> None:
    if name == APPLE_TRANSCRIBE_MODEL:
        if not apple_stt_available():
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Apple 온디바이스 전사는 실리콘맥(macOS 26+) 서버에서만 사용할 수 있습니다.")
        return
    if name not in get_catalog():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown whisper model")
    if not is_downloaded(name):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"whisper 모델 '{name}'이 설치되어 있지 않습니다. 먼저 다운로드하세요.")


async def _get_job_or_404(db: AsyncSession, external_id: UUID) -> VideoJob:
    job = (await db.execute(
        select(VideoJob).where(VideoJob.external_id == external_id)
    )).scalar_one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "video job not found")
    return job


async def _default_owner_id(db: AsyncSession) -> int:
    """무인증 개방 후 VideoJob.owner_user_id(NOT NULL FK) 채우기용 — 시스템의
    첫 사용자를 소유자로 해석한다. 서버 콘솔 온보딩이 항상 계정을 먼저
    만들므로 실질적으로 503에 도달하지 않는다."""
    owner_id = (await db.execute(
        select(AppUser.id).order_by(AppUser.id).limit(1)
    )).scalar_one_or_none()
    if owner_id is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "운영자 계정이 아직 없습니다 — 서버 콘솔에서 계정을 먼저 만드세요")
    return owner_id


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_video_job(
    body: VideoJobCreateIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require_model(body.whisper_model)
    owner_id = await _default_owner_id(db)
    job = VideoJob(external_id=uuid4(), owner_user_id=owner_id,
                   title=body.title or body.youtube_url, source_type="youtube",
                   source_ref=body.youtube_url, whisper_model=body.whisper_model,
                   translate_provider=body.translate_provider,
                   translate_cli_model=body.translate_cli_model,
                   status="queued")
    db.add(job)
    await db.commit()
    _prune_old_jobs()
    _start_pipeline(job.external_id)
    return {"job_id": str(job.external_id)}


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def create_upload_job(
    db: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    whisper_model: Annotated[str, Form()],
    title: Annotated[str | None, Form()] = None,
    translate_provider: Annotated[
        str | None, Form(pattern=_TRANSLATE_PROVIDER_PATTERN)] = None,
    translate_cli_model: Annotated[str | None, Form()] = None,
) -> dict:
    _require_model(whisper_model)
    external_id = uuid4()
    filename = file.filename or "upload.mp4"
    suffix = Path(filename).suffix or ".mp4"
    dest = job_dir(external_id) / f"source{suffix}"
    try:
        await save_upload(file, dest)
        owner_id = await _default_owner_id(db)
        job = VideoJob(external_id=external_id, owner_user_id=owner_id,
                       title=title or filename, source_type="upload",
                       source_ref=filename, whisper_model=whisper_model,
                       translate_provider=translate_provider,
                       translate_cli_model=translate_cli_model,
                       status="queued", media_path=str(dest))
        db.add(job)
        await db.commit()
    except Exception:
        # 실패 시 방금 쓴 파일/디렉터리 정리 — DB 행 없는 고아 파일 방지
        shutil.rmtree(job_dir(external_id), ignore_errors=True)
        raise
    _prune_old_jobs()
    _start_pipeline(external_id)
    return {"job_id": str(external_id)}


def _tree_size(root: Path) -> int:
    """디렉토리 트리의 총 바이트(동기 blocking I/O). 파일 수만 개(씬 스캔이 만드는
    수백 썸네일 등) + AV(실기 Kaspersky)가 매 stat을 가로채는 환경에선 수 초가
    걸릴 수 있다 — async 핸들러에서 직접 돌리면 이벤트 루프를 막아, 폴링 중인 다른
    요청들이 DB 커넥션을 쥔 채 정지→풀 고갈(QueuePool timeout)→앱 얼음을 유발한다.
    반드시 asyncio.to_thread로 감싸 호출할 것. 스캔 중 사라진 파일(동시 프루닝)은 무시."""
    total = 0
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
    return total


def _job_dir_size(external_id: UUID | str) -> int:
    """작업 폴더의 총 바이트. pathlib만 사용 — Windows/POSIX 공통."""
    return _tree_size(job_dir(external_id))


@router.get("")
async def list_video_jobs(
    db: Annotated[AsyncSession, Depends(get_session)],
    with_sizes: Annotated[bool, Query()] = False,
) -> dict:
    # with_sizes는 작업별 폴더 용량을 스캔한다 — 서버 콘솔 관리 패널 전용 옵트인.
    # 클라이언트의 3초 폴링 핫패스는 기본값(False)이라 스캔 비용을 지지 않는다.
    # created_at은 초 단위라 일괄 업로드 작업들이 같은 값으로 묶여 순서가 섞인다
    # → id를 타이브레이커로 생성 역순을 안정화(2026-07-08 Windows 실기기 정렬 버그).
    jobs = (await db.execute(
        select(VideoJob)
        .order_by(VideoJob.created_at.desc(), VideoJob.id.desc())
        .limit(100)
    )).scalars().all()
    items = []
    for job in jobs:
        out = _job_out(job)
        if with_sizes:
            # 디스크 트리 walk는 blocking — 이벤트 루프를 막지 않게 스레드로 돌린다.
            out["size_bytes"] = await asyncio.to_thread(
                _job_dir_size, job.external_id)
        items.append(out)
    return {"items": items}


class SlateTemplateIn(BaseModel):
    """쇼 템플릿 — 슬레이트 구역 + 토큰 규칙. 같은 쇼는 에피소드가 바뀌어도
    슬레이트 위치와 포맷이 같으므로 한 벌로 묶어 저장한다."""
    name: str = Field(min_length=1, max_length=80)
    region: OcrRegionIn
    delimiters: list[str] = Field(default_factory=lambda: ["_", "-"])
    seq_tokens: list[int] = Field(default_factory=list)
    scene_tokens: list[int] = Field(default_factory=list)
    # 샘플 간격도 쇼 특성(컷 밀도)이라 템플릿에 함께 저장한다.
    scan_interval_s: float = Field(default=2.0, ge=0.1, le=5.0)
    # 스캔 방식(간격/지문)도 쇼 단위로 정해지는 값이라 같이 저장한다.
    method: str = Field(default="interval", pattern="^(interval|fingerprint)$")


@router.get("/slate-templates")
async def get_slate_templates() -> dict:
    # /{external_id}보다 먼저 선언 — 선언 순서 매칭이라 뒤에 두면 UUID 파싱 422.
    return {"templates": list_templates()}


@router.post("/slate-templates")
async def save_slate_template(body: SlateTemplateIn) -> dict:
    return {"templates": upsert_template(body.model_dump())}


@router.delete("/slate-templates/{name}")
async def remove_slate_template(name: str) -> dict:
    if not delete_template(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "템플릿을 찾을 수 없습니다.")
    return {"templates": list_templates()}


@router.get("/translate-engines")
async def get_translate_engines() -> dict:
    # 반드시 /{external_id} 동적 라우트보다 먼저 선언 — 선언 순서 매칭이라
    # 뒤에 두면 "translate-engines"가 UUID로 파싱 시도되어 422가 난다.
    return {"engines": list_translate_engines()}


@router.get("/storage")
async def get_storage_usage(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    # 반드시 /{external_id} 동적 라우트보다 먼저 선언 — translate-engines와 동일 이유.
    # 콘솔이 3초마다 폴링하는 핫패스 — 디스크 트리 walk(_tree_size)를 async 핸들러에서
    # 직접 돌리면 이벤트 루프를 막아 다른 요청이 DB 커넥션을 쥔 채 정지→풀 고갈→앱
    # 얼음(실기 로그: QueuePool timeout 30). 반드시 스레드로 오프로드한다.
    total = await asyncio.to_thread(_tree_size, video_jobs_root())
    count = (await db.execute(
        select(func.count()).select_from(VideoJob))).scalar_one()
    return {"total_bytes": total, "job_count": count, "keep": RETENTION_KEEP}


_CANCEL_MESSAGE = "사용자가 작업을 취소했습니다."


@router.post("/cancel-all")
async def cancel_all_video_jobs(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """대기열의 모든 queued 작업을 먼저 취소한 뒤 활성(실행 중) 작업을 취소한다.

    반드시 이 순서로: 활성 작업을 먼저 취소하면 세마포어가 즉시 반납되고,
    아직 큐잉된 다음 작업이 그 사이 승격돼 취소를 피할 수 있다 — queued를
    먼저 확정 취소해두면 승격되더라도 이미 취소된 상태라 안전하다.
    반드시 /{external_id} 동적 라우트보다 먼저 선언 — translate-engines/storage와 동일 이유.
    """
    queued_jobs = (await db.execute(
        select(VideoJob).where(VideoJob.status == "queued")
    )).scalars().all()
    for job in queued_jobs:
        cancel_job_task(job.external_id)
        job.status = "cancelled"
        job.progress = 0
        job.error = _CANCEL_MESSAGE
    cancelled_queued = len(queued_jobs)

    active_statuses = [s for s in INFLIGHT_STATUSES if s != "queued"]
    active_jobs = (await db.execute(
        select(VideoJob).where(VideoJob.status.in_(active_statuses))
    )).scalars().all()
    for job in active_jobs:
        cancel_job_task(job.external_id)
        job.status = "cancelled"
        job.progress = 0
        job.error = _CANCEL_MESSAGE
    cancelled_active = len(active_jobs)

    await db.commit()
    return {"cancelled_queued": cancelled_queued, "cancelled_active": cancelled_active}


@router.get("/{external_id}")
async def get_video_job(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await _get_job_or_404(db, external_id)
    rows = (await db.execute(
        select(VideoSegment).where(VideoSegment.job_id == job.id)
        .order_by(VideoSegment.seq)
    )).scalars().all()
    out = _job_out(job)
    out["segments"] = [{"seq": r.seq, "start_ms": r.start_ms, "end_ms": r.end_ms,
                        "text_en": r.text_en, "text_ko": r.text_ko} for r in rows]
    return out


class SegmentEdit(BaseModel):
    seq: int
    text_ko: str


class SegmentsPatchIn(BaseModel):
    edits: list[SegmentEdit]


@router.patch("/{external_id}/segments")
async def patch_segments(
    external_id: UUID,
    body: SegmentsPatchIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await _get_job_or_404(db, external_id)
    by_seq = {e.seq: e.text_ko for e in body.edits}
    rows = (await db.execute(
        select(VideoSegment).where(VideoSegment.job_id == job.id,
                                   VideoSegment.seq.in_(list(by_seq)))
    )).scalars().all()
    for row in rows:
        row.text_ko = by_seq[row.seq]
    await db.commit()
    return {"updated": len(rows)}


@router.post("/{external_id}/burn", status_code=status.HTTP_202_ACCEPTED)
async def burn_video_job(
    external_id: UUID,
    body: BurnIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await _get_job_or_404(db, external_id)
    # cancelled 포함 — 굽기 도중 취소한 작업은 세그먼트가 이미 있으므로
    # (error에서의 재굽기와 동일하게) 전체 재생성 없이 다시 구울 수 있어야 한다.
    if job.status not in ("review", "done", "error", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"검수 가능한 상태가 아닙니다 (status={job.status})")
    job.status = "burning"
    job.progress = 0
    job.error = None
    await db.commit()
    _start_burn(external_id, body.position, body.margin_v, body.font_size, body.color)
    return {"status": "burning"}


@router.post("/{external_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_video_job(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """진행 중 파이프라인 중단 — 행은 남겨 재생성/삭제 선택지를 준다.

    취소는 베스트에포트: 태스크 cancel은 다음 await 지점에서 멎고 세마포어가
    즉시 반납된다(삭제 경로와 동일 semantics). 상태는 실패(error)와 구분되는
    'cancelled'로 초기화해 재생성 버튼의 대상이 되게 한다.
    """
    job = await _get_job_or_404(db, external_id)
    if job.status in ("review", "done", "error", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"이미 종료된 작업입니다 (status={job.status})")
    cancel_job_task(external_id)
    job.status = "cancelled"
    job.progress = 0
    job.error = _CANCEL_MESSAGE
    await db.commit()
    return {"status": "canceled"}


@router.post("/{external_id}/rebuild", status_code=status.HTTP_202_ACCEPTED)
async def rebuild_video_job(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """같은 소스·같은 옵션으로 파이프라인 재실행(제자리 재생성).

    대용량 원본을 다시 업로드하지 않고 전사/번역을 다시 돌리는 용도(모델·
    프롬프트 수정 후 재작업 등). run_video_job이 세그먼트를 선삭제 후 재삽입
    하므로 별도 정리는 상태 리셋만으로 충분하다. 기존 검수 편집과 굽기
    결과는 폐기된다.
    """
    job = await _get_job_or_404(db, external_id)
    if job.status not in ("review", "done", "error", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"진행 중인 작업은 재생성할 수 없습니다 (status={job.status})")
    if job.source_type == "upload" and (
            not job.media_path or not Path(job.media_path).exists()):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "원본 영상 파일이 남아 있지 않아 재생성할 수 없습니다.")
    cancel_job_task(external_id)  # 방어적 — 터미널 상태면 실행 중 태스크 없음
    job.status = "queued"
    job.progress = 0
    job.error = None
    job.burned_path = None  # 새 검수 전까지 옛 굽기 결과 다운로드 방지
    await db.commit()
    _start_pipeline(external_id)
    return {"status": "queued"}


class RetranslateIn(BaseModel):
    provider: str = Field(pattern=_TRANSLATE_PROVIDER_PATTERN)
    cli_model: str | None = None


@router.post("/{external_id}/retranslate")
async def retranslate_video_job(
    external_id: UUID,
    body: RetranslateIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """영문으로 남은 세그먼트만 골라 지정 엔진으로 다시 번역한다.

    대상은 is_source_copy(원문을 그대로 복사한 줄)뿐이다 — 사용자가 손댄 줄은
    정의상 text_ko != text_en이라 절대 덮어쓰지 않는다. 검수 편집을 통째로
    폐기하는 rebuild와 다른 점이다. is_untranslated(english_leak 포함)를 대상
    선정에 쓰면 이 안전 속성이 깨진다 — 사후 확인에만 쓴다.
    번역은 translate_segments를 거쳐 글로서리 보정·청킹을 그대로 탄다.
    """
    job = await _get_job_or_404(db, external_id)
    if job.status not in ("review", "done", "error", "cancelled"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"진행 중인 작업은 재번역할 수 없습니다 (status={job.status})")
    engine = next(
        (e for e in list_translate_engines() if e["value"] == body.provider), None)
    if engine is None or not engine["available"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"이 서버에서 사용할 수 없는 번역 엔진입니다: {body.provider}")

    rows = (await db.execute(
        select(VideoSegment)
        .where(VideoSegment.job_id == job.id)
        .order_by(VideoSegment.seq)
    )).scalars().all()
    # 대상 선정은 is_source_copy만 — is_untranslated를 쓰면 사용자가 일부러
    # 영문으로 남긴 편집을 덮어쓴다.
    targets = [r for r in rows if is_source_copy(r.text_en, r.text_ko)]
    if not targets:
        return {"total": 0, "retranslated": 0, "remaining": 0}

    translator = create_translator(body.provider, body.cli_model)
    try:
        out = await translate_segments(
            [SubSegment(seq=r.seq, start_ms=r.start_ms, end_ms=r.end_ms,
                        text=r.text_en) for r in targets],
            translator,
        )
    finally:
        await maybe_aclose_translator(translator)

    by_seq = {s.seq: s.text for s in out}
    retranslated = 0
    for row in targets:
        ko = by_seq.get(row.seq)
        # 사후 확인은 is_untranslated(english_leak 포함) — 방금 모델이 뱉은
        # 출력을 보는 것이라 안전 문제가 없고, 영어면 저장하지 않고 remaining으로
        # 보고해 카운트를 정직하게 만든다.
        if ko is None or is_untranslated(row.text_en, ko):
            continue
        row.text_ko = ko
        retranslated += 1
    await db.commit()
    return {"total": len(targets), "retranslated": retranslated,
            "remaining": len(targets) - retranslated}


class ScanIn(BaseModel):
    # 샘플 간격(초). 짧은 씬(2초 미만)이 많은 애니메틱은 촘촘하게(0.25s) 떠야
    # 놓치지 않는다 — 크롭 OCR이 빨라져 0.25s도 25분 영상 ~7분이면 끝난다.
    interval_s: float = Field(default=2.0, ge=0.1, le=5.0)
    # 스캔 방식 — interval(간격 OCR 샘플링+정밀화, 기존)과 fingerprint(전 프레임
    # 지문 컷 감지, 프레임 정확·정밀화 불필요)를 나란히 둔다. 지문에 리스크
    # (가짜 컷 등)가 보이면 기존 방식으로 폴백할 수 있어야 한다(사용자 결정).
    method: str = Field(default="interval", pattern="^(interval|fingerprint)$")


@router.post("/{external_id}/scenes/scan", status_code=status.HTTP_202_ACCEPTED)
async def scan_scenes(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
    body: ScanIn | None = None,
) -> dict:
    job = await _get_job_or_404(db, external_id)
    if job.status != "done" or not job.burned_path or not Path(job.burned_path).exists():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "씬 분할은 굽기 완료(done)된 작업에서만 가능합니다.")
    scan_in = body or ScanIn()
    # 초기 scanning 상태를 동기 기록(익스포트/정밀화와 같은 패턴) — 프레임 추출
    # (수 분) 동안 옛 scanned 데이터가 남아 있으면 재스캔 폴링이 '스캔 완료
    # (옛 데이터)'로 오판한다. 구역은 재스캔에도 유지되게 되싣고, method도 실어
    # 재진입 폴링이 방식을 안다.
    prev = load_scenes(external_id) or {}
    save_scenes(external_id, {"scanning": True, "total_frames": 0,
                              "ocr_done": 0, "frames": [],
                              "method": scan_in.method,
                              "ocr_region": prev.get("ocr_region")})
    if scan_in.method == "fingerprint":
        _start_scene_scan_fingerprint(external_id)
    else:
        _start_scene_scan(external_id, scan_in.interval_s)
    return {"status": "scanning"}


@router.get("/{external_id}/scenes")
async def get_scenes(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _get_job_or_404(db, external_id)
    data = load_scenes(external_id)
    if not data:
        return {"scanned": False, "scanning": False, "error": None,
                "ocr_done": 0, "total_frames": 0, "frames": [],
                "segments_scene": [], "segments_sequence": [], "rule": None,
                "boundary_ok": []}
    # scanned = 스캔 진행중 아님 + 에러 없음(+frames 확정). 진행중이면 프론트가
    # ocr_done/total_frames로 진척을 표시하고, error면 폴링을 멈춘다.
    scanning = bool(data.get("scanning"))
    error = data.get("error")
    return {
        "scanned": (not scanning) and error is None and "frames" in data,
        "scanning": scanning,
        "error": error,
        "ocr_done": data.get("ocr_done", 0),
        "total_frames": data.get("total_frames", 0),
        # 판독 카운터가 아직 없는 앞 구간(크롭·추출·컷감지)의 단계 이름과
        # 살아있음 신호. 프론트는 stage_tick 변화를 진척으로 보고 정체 판정을
        # 리셋한다 — 없으면 멀쩡한 스캔이 200초 뒤 실패로 뜬다.
        "stage": data.get("stage"),
        "stage_tick": data.get("stage_tick", 0),
        "frames": data.get("frames", []),
        "segments_scene": data.get("segments_scene", []),
        "segments_sequence": data.get("segments_sequence", []),
        "rule": data.get("rule"),
        "interval_ms": data.get("interval_ms", 2000),
        # 썸네일은 스캔 간격과 분리(성기게) — 필름스트립 격자 계산은 이 값을 쓴다.
        "thumb_interval_ms": data.get("thumb_interval_ms",
                                      data.get("interval_ms", 2000)),
        "thumb_count": data.get("thumb_count", len(data.get("frames", []))),
        "ocr_region": data.get("ocr_region"),
        # 스캔 방식 — 프론트가 정밀화 단계 표시/생략을 이 값으로 가른다.
        "method": data.get("method", "interval"),
        # 지문 스캔의 영상 전체 길이(마지막 런 끝) — 간격 방식은 프레임 격자로
        # 길이를 유도하지만 지문 런은 격자가 없어 명시 값이 필요하다.
        "total_ms": data.get("total_ms"),
        # 측정 fps — 머리·꼬리 검수 팝업이 경계 프레임을 프레임 단위로 시킹하는 데
        # 쓴다. 24 가정은 경계에서 인덱스를 1 어긋내므로 측정값을 그대로 내려준다.
        "video_fps": data.get("video_fps"),
        # 경계 오류(혼입) 검사 결과 — 씬 모드 세그먼트 중 머리/꼬리 프레임에 이웃
        # 슬레이트가 잡힌 구간. 프론트 '⚠ 경계 오류' 필터 탭이 이 인덱스를 쓴다.
        "boundary_issues": data.get("boundary_issues", []),
        # 사용자가 '문제없음'으로 확인한 구간(라벨 + 확인 당시 경계). 프론트가
        # 경계오류 탭에서 제외하되, 경계가 그 뒤에 바뀌었으면 무시한다.
        "boundary_ok": data.get("boundary_ok", []),
    }


@router.post("/{external_id}/scenes/rule")
async def set_scene_rule(
    external_id: UUID,
    body: SlateRuleIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _get_job_or_404(db, external_id)
    data = load_scenes(external_id)
    if not data or not data.get("frames"):
        raise HTTPException(status.HTTP_409_CONFLICT, "먼저 씬 스캔을 실행하세요.")
    if data.get("method") == "fingerprint":
        # 지문 스캔: 경계는 이미 프레임 정확한 컷(런) — 규칙은 런들을 같은 키로
        # 병합하는 데만 쓴다(min_ms 흡수·중앙정렬·정밀화 없음). data를 update로
        # 고쳐 method·runs·ocr_region·total_ms를 보존한다(규칙 재확정 가능해야).
        rule_dict = body.model_dump()
        segs = build_fingerprint_segments(data.get("runs") or [], rule_dict)
        data.update(segs)
        data["rule"] = rule_dict
        save_scenes(external_id, data)
        return {"segments_scene": data["segments_scene"],
                "segments_sequence": data["segments_sequence"],
                "rule": rule_dict}
    interval_ms = data.get("interval_ms", 1000)
    samples = [FrameSample(index=i, t_ms=f["t_ms"], text=f.get("text", ""))
               for i, f in enumerate(data["frames"])]
    total_ms = (samples[-1].t_ms + interval_ms) if samples else 0
    rule_dict = body.model_dump()
    # min_ms 미지정이면 간격 비례 자동값 — 1샘플 오독 튐(≈1 interval)은 흡수하되
    # 2샘플 이상 진짜 씬은 남기도록 1.5×interval. 이러면 0.25초 스캔에서 0.5초
    # 이상 씬이 살아남는다(실기 0050=0.75초). rule_dict에도 실제 쓴 값을 남긴다.
    min_ms = body.min_ms if body.min_ms is not None else round(1.5 * interval_ms)
    rule_dict["min_ms"] = min_ms
    scene_data = build_scene_data(samples, rule_dict, total_ms, min_ms)
    scene_data["interval_ms"] = interval_ms
    # 사용자가 지정한 OCR 구역은 계산 산출물이 아니라 그 작품의 설정이다 —
    # build_scene_data가 만든 새 dict에 되실어야 경계 계산으로 지워지지 않는다
    # (지워지면 다음 스캔·정밀화가 전체 프레임을 훑어 느려지고, 쇼에 따라서는
    # 판독 자체가 실패한다).
    scene_data["ocr_region"] = data.get("ocr_region")
    save_scenes(external_id, scene_data)
    return {"segments_scene": scene_data["segments_scene"],
            "segments_sequence": scene_data["segments_sequence"],
            "rule": rule_dict}


@router.patch("/{external_id}/scenes/segments")
async def override_scene_segments(
    external_id: UUID,
    body: SegmentsOverrideIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _get_job_or_404(db, external_id)
    data = load_scenes(external_id)
    if not data:
        raise HTTPException(status.HTTP_409_CONFLICT, "먼저 씬 스캔을 실행하세요.")
    key = "segments_sequence" if body.mode == "sequence" else "segments_scene"
    data[key] = [s.model_dump() for s in body.segments]
    save_scenes(external_id, data)
    return {"updated": True}


@router.post("/{external_id}/scenes/export", status_code=status.HTTP_202_ACCEPTED)
async def export_scenes(
    external_id: UUID,
    body: SceneExportIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await _get_job_or_404(db, external_id)
    if job.status != "done":
        raise HTTPException(status.HTTP_409_CONFLICT, "완료된 작업만 익스포트할 수 있습니다.")
    data = load_scenes(external_id)
    key = "segments_sequence" if body.mode == "sequence" else "segments_scene"
    if not data or not (data.get(key) or []):
        raise HTTPException(status.HTTP_409_CONFLICT, "자를 세그먼트가 없습니다 — 규칙을 확정하세요.")
    segments = data[key]
    indices = body.indices
    if indices is not None:
        # 목록이 어긋난 채 엉뚱한 씬을 덮어쓰는 게 최악의 결과다 — 범위를 벗어나면
        # 자르지 않고 거부하고, 클라가 목록을 다시 불러오게 한다.
        if not indices or any(i < 0 or i >= len(segments) for i in indices):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "익스포트할 씬 번호가 목록과 맞지 않습니다 — 씬 목록을 다시 불러오세요.")
        indices = sorted(set(indices))
    count = len(indices) if indices is not None else len(segments)
    # 초기 상태를 동기로 기록 — 프론트 폴링이 202 직후부터 진행바를 표시하게.
    save_export_status(external_id, {"exporting": True, "done": 0, "total": count,
                                     "out_dir": body.out_dir, "error": None})
    _start_scene_export(external_id, body.mode, body.out_dir, indices)
    return {"status": "exporting", "count": count}


@router.get("/{external_id}/scenes/export/status")
async def scene_export_status(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _get_job_or_404(db, external_id)
    st = load_export_status(external_id)
    if not st:
        return {"exporting": False, "done": 0, "total": 0, "error": None,
                "out_dir": None, "files": []}
    return {"exporting": bool(st.get("exporting")), "done": st.get("done", 0),
            "total": st.get("total", 0), "error": st.get("error"),
            "out_dir": st.get("out_dir"), "files": st.get("files", [])}


# 탐침 파일 이름 접두사 — 클라 sceneSplitLogic.ts probeFileName, Rust PROBE_PREFIX와
# 같은 계약. 한쪽만 바꾸면 탐침이 조용히 실패해 같은 PC에서도 중계로 떨어진다.
_PROBE_PREFIX = "yeson_probe_"


@router.post("/{external_id}/scenes/export/probe")
async def scene_export_probe(
    external_id: UUID,
    body: SceneExportProbeIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """서버가 이 폴더에 직접 구워도 되는지 확인한다.

    서버·클라가 같은 PC면 중계(서버가 굽고 → 클라가 HTTP로 받아 쓰고 → 서버 사본
    삭제)가 통째로 낭비다: 같은 바이트를 디스크에 두 번 쓰고, 굽기가 다 끝난 뒤에야
    복사가 시작된다.

    그렇다고 호스트명 따위로 '같은 PC냐'를 추측하면 v1.7.3에서 고친 실패가 되살아난다
    — 서버 디스크에만 파일이 생기고 사용자가 고른 폴더는 끝까지 빈 채로 남는데 에러도
    안 나던 그 버그(실기 윈도우). 그래서 추측하지 않고 증명한다.

    두 가지를 함께 본다. ①클라가 방금 쓴 토큰 파일이 이 경로에서 읽히는가(같은 폴더인가)
    ②서버가 거기에 쓸 수 있는가. 같은 PC여도 ②가 거짓일 수 있다(macOS TCC — 서버 앱은
    클라와 다른 번들이다; 윈도우 제어된 폴더 액세스). 반대로 다른 PC라도 공유 폴더를
    고르면 둘 다 참이고, 그때는 전송 한 번을 통째로 아낀다.

    서버가 쓴 파일은 서버가 지운다 — 어느 경로로 끝나도 잔여물이 없다.
    """
    await _get_job_or_404(db, external_id)
    dest = Path(body.dir)
    # 폴더를 만들지 않는다 — 서버에 빈 폴더만 생기던 그 실패를 재현하지 않기 위해서다.
    if not dest.is_dir():
        return {"direct": False, "reason": "not_a_dir"}

    mine = dest / f"{_PROBE_PREFIX}{body.token}.tmp"
    # 방금 만들어진 파일이 백신 검사나 SMB 음성 캐싱으로 잠깐 안 보일 수 있다.
    for attempt in range(3):
        if attempt:
            await asyncio.sleep(0.3)
        try:
            if mine.read_text(encoding="utf-8").strip() == body.token:
                break
        except OSError:
            continue
    else:
        return {"direct": False, "reason": "token_mismatch"}

    ack = dest / f"{_PROBE_PREFIX}ack_{body.token}.tmp"
    try:
        ack.write_text(body.token, encoding="utf-8")
        if ack.read_text(encoding="utf-8") != body.token:
            return {"direct": False, "reason": "write_denied"}
    except OSError:
        logger.info("scene export probe: 서버가 %s 에 쓸 수 없다 — 중계로 간다", dest)
        return {"direct": False, "reason": "write_denied"}
    finally:
        try:
            ack.unlink(missing_ok=True)
        except OSError:
            logger.exception("탐침 ack 파일 삭제 실패: %s", ack)
    return {"direct": True, "reason": "ok"}


@router.get("/{external_id}/scenes/export/file")
async def scene_export_file(
    external_id: UUID,
    name: Annotated[str, Query(min_length=1, max_length=255)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    """익스포트한 클립 하나를 내려준다 — 클라이언트가 사용자가 고른 로컬 폴더에
    저장한다.

    저장 폴더 선택창은 클라 PC에서 뜨는데 자르기·쓰기는 서버가 한다(원본과
    ffmpeg가 서버에 있다). 두 PC가 다르면 서버 디스크에 그 경로가 새로 생기고
    사용자가 고른 폴더는 영영 비어 있었다(실기 윈도우). 서버는 자기 폴더에 굽고
    클라가 이 통로로 받아 쓴다.

    이 잡이 실제로 익스포트한 파일 목록 안에서만 고른다 — 이름을 경로로 해석하지
    않으므로 조작으로 서버의 다른 파일을 읽어갈 수 없다.
    """
    await _get_job_or_404(db, external_id)
    st = load_export_status(external_id) or {}
    for entry in st.get("files") or []:
        path = Path(entry)
        if path.name != name:
            continue
        if not path.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                "익스포트 파일이 서버에 없습니다 — 다시 익스포트하세요.")
        return FileResponse(path, media_type="video/mp4", filename=name)
    raise HTTPException(status.HTTP_404_NOT_FOUND,
                        "이 작업이 익스포트한 파일이 아닙니다.")


@router.post("/{external_id}/scenes/export/cleanup")
async def scene_export_cleanup(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """클라이언트가 다 받아 간 뒤 서버 사본을 지운다.

    서버는 클라에 넘겨줄 목적으로만 굽는다 — 안 지우면 잡마다 클립 수백 개가
    서버 디스크에 그대로 쌓인다. 클라가 '전부 받았다'고 알릴 때만 부른다:
    받는 중에 실패했는데 원본을 지우면 수십 분짜리 재인코딩을 다시 해야 한다.

    **작업 폴더 안**만 지운다. 옛 기록에는 사용자가 지정한 임의 경로(out_dir)가
    남아 있을 수 있는데, 그건 사용자 폴더이지 우리가 만든 사본이 아니다.
    """
    await _get_job_or_404(db, external_id)
    st = load_export_status(external_id)
    if not st:
        return {"deleted": 0}
    root = job_dir(external_id).resolve()
    deleted = 0
    for entry in st.get("files") or []:
        path = Path(entry)
        try:
            inside = path.resolve().is_relative_to(root)
        except OSError:      # 존재하지 않는 경로 등 — 건드리지 않는다
            continue
        if not inside:
            continue
        try:
            path.unlink(missing_ok=True)
            deleted += 1
        except OSError:
            logger.exception("익스포트 사본 삭제 실패: %s", path)
    # 비게 된 익스포트 폴더도 치운다(비어 있을 때만 — 남은 파일은 보존).
    out_dir = st.get("out_dir")
    if out_dir:
        d = Path(out_dir)
        try:
            if d.resolve().is_relative_to(root) and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass
    save_export_status(external_id, {**st, "files": []})
    return {"deleted": deleted}


class SceneRefineIn(BaseModel):
    mode: str = Field(pattern="^(scene|sequence)$")


@router.post("/{external_id}/scenes/refine", status_code=status.HTTP_202_ACCEPTED)
async def refine_scenes(
    external_id: UUID,
    body: SceneRefineIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _get_job_or_404(db, external_id)
    data = load_scenes(external_id)
    key = "segments_sequence" if body.mode == "sequence" else "segments_scene"
    if not data or not data.get("rule") or len(data.get(key) or []) < 2:
        raise HTTPException(status.HTTP_409_CONFLICT, "먼저 규칙을 확정하세요.")
    if data.get("method") == "fingerprint":
        # 지문 경계는 이미 프레임 정확 — 정밀화는 무의미하고 수십 분을 태운다.
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "지문 컷 감지 경계는 이미 프레임 정확합니다 — 정밀화가 필요 없습니다.")
    save_refine_status(external_id, {"refining": True, "done": 0,
                                     "total": len(data[key]) - 1, "error": None})
    _start_scene_refine(external_id, body.mode)
    return {"status": "refining", "total": len(data[key]) - 1}


@router.get("/{external_id}/scenes/refine/status")
async def scene_refine_status(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _get_job_or_404(db, external_id)
    st = load_refine_status(external_id)
    if not st:
        return {"refining": False, "done": 0, "total": 0, "error": None}
    return {"refining": bool(st.get("refining")), "done": st.get("done", 0),
            "total": st.get("total", 0), "error": st.get("error")}


@router.post("/{external_id}/scenes/boundary-check",
             status_code=status.HTTP_202_ACCEPTED)
async def boundary_check_scenes(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """씬 모드 세그먼트의 경계 프레임을 OCR해 head/tail 혼입 구간을 표시한다.
    익스포트 직전 자동 흐름의 마지막 단계 — 결과는 GET /scenes의 boundary_issues."""
    await _get_job_or_404(db, external_id)
    data = load_scenes(external_id)
    segments = (data or {}).get("segments_scene") or []
    if len(segments) < 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "먼저 규칙을 확정하세요.")
    save_boundary_status(external_id, {"checking": True, "done": 0,
                                       "total": len(segments), "error": None})
    _start_boundary_check(external_id)
    return {"status": "checking", "total": len(segments)}


@router.get("/{external_id}/scenes/boundary-check/status")
async def scene_boundary_status(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _get_job_or_404(db, external_id)
    st = load_boundary_status(external_id)
    if not st:
        return {"checking": False, "done": 0, "total": 0, "error": None}
    return {"checking": bool(st.get("checking")), "done": st.get("done", 0),
            "total": st.get("total", 0), "error": st.get("error")}


@router.post("/{external_id}/scenes/boundary-ok")
async def save_boundary_ok(
    external_id: UUID,
    body: BoundaryOkIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """사용자가 눈으로 확인해 '문제없음'으로 표시한 경계오류 구간 목록을 저장한다.

    경계 검사는 디졸브처럼 두 슬레이트가 겹쳐 보이는 구간을 혼입으로 플래그하는데
    실제로는 경계가 맞는 경우가 있다. 400씬 검수는 여러 세션에 걸치므로 확인 결과가
    남지 않으면 같은 줄을 매번 다시 본다.

    목록 '전체'를 교체한다 — 추가·삭제를 나누면 부분 상태가 어긋난다. 빈 배열이
    '모두 해제'다.

    확인 당시의 start_ms/end_ms를 함께 저장한다. 나중에 그 씬의 경계가 바뀌면
    클라가 이 확인표시를 무시하고 목록에 다시 띄운다 — 바뀐 경계를 안 본 채로
    숨기지 않기 위해서다.
    """
    await _get_job_or_404(db, external_id)
    data = load_scenes(external_id) or {}
    data["boundary_ok"] = [item.model_dump() for item in body.items]
    save_scenes(external_id, data)
    return {"count": len(body.items)}


@router.get("/{external_id}/scenes/thumb/{index}")
async def scene_thumbnail(
    external_id: UUID,
    index: int,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    await _get_job_or_404(db, external_id)
    # 썸네일은 1-based(thumb_00001.jpg) — index는 0-based 프레임 인덱스
    path = job_dir(external_id) / "scene_thumbs" / f"thumb_{index + 1:05d}.jpg"
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thumbnail not found")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/{external_id}/scenes/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_scene_ops(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """진행 중인 스캔/정밀화/익스포트 중단 — 지금까지는 앱을 죽여야 했다.

    세대 카운터를 올리고 ffmpeg를 죽인 뒤(cancel_job_task), 상태 파일의 진행
    플래그를 내려 프론트 폴링이 멈추게 한다. 취소된 스캔은 완료가 아니므로
    frames를 남기지 않는다(부분 판독을 완료로 오인하면 경계가 엉망이 된다).
    사용자가 지정한 OCR 구역은 작업과 무관한 설정이라 보존한다.
    """
    await _get_job_or_404(db, external_id)
    cancel_job_task(external_id)
    st = load_refine_status(external_id)
    if st and st.get("refining"):
        save_refine_status(external_id, {**st, "refining": False})
    bs = load_boundary_status(external_id)
    if bs and bs.get("checking"):
        save_boundary_status(external_id, {**bs, "checking": False})
    ex = load_export_status(external_id)
    if ex and ex.get("exporting"):
        save_export_status(external_id, {**ex, "exporting": False})
    data = load_scenes(external_id)
    if data and data.get("scanning"):
        save_scenes(external_id, {"scanning": False,
                                  "interval_ms": data.get("interval_ms", 2000),
                                  # 방식 선택도 설정이다 — 지우면 다음 GET이
                                  # interval로 오판해 UI 선택이 되돌아간다.
                                  "method": data.get("method", "interval"),
                                  "ocr_region": data.get("ocr_region")})
    return {"status": "canceled"}


@router.post("/{external_id}/scenes/ocr-region")
async def set_ocr_region(
    external_id: UUID,
    body: OcrRegionIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """OCR 영역 저장 — 스캔/정밀화가 이 영역만 잘라 판독한다(속도↑, 무관한
    텍스트 배제). 스캔 결과는 건드리지 않고 영역만 갱신한다."""
    await _get_job_or_404(db, external_id)
    data = load_scenes(external_id) or {}
    data["ocr_region"] = body.model_dump()
    save_scenes(external_id, data)
    return {"ocr_region": data["ocr_region"]}


class OcrTestIn(BaseModel):
    t_ms: int = Field(ge=0)
    region: OcrRegionIn | None = None


@router.post("/{external_id}/scenes/ocr-test")
async def test_ocr_region(
    external_id: UUID,
    body: OcrTestIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """지정한 영역으로 한 프레임만 읽어본다 — 25분짜리 스캔을 돌리기 전에
    영역이 맞는지 즉시 확인하기 위한 미리읽기."""
    await _get_job_or_404(db, external_id)
    burned = job_dir(external_id) / "burned.mp4"
    if not burned.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "burned video not found")
    ffmpeg = locate_ffmpeg()
    if ffmpeg is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "ffmpeg를 찾을 수 없습니다.")
    region = None
    if body.region is not None:
        r = body.region
        region = (r.x, r.y, r.w, r.h)
    tmp = job_dir(external_id) / "ocr_test.png"

    def _read() -> str:
        extract_frame(ffmpeg, burned, body.t_ms, tmp,
                      proc_key=str(external_id), region=region)
        # 스캔과 같은 구분자·같은 폴백으로 읽는다 — 미리읽기가 스캔보다 빡빡하면
        # 멀쩡한 구역에 "판독 실패"가 떠 사용자가 구역을 다시 잡게 된다.
        band = 1.0 if region else TOP_BAND_DEFAULT
        try:
            return (read_slate_line(tmp, DEFAULT_DELIMS, top_frac=band)
                    or read_slate_line_rescaled(tmp, DEFAULT_DELIMS,
                                                top_frac=band))
        finally:
            tmp.unlink(missing_ok=True)

    text = await asyncio.to_thread(_read)
    return {"text": text,
            "tokens": tokenize(text, DEFAULT_DELIMS) if text else []}


@router.get("/{external_id}/scenes/thumb-at")
async def scene_thumbnail_at(
    external_id: UUID,
    t_ms: Annotated[int, Query(ge=0)],
    db: Annotated[AsyncSession, Depends(get_session)],
    h: Annotated[int, Query(ge=48, le=720)] = 90,
) -> FileResponse:
    """임의 시각의 썸네일 — 정밀화된 구간 시작(2초 격자 밖) 프레임을 보여준다.

    요청 시 추출하고 디스크에 캐시한다. 캐시 키가 t_ms(+높이)라 경계가 바뀌어도
    무효화가 필요 없다(같은 시각이면 같은 프레임). 정밀화·병합으로 경계가 움직여도
    다음 렌더에서 새 시각으로 자연히 다시 뽑힌다.

    h=높이(px). 기본 90(필름스트립 격자용). 머리·꼬리 검수는 슬레이트를 읽을 수
    있게 더 큰 값을 요청한다. 기본 90은 파일명을 그대로 둬 기존 캐시와 호환한다.
    """
    await _get_job_or_404(db, external_id)
    burned = job_dir(external_id) / "burned.mp4"
    if not burned.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "burned video not found")
    name = f"at_{t_ms:09d}.jpg" if h == 90 else f"at_{t_ms:09d}_h{h}.jpg"
    path = job_dir(external_id) / "scene_thumbs" / name
    if not path.exists():
        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "ffmpeg를 찾을 수 없습니다.")
        await asyncio.to_thread(extract_thumbnail_at, ffmpeg, burned, t_ms, path,
                                h)
        if not path.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "thumbnail not found")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{external_id}/download")
async def download_video_job(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
    kind: Annotated[str, Query(pattern="^(video|srt)$")] = "video",
):
    job = await _get_job_or_404(db, external_id)
    if kind == "srt":
        rows = (await db.execute(
            select(VideoSegment).where(VideoSegment.job_id == job.id)
            .order_by(VideoSegment.seq)
        )).scalars().all()
        srt = segments_to_srt(
            [SubSegment(r.seq, r.start_ms, r.end_ms, r.text_ko) for r in rows])
        return Response(
            content=srt.encode("utf-8"), media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="{job.external_id}.srt"'})
    if not job.burned_path or not Path(job.burned_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "burned video not ready")
    return FileResponse(job.burned_path, media_type="video/mp4",
                        filename=f"{job.title[:60]}-captioned.mp4")


@router.get("/{external_id}/media")
async def stream_video_media(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    # capability URL — see module docstring
    job = await _get_job_or_404(db, external_id)
    path = job.preview_path or job.media_path
    if not path or not Path(path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media not ready")
    return FileResponse(path, media_type="video/mp4")


@router.delete("/{external_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video_job(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    job = await _get_job_or_404(db, external_id)
    # 실행 중인 파이프라인이 있으면 먼저 취소한다 — 세마포어를 즉시 반납해 대기 중인
    # 다음 작업이 진행되고, 지워진 행/파일에 대한 NoResultFound·FileNotFound를 반복하는
    # 좀비 태스크를 막는다. 취소 후 폴더/행 삭제.
    cancel_job_task(external_id)
    shutil.rmtree(job_dir(external_id), ignore_errors=True)
    await db.delete(job)
    await db.commit()
