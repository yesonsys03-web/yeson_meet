"""영상 작업 유지보수 — 재시작 스윕(작업 상태·씬 플래그)과 리텐션 프루닝.

pipeline.py에서 분리(2026-07-29 리팩토링, 동작 변경 0). 세 진입점 모두 서버
기동 경로(main.py lifespan)와 작업 생성 직후(prune)에서 호출된다.
"""
from __future__ import annotations

import logging
import os
import shutil

from sqlalchemy import delete, select, update

from apps.server.db.models import VideoJob
from apps.server.db.session import AsyncSessionLocal
from .job_store import (
    job_dir, load_boundary_status, load_export_status, load_refine_status,
    load_scenes, save_boundary_status, save_export_status, save_refine_status,
    save_scenes, video_jobs_root,
)

# 로거 이름은 분리 전과 동일하게 유지한다(job_tasks 참조).
logger = logging.getLogger("yeson.video.pipeline")

_INFLIGHT_STATUSES = ("queued", "ingesting", "extracting", "transcribing",
                      "translating", "burning")

# 자막 메이커가 무한정 쌓이지 않도록 유지할 최근 작업 수 (개수 상한 정책).
RETENTION_KEEP = 30


def _another_instance_is_serving() -> bool:
    """이미 같은 포트를 서빙 중인 인스턴스가 있으면 True.

    uvicorn은 lifespan startup을 소켓 바인딩보다 먼저 실행한다. 이중 기동된
    두 번째 프로세스는 곧 'address already in use'로 죽는데, 그 전에 sweep이
    돌면 살아있는 인스턴스의 진행 중 작업을 오판한다 — 그 경우 sweep을 건너뛴다.
    """
    import socket

    port = int(os.environ.get("PORT", "8000"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


async def fail_inflight_video_jobs_at_startup() -> None:
    """서버 재시작으로 중단된 작업을 error로 정리 — end_live_sessions_at_startup과 같은 취지.

    영상 자막 파이프라인은 프로세스 메모리상의 asyncio task로 진행되므로,
    서버가 재시작되면 진행 중이던 job은 상태만 남고 다시 이어받을 코드가
    없다 — 영구 좀비로 남아 큐를 막는다. 재시작 직후 in-flight 상태를 모두
    error로 정리해 사용자가 삭제 후 재시도할 수 있게 한다.
    """
    if _another_instance_is_serving():
        logger.warning(
            "startup video-job sweep skipped: another instance is already serving")
        return
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(VideoJob)
            .where(VideoJob.status.in_(_INFLIGHT_STATUSES))
            .values(status="error", error="서버 재시작으로 작업이 중단되었습니다. 삭제 후 다시 시도하세요.")
        )
        await db.commit()
    if result.rowcount:
        logger.info("startup sweep: %d in-flight video job(s) marked error", result.rowcount)


_RESTART_STOPPED = "서버가 재시작돼 작업이 중단되었습니다. 다시 실행하세요."


async def clear_stale_scan_flags_at_startup() -> None:
    """재시작으로 죽은 씬 분할 작업의 '진행 중' 플래그를 내린다.

    fail_inflight_video_jobs_at_startup은 DB의 job 상태만 본다. 씬 분할의
    진행 상태(scanning/refining/checking/exporting)는 작업 폴더의 JSON에 있고,
    그 플래그를 내리는 건 작업 자신뿐이라(완료·취소·실패) 스캔 도중 서버가
    재시작되면 뒤에 도는 작업이 없는데도 화면이 영원히 '실행중'으로 남았다 —
    사용자가 취소를 눌러야만 빠져나올 수 있었다.

    사용자 설정(ocr_region·method·interval)은 작업 산출물이 아니므로 보존하고,
    끝난 스캔은 건드리지 않는다(없던 에러를 심지 않는다). '다른 인스턴스가
    서빙 중' 가드는 DB 스윕과 같다 — 이중 기동된 비소유 프로세스가 살아있는
    인스턴스의 스캔을 죽이면 안 된다.
    """
    if _another_instance_is_serving():
        logger.warning(
            "startup scene-flag sweep skipped: another instance is serving")
        return
    root = video_jobs_root()
    if not root.exists():
        return
    cleared = 0
    for job in root.iterdir():
        if not job.is_dir():
            continue
        eid = job.name
        try:
            data = load_scenes(eid)
            if data and data.get("scanning"):
                save_scenes(eid, {**data, "scanning": False,
                                  "error": _RESTART_STOPPED})
                cleared += 1
            for load, save, key in (
                    (load_refine_status, save_refine_status, "refining"),
                    (load_boundary_status, save_boundary_status, "checking"),
                    (load_export_status, save_export_status, "exporting")):
                st = load(eid)
                if st and st.get(key):
                    save(eid, {**st, key: False, "error": _RESTART_STOPPED})
                    cleared += 1
        except Exception:  # noqa: BLE001 — 한 작업의 손상 파일이 기동을 막지 않게
            logger.exception("startup scene-flag sweep failed for %s", eid)
    if cleared:
        logger.info("startup sweep: cleared %d stale scene flag(s)", cleared)


async def _prune_pre_delete_hook(candidate_ids: list[int]) -> None:
    """프루닝의 SELECT와 DELETE 사이 지점 (기본 no-op). 테스트가 여기서 상태
    전이(review→burning)를 주입해 DELETE 시점의 상태 재확인 가드를 검증한다."""
    return None


async def prune_old_video_jobs(keep: int = RETENTION_KEEP) -> int:
    """가장 최근 ``keep``개만 남기고 오래된 영상 작업을 삭제한다 (작업 폴더 + DB 행).

    자막 메이커 작업은 원본/preview/burned mp4를 작업 폴더에 쌓으므로 정리하지
    않으면 무한정 누적된다. 서버 시작 시와 새 작업 생성 직후 호출해 개수를
    상한으로 유지한다. 진행 중(in-flight) 작업은 아무리 오래돼도 절대 지우지
    않는다 — 실행 중인 굽기/전사의 입력 파일을 없애면 안 되기 때문.
    """
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(VideoJob.id, VideoJob.status).order_by(
                    VideoJob.created_at.desc(), VideoJob.id.desc())
            )).all()
            candidate_ids = [r.id for r in rows[keep:]
                             if r.status not in _INFLIGHT_STATUSES]
            if not candidate_ids:
                return 0
            await _prune_pre_delete_hook(candidate_ids)
            # 삭제 시점에 상태를 원자적으로 재확인한다. SELECT와 DELETE 사이에
            # review→burning으로 전이한 작업(동시에 굽기가 시작된 경우)은 지우지
            # 않는다 — 그 폴더/행을 지우면 진행 중인 run_burn_job이 깨진다. Core
            # 벌크 삭제라 동시 프루닝 두 개가 겹쳐도 StaleDataError가 나지 않고,
            # 실제로 삭제된 행만 RETURNING으로 받아 그 폴더만 정리한다.
            deleted = (await db.execute(
                delete(VideoJob)
                .where(VideoJob.id.in_(candidate_ids),
                       VideoJob.status.not_in(_INFLIGHT_STATUSES))
                .returning(VideoJob.external_id)
            )).all()
            await db.commit()
        for row in deleted:
            shutil.rmtree(job_dir(row.external_id), ignore_errors=True)
        if deleted:
            logger.info("retention: pruned %d old video job(s) (keep=%d)",
                        len(deleted), keep)
        return len(deleted)
    except Exception:  # noqa: BLE001 — fire-and-forget 태스크로도 호출되므로 삼키고 로그
        logger.exception("video-job retention prune failed")
        return 0


async def prune_old_video_jobs_at_startup() -> int:
    """서버 시작 시 리텐션 프루닝 — in-flight 스윕과 동일한 '다른 인스턴스가
    서빙 중' 가드로 보호한다.

    이중 기동된 비소유 프로세스(uvicorn lifespan이 포트 바인딩보다 먼저 도는)가
    살아있는 인스턴스의 작업 폴더/DB 행을 지운 뒤 'address already in use'로
    죽는 것을 막는다. 런타임 작업 생성 시 호출되는 prune_old_video_jobs()는
    자기 자신이 이미 포트를 점유하고 있어 이 가드를 쓰면 항상 스킵되므로,
    가드는 startup 경로에만 둔다.
    """
    if _another_instance_is_serving():
        logger.warning(
            "startup retention prune skipped: another instance is already serving")
        return 0
    return await prune_old_video_jobs()
