"""Video caption job endpoints.

The entire video captions API is deliberately UNAUTHENTICATED (product
decision 2026-07-06): this deployment treats the LAN as the trust boundary,
the same acceptance already made for viewer tokens. /media in particular
can never carry an Authorization header (HTML5 <video> cannot attach one),
so its unguessable job UUID acts as the capability URL — the other
endpoints extend that same trust decision rather than being a special case.
"""
from __future__ import annotations

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
                                                        cancel_job_task, job_dir,
                                                        prune_old_video_jobs,
                                                        run_burn_job, run_video_job,
                                                        start_job_task, start_task,
                                                        video_jobs_root)
from apps.server.domain.video_captions.pipeline import \
    _INFLIGHT_STATUSES as INFLIGHT_STATUSES
from apps.server.domain.video_captions.srt import SubSegment, segments_to_srt
from apps.server.domain.video_captions.translate import (is_source_copy,
                                                         is_untranslated,
                                                         maybe_aclose_translator,
                                                         translate_segments)
from apps.server.domain.video_captions.translate_cli import (create_translator,
                                                             list_translate_engines)
from apps.server.domain.video_captions.whisper_models import get_catalog, is_downloaded

router = APIRouter(tags=["video-jobs"], prefix="/video-jobs")


def _start_pipeline(external_id: UUID) -> None:  # test seam
    start_job_task(external_id, run_video_job(external_id))


def _start_burn(external_id: UUID, position: str, margin_v: int,
                font_size: int, color: str) -> None:  # test seam
    start_job_task(external_id,
                   run_burn_job(external_id, position, margin_v, font_size, color))


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


def _job_dir_size(external_id: UUID | str) -> int:
    """작업 폴더의 총 바이트. pathlib만 사용 — Windows/POSIX 공통. 스캔 중
    사라진 파일(동시 프루닝)은 무시한다."""
    total = 0
    d = job_dir(external_id)
    if d.exists():
        for path in d.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
    return total


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
            out["size_bytes"] = _job_dir_size(job.external_id)
        items.append(out)
    return {"items": items}


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
    root = video_jobs_root()
    total = 0
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass  # 스캔 도중 사라진 파일은 무시
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
