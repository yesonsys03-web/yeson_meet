"""Video caption job orchestration.

Long-running per-job work runs as an asyncio task with its OWN
``AsyncSessionLocal()`` (the request session is closed by then) — same rule as
the report FTS background task. CPU-bound stages go through asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select, update

from apps.server.db.models import VideoJob
from apps.server.db.session import AsyncSessionLocal
from . import gpu_pack  # noqa: F401 — 파사드 존속(테스트가 pl.gpu_pack.is_enabled를 패치)
from .ffmpeg import (
    build_scan_source, cut_segment, extract_frame,
    extract_fingerprint_frames, extract_frames_at,
    extract_thumbnails, locate_ffmpeg, video_fps,
)
from .job_store import (
    _TOP_BAND_DEFAULT, _band_for, boundary_status_path, export_status_path,
    job_dir, load_boundary_status, load_export_status, load_ocr_region,
    load_refine_status, load_scenes, refine_status_path, save_boundary_status,
    save_export_status, save_refine_status, save_scenes, scenes_json_path,
    video_jobs_root,
)
from .job_tasks import (
    _BURN_SEMAPHORE, _JOB_SEMAPHORE, _bump_generation, _current_generation,
    _load_job, _refine_workers, _set_progress, _set_status, _try_set_error,
    cancel_job_task, start_job_task, start_task,
)
from .fingerprint import (
    FADE_WINDOW, detect_cuts_with_fades, diff_series, frame_boundary_ms,
    frame_runs, load_fingerprint, stable_frame,
)
from .burn_run import run_burn_job  # noqa: F401 — 파사드 재수출
from .caption_run import run_video_job  # noqa: F401 — 파사드 재수출
from .scene_scan import (  # noqa: F401 — 재수출(+지문 스캔이 _DEFAULT_DELIMS 공유)
    _DEFAULT_DELIMS, _SCAN_INTERVAL_S, build_scene_data, run_scene_scan,
)
from .scene_split import (
    SceneRun, SlateRule, build_label, canonicalize_texts,
    dedupe_labels, label_matches, runs_to_segments, tokenize,
)
from .slate_ocr import read_frame_text, read_slate_line, read_slate_line_rescaled
from .transcribe import StaleRunCancelled

logger = logging.getLogger("yeson.video.pipeline")

# StaleRunCancelled(취소·재생성 감지용 예외)는 pipeline↔transcribe 순환 임포트를
# 피해 transcribe.py에 정의돼 있고, 전사·굽기 진행 콜백이 공용으로 던진다.


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

_INFLIGHT_STATUSES = ("queued", "ingesting", "extracting", "transcribing",
                      "translating", "burning")

# 자막 메이커가 무한정 쌓이지 않도록 유지할 최근 작업 수 (개수 상한 정책).
RETENTION_KEEP = 30


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


# 판독 카운터(ocr_done)가 아직 없는 앞 구간의 단계 이름 — 프론트가 그대로
# 표시한다("프레임 추출 중…"보다 어디쯤인지 알 수 있게).
STAGE_CROP = "스캔용 크롭본 만드는 중"
STAGE_FRAMES = "프레임 추출 중"
STAGE_THUMBS = "썸네일 만드는 중"
STAGE_CUTS = "컷 감지 중"

# 살아있음 신호를 갱신하는 주기(초). 프론트 정체 판정이 200초라 넉넉히 짧게.
_EXTRACT_TICK_S = 3.0


def _extract_tick(scan_src: Path, frames_dir: Path, thumbs_dir: Path) -> int:
    """추출 구간의 '살아있음' 신호 — 산출물이 실제로 늘어야 값이 오른다.

    단순 시계였다면 진짜로 멎은 ffmpeg도 살아있는 것처럼 보여 정체 감지가
    죽는다. 크롭본 크기(KB)와 추출된 파일 수를 더해, 일이 진행될 때만 값이
    바뀌게 한다(수가 아니라 '변했는가'만 쓴다).
    """
    tick = 0
    try:
        if scan_src.exists():
            tick += scan_src.stat().st_size // 1024
    except OSError:
        pass
    for d, pat in ((frames_dir, "f_*.png"), (thumbs_dir, "thumb_*.jpg")):
        try:
            if d.exists():
                tick += sum(1 for _ in d.glob(pat))
        except OSError:
            pass
    return tick


# 지문 클러스터 흡수 캡 — 프론트 '오독 갈라짐 정리'(FLANK_MAX_MS)와 동일 5초.
# 이보다 긴 블록은 진짜 비단조 씬일 수 있어 보존한다.
_FP_FLANK_MAX_MS = 5000


def build_fingerprint_segments(runs_raw: list[dict], rule_dict: dict) -> dict:
    """지문 런 + 규칙 → 양 모드 세그먼트(순수 함수, build_scene_data의 지문판).
    경계는 이미 프레임 정확한 컷이라 min_ms 흡수·중앙정렬·정밀화가 없다 —
    규칙은 런들을 같은 키로 병합하는 데만 쓴다.

    그룹핑 전에 런 텍스트를 canonical화하고(구분자 유실 오독 → 같은 키로 병합),
    교정 못 한 오독은 클러스터 흡수(≤5s)로 걷어낸다 — 지문은 런 중간(흐릿한
    프레임 근처)을 읽어 오독률이 높아(실기 11.5%) 이 두 단계가 없으면 오독
    하나가 세그먼트 하나로 굳는다(실기 씬 806→481·시퀀스 322→19)."""
    rule = SlateRule(
        delimiters=rule_dict.get("delimiters", ["_", " ", "-"]),
        seq_tokens=rule_dict["seq_tokens"],
        scene_tokens=rule_dict.get("scene_tokens", []),
    )
    texts = canonicalize_texts([r.get("text", "") for r in runs_raw],
                               rule.delimiters,
                               example=rule_dict.get("example"))
    runs = [SceneRun(start_ms=r["start_ms"], end_ms=r["end_ms"], text=t,
                     cut_diff=r.get("cut_diff", 0))
            for r, t in zip(runs_raw, texts)]
    return {
        "segments_scene": [
            {"label": s.label, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in runs_to_segments(runs, rule, "scene",
                                      absorb_flanked_ms=_FP_FLANK_MAX_MS)],
        "segments_sequence": [
            {"label": s.label, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in runs_to_segments(runs, rule, "sequence",
                                      absorb_flanked_ms=_FP_FLANK_MAX_MS)],
    }


# 지문 스캔에서 구역 미지정 시 상단 밴드를 크롭으로 쓴다(_TOP_BAND_DEFAULT와
# 같은 비율) — 지문은 크롭이 필수라(전체 프레임이면 애니 전체의 변화가 다 컷으로
# 잡힌다) 기존 '상단 밴드 가정'을 크롭으로 실체화한 것. 썸네일 간격은 간격
# 스캔의 상한과 동일한 2초 고정(지문에는 샘플 간격 개념이 없다).
_FP_FALLBACK_REGION = (0.0, 0.0, 1.0, _TOP_BAND_DEFAULT)
_FP_THUMB_INTERVAL_S = 2.0


def _text_side(text: str | None, prev_text: str, next_text: str,
               delimiters: list[str]) -> str | None:
    """판독 텍스트가 이전/다음 어느 쪽 슬레이트인지 — squash 접두 상호 일치
    (오독·꼬리 잘림 내성). 판독불가·양쪽 다 일치(공통 접두만 읽힘)면 None."""
    def sq(s: str) -> str:
        # 소문자화 — OCR이 v01/V01을 오가며 읽어(실기) 대소문자 구분 비교는
        # '어느 쪽도 아님'을 만들고 OCR 권위를 무력화한다.
        return "".join("".join(t.split()) for t in tokenize(s, delimiters)).lower()

    x = sq(text or "")
    if not x:
        return None
    prev_sq, next_sq = sq(prev_text), sq(next_text)
    match_prev = x.startswith(prev_sq) or prev_sq.startswith(x)
    match_next = x.startswith(next_sq) or next_sq.startswith(x)
    if match_prev == match_next:
        return None
    return "next" if match_next else "prev"


def _clamp_fp_move(ocr_side, cur: int, target: int) -> int:
    """지문 유사도 이동을 OCR 가독성으로 캡 — 읽히는 프레임의 소속은 OCR이 권위.

    유사도 정렬은 판독불가 페이드에는 옳지만, 새 슬레이트가 옛 그림 위에 일찍
    떠오르는 반대 극성 디졸브에서는 OCR로 이미 '다음'이 읽히는 프레임까지 이전
    쪽으로 밀어버린다(실기 090_0180 꼬리에 0190). 오른쪽 이동은 prev로 재배정될
    구간에서 next로 읽히는 첫 프레임에서 멈추고, 왼쪽 이동은 next로 재배정될
    구간에서 prev로 읽히는 프레임 뒤로 물린다."""
    if target > cur:
        for frame in range(cur, target):
            if ocr_side(frame) == "next":
                return frame
        return target
    if target < cur:
        best = target
        for frame in range(target, cur):
            if ocr_side(frame) == "prev":
                best = frame + 1
        return best
    return cur


def _align_cut(read_at, cut: int, prev_text: str, next_text: str,
               lo: int, hi: int, delimiters: list[str],
               max_probe: int = 24) -> int:
    """지문 컷을 '다음 슬레이트가 읽히는 첫 프레임'으로 정렬한다.

    지문 컷(픽셀 전환 지점)은 디졸브에서 슬레이트 '가독' 전환과 어긋난다
    (실기: 130→140 컷 6프레임 지각 — 클립 꼬리가 다음 시퀀스로 읽힘,
    030→040은 1프레임 조기). read_at(frame)->text로 컷 주변을 읽어, 컷 직전
    프레임이 이미 다음으로 읽히면 왼쪽으로, 컷 프레임이 아직 이전으로 읽히면
    오른쪽으로 걷는다. 판정은 squash 접두 상호 일치(오독·꼬리 잘림 내성) —
    양쪽 다 일치(공통 접두만 읽힘)하거나 판독불가면 근거가 없으므로 멈춘다
    (보수적 — 원래 컷 유지가 기본). lo/hi는 이웃 런 침범 방지 경계(exclusive)."""
    def side(frame: int) -> str | None:
        return _text_side(read_at(frame), prev_text, next_text, delimiters)

    before = side(cut - 1)
    if before == "next" or (before is None and side(cut) == "next"):
        # 컷 지각 — 다음 슬레이트가 읽히는 가장 이른 프레임까지 왼쪽으로.
        # 컷 직전 프레임이 판독불가여도 컷 프레임이 '다음'으로 읽히면 더
        # 왼쪽을 살핀다 — 슬레이트만 바뀌고 그림이 이어지는 무컷 전환에서
        # 경계 프레임 판독 깜박임 하나가 걷기 시작을 막아 꼬리 혼입
        # ~22프레임이 남았다(실기 040_0200). 이동은 '다음'으로 확인된 가장
        # 깊은 프레임까지만: 판독불가는 건너뛰되 이동 근거가 되지 않고(그
        # 구간의 귀속은 원래 컷 쪽 유지), '이전'이 읽히면 멈춘다.
        new = cut - 1 if before == "next" else cut
        frame, probes = cut - 2, 0
        while frame > lo and probes < max_probe:
            s = side(frame)
            if s == "prev":
                break
            if s == "next":
                new = frame
            frame -= 1
            probes += 1
        return new
    if side(cut) == "prev":
        # 컷 조기 — 이전 슬레이트가 끝나는 지점(다음이 읽히는 첫 프레임)까지.
        # 직전 프레임(before)이 판독불가여도 컷 프레임이 '이전'으로 읽히면
        # 걷는다 — before까지 요구하던 가드가 디졸브 경계의 ±1프레임 잔존
        # 4건을 남겼다(실기 468클립 검사). 컷 프레임이 판독불가면 걷지 않는다
        # — 무판독 구간의 귀속은 지문(①_fp_align·블록 귀속)의 몫이라, 여기서
        # 걷어 다음-읽힘 프레임까지 밀면 지문이 next로 귀속한 구간을 도로
        # 빼앗는다(통합 테스트로 잠금).
        frame, probes = cut + 1, 0
        while frame < hi and probes < max_probe:
            s = side(frame)
            if s == "next":
                return frame
            # 판독불가/양쪽공통 프레임은 근거가 없을 뿐 — 걷기를 끊지 않고
            # 건너뛴다(디졸브 블러 1프레임이 걷기를 끊어 다음이 읽히는데도
            # 컷이 안 옮겨지던 실기 머리 혼입 090_0060·020_0250). '다음'을
            # 못 찾고 끝나면 컷 유지라 건너뛴 프레임은 이전 쪽에 남는다.
            frame += 1
            probes += 1
    return cut


def _fp_align(fp_at, cut: int, ref_prev, ref_next, lo: int, hi: int,
              window: int = 8) -> int | None:
    """지문 유사도 플립 지점으로 컷을 정렬 — 판독불가 페이드 프레임의 귀속.

    디졸브의 페이드 프레임은 OCR로 못 읽지만 픽셀은 아직 이전 슬레이트의
    잔상이다(실기 030_0190→0200: 페이드 2프레임의 지문 거리 4823 vs 8044로
    이전 쪽, 다음 첫 프레임은 7951 vs 127로 다음 쪽 — 사람 눈의 경계와 일치).
    컷 주변 창에서 프레임 지문이 이전/다음 런 대표 지문(안정 프레임) 중 어느
    쪽에 가까운지를 훑어 '다음 쪽에 처음 가까워지는 프레임'을 경계로 삼는다.
    창 안에 플립이 없으면 None(유지). OCR 정렬과 달리 판독 불가 프레임에서도
    동작하고, 이미 추출된 지문 PNG를 재사용해 ffmpeg·OCR 호출이 없다.
    lo/hi는 이웃 런 침범 방지 경계(lo exclusive, hi exclusive)."""
    import numpy as np

    def is_next(frame: int) -> bool:
        fp = fp_at(frame)
        return int(np.sum(fp != ref_prev)) >= int(np.sum(fp != ref_next))

    start = max(lo + 1, cut - window)
    end = min(hi, cut + window + 1)
    prior: bool | None = None
    for frame in range(start, end):
        cur = is_next(frame)
        if cur and prior is not True:
            return frame
        prior = cur
    return None


# 패딩 재판독 배율 — 경계 프레임 판독은 검출기 여백·저대비에 민감해, 같은
# 프레임이 구역을 넓히면 읽히는 경우가 실측으로 확인됐다(HH0304: 130_0160
# 디졸브 블러·020_0250 잔존 프레임 모두 타이트 구역 ''→패딩 구역 정상 판독).
# 1차는 스캔과 동일 구역(판독 조건 일관), 실패 시에만 패딩으로 근거를 회수한다.
_READ_PAD_FRAC = 0.3


def _pad_region(region) -> tuple[float, float, float, float]:
    """경계 재판독용 패딩 구역 — 사방으로 w·h의 _READ_PAD_FRAC만큼 넓힌다
    (0..1 클램프). 판독에만 쓰며 지문·경계 계산 구역은 그대로다."""
    x, y, w, h = region
    nx = max(0.0, x - w * _READ_PAD_FRAC)
    ny = max(0.0, y - h * _READ_PAD_FRAC)
    nw = min(1.0 - nx, x + w * (1.0 + _READ_PAD_FRAC) - nx)
    nh = min(1.0 - ny, y + h * (1.0 + _READ_PAD_FRAC) - ny)
    return (nx, ny, nw, nh)


def _relative_region(inner, outer) -> tuple[float, float, float, float]:
    """outer 크롭본 좌표계에서 본 inner 구역(비율, 0..1 클램프).

    스캔 중간본(build_scan_source가 만든 패딩 크롭 영상) 위에서 타이트 구역
    판독을 계속하기 위한 변환 — 중간본 전체 프레임(0,0,1,1)이 곧 패딩 구역이고,
    타이트 구역은 그 안의 부분 사각형이 된다."""
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    if ow <= 0 or oh <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    x = min(max((ix - ox) / ow, 0.0), 1.0)
    y = min(max((iy - oy) / oh, 0.0), 1.0)
    w = min(max(iw / ow, 0.0), 1.0 - x)
    h = min(max(ih / oh, 0.0), 1.0 - y)
    return (x, y, w, h)


def _resolve_unreadable_blocks(
    runs_f: list[tuple[int, int]], texts: list[str], picks: list[int],
    delimiters: list[str], fp_at, read_frame,
) -> tuple[list[tuple[int, int]], list[str]]:
    """서로 다른 라벨 사이에 낀 판독불가('') 런 블록을 프레임 단위로 귀속한다.

    텍스트 근거가 전혀 없는 블록은 runs_to_segments의 컷 세기 비율만으로는
    판정이 안 된다(실기 HH0304 2026-07-23: 문제 경계 전부가 애매 밴드
    1.1~2.4배 → 블록이 통째 앞 씬에 붙어 시퀀스 3·씬 48클립에 이웃 프레임
    혼입). 여기서 ①블록 각 프레임의 지문이 이전/다음 런 대표 지문(안정
    프레임) 중 어느 쪽에 가까운지로 플립 프레임을 찾고(_fp_align과 같은
    판정을 블록 전체 폭으로), ②블록 가장자리·플립 주변 프레임을 OCR해
    읽히는 프레임의 소속으로 플립을 캡한다(OCR 가독 > 픽셀 유사도 원칙).
    블록은 플립에서 갈라 양옆 런에 병합돼 사라진다 — 이후 경계 정렬(bounds)이
    읽히는 경계가 된 이 지점을 ±8프레임 OCR로 최종 다듬는다.

    같은 canonical 라벨 사이 블록(씬 내부 가짜컷·오독)은 연속 병합이 맞고,
    선두/꼬리 블록(한쪽 이웃 없음)은 기존 규칙(선두 드롭·꼬리 앞씬)이 맞으므로
    건드리지 않는다. OCR 제약이 모순(비단조 판독)이면 보수적으로 무변경."""
    import numpy as np

    def sq(s: str) -> str:
        return "".join("".join(t.split())
                       for t in tokenize(s, delimiters)).lower()

    blocks: list[tuple[int, int]] = []
    i = 0
    while i < len(texts):
        if texts[i].strip():
            i += 1
            continue
        j = i
        while j + 1 < len(texts) and not texts[j + 1].strip():
            j += 1
        blocks.append((i, j))
        i = j + 1

    resolved: dict[int, tuple[int, int]] = {}  # bi -> (bj, flip)
    for bi, bj in blocks:
        a, b = bi - 1, bj + 1
        if a < 0 or b >= len(texts):
            continue
        if sq(texts[a]) == sq(texts[b]):
            continue
        s, e = runs_f[bi][0], runs_f[bj][1]
        ref_prev, ref_next = fp_at(picks[a]), fp_at(picks[b])

        def is_next(f: int) -> bool:
            # _fp_align의 >=와 달리 엄격 부등호 — 지문이 무정보(양쪽 동거리)면
            # 이동 근거가 없으므로 기존 귀속(앞 씬)에 남긴다. OCR 캡이 최종 권위.
            fp = fp_at(f)
            return int(np.sum(fp != ref_prev)) > int(np.sum(fp != ref_next))

        flip = e
        for f in range(s, e):
            if is_next(f):
                flip = f
                break
        lo, hi = s, e
        for f in sorted({s, e - 1, max(s, flip - 1), min(e - 1, flip)}):
            side = _text_side(read_frame(f), texts[a], texts[b], delimiters)
            if side == "prev":
                lo = max(lo, f + 1)
            elif side == "next":
                hi = min(hi, f)
        if lo > hi:
            continue
        resolved[bi] = (bj, min(max(flip, lo), hi))

    if not resolved:
        return list(runs_f), list(texts)

    out_runs: list[tuple[int, int]] = []
    out_texts: list[str] = []
    override_start: int | None = None
    i = 0
    while i < len(runs_f):
        if i in resolved:
            bj, flip = resolved[i]
            if out_runs and flip > runs_f[i][0]:
                out_runs[-1] = (out_runs[-1][0], flip)
            override_start = flip
            i = bj + 1
            continue
        s, e = runs_f[i]
        if override_start is not None:
            s = override_start
            override_start = None
        out_runs.append((s, e))
        out_texts.append(texts[i])
        i += 1
    return out_runs, out_texts


async def run_scene_scan_fingerprint(external_id: UUID) -> None:
    """burned.mp4 전 프레임의 텍스트 이진화 지문으로 컷을 찾고, 컷 사이 런마다
    슬레이트를 OCR해 scenes.json에 method="fingerprint"로 저장한다. 경계는 규칙
    확정(/scenes/rule) 때 runs_to_segments가 계산한다 — 간격 스캔과 같은 2단계
    UX이되, 경계가 이미 프레임 정확이라 정밀화 단계가 없다.

    진행률: 추출·지문 단계는 total_frames=0(프론트 '프레임 추출 중…' 표시),
    런 OCR 단계부터 ocr_done/total_frames(=런 수)로 증분 기록. 취소·실패·세마포어
    규약은 run_scene_scan과 동일하되 method를 함께 보존한다(방식 선택 유지)."""
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    region_out = None
    try:
        workdir = job_dir(external_id)
        burned = workdir / "burned.mp4"
        if not burned.exists():
            raise RuntimeError("굽기 완료본(burned.mp4)이 없습니다.")
        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")
        # 컷 프레임 인덱스 ↔ 시각(ms) 변환의 기준 — 반드시 측정 fps(showinfo).
        fps = video_fps(ffmpeg, burned)
        if not fps:
            raise RuntimeError("소스 프레임레이트를 측정하지 못했습니다.")

        frames_dir = workdir / "scene_fp_frames"
        thumbs_dir = workdir / "scene_thumbs"
        for d in (frames_dir, thumbs_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

        region = load_ocr_region(external_id)
        region_out = ({"x": region[0], "y": region[1],
                       "w": region[2], "h": region[3]} if region else None)
        eff_region = region or _FP_FALLBACK_REGION
        thumb_interval_ms = int(_FP_THUMB_INTERVAL_S * 1000)

        def _prog(extra: dict) -> dict:
            return {"scanning": True, "method": "fingerprint",
                    "thumb_interval_ms": thumb_interval_ms,
                    "ocr_region": region_out, **extra}

        def _check_cancel() -> None:
            if generation != _current_generation(external_id):
                raise StaleRunCancelled(external_id)

        # 스캔 전용 크롭 중간본 — 이후 지문·판독·정렬의 모든 디코드가 1080p
        # 원본 대신 이 초소형 영상을 대상으로 한다(build_scan_source 참조).
        # 중간본 전체 프레임=패딩 구역이므로, 타이트 구역은 상대 좌표로 변환해
        # 쓰고 패딩 판독은 크롭 없이(전체 프레임) 읽는다.
        scan_src = workdir / "fp_scan_src.mp4"
        pad_abs = _pad_region(eff_region)
        tight_rel = _relative_region(eff_region, pad_abs)
        _FULL_REL = (0.0, 0.0, 1.0, 1.0)

        def _work() -> tuple[list[SceneRun], int, int]:
            # 여기부터 diff_series까지는 판독 카운터가 없는 구간이다(실측 60초).
            # 아무것도 안 흘리면 프론트가 200초 무변화를 정체로 보고 멀쩡한
            # 스캔을 포기하므로, 단계 이름과 산출물 증가(_extract_tick)를
            # 감시 스레드가 알린다 — 이 구간에는 다른 기록자가 없어 경합 없음.
            stage = [STAGE_CROP]
            stop = threading.Event()
            lock = threading.Lock()

            def _mark(tick: int) -> None:
                with lock:
                    save_scenes(external_id, _prog(
                        {"total_frames": 0, "ocr_done": 0, "frames": [],
                         "stage": stage[0], "stage_tick": tick}))

            def _enter(name: str) -> None:
                """단계 진입 — 본 스레드가 쓰므로 순서가 보장된다."""
                stage[0] = name
                _mark(_extract_tick(scan_src, frames_dir, thumbs_dir))

            def _watch() -> None:
                """단계 '안'의 살아있음 — 산출물이 늘 때만 값을 올린다."""
                last = None
                while not stop.wait(_EXTRACT_TICK_S):
                    tick = _extract_tick(scan_src, frames_dir, thumbs_dir)
                    if tick != last:
                        last = tick
                        _mark(tick)

            _enter(STAGE_CROP)
            watcher = threading.Thread(target=_watch, daemon=True)
            watcher.start()
            try:
                build_scan_source(ffmpeg, burned, scan_src, pad_abs,
                                  proc_key=str(external_id))
                _enter(STAGE_FRAMES)
                extract_fingerprint_frames(ffmpeg, scan_src, frames_dir,
                                           tight_rel, proc_key=str(external_id))
                _enter(STAGE_THUMBS)
                # 썸네일은 전체 화면이 필요하다(필름스트립) — 원본 유지.
                extract_thumbnails(ffmpeg, burned, thumbs_dir,
                                   _FP_THUMB_INTERVAL_S,
                                   proc_key=str(external_id))
            finally:
                stop.set()
                watcher.join(timeout=_EXTRACT_TICK_S * 2)
            thumb_count = len(list(thumbs_dir.glob("thumb_*.jpg")))
            pngs = sorted(frames_dir.glob("f_*.png"))
            n_frames = len(pngs)
            if n_frames == 0:
                raise RuntimeError("프레임을 추출하지 못했습니다.")
            stage[0] = STAGE_CUTS
            # 인접+윈도우 diff 한 패스 — 윈도우가 느린 페이드(인접 diff가 임계를
            # 못 넘는 디졸브)의 컷 누락을 막는다(실기: 씬 통째 흡수). 3만 장을
            # 도는 통짜 루프라 여기서도 진행률을 흘린다(취소 확인과 같은 주기).
            diffs, wdiffs = diff_series(
                pngs, FADE_WINDOW, check_cancel=_check_cancel,
                on_progress=lambda i: save_scenes(external_id, _prog(
                    {"total_frames": 0, "ocr_done": 0, "frames": [],
                     "thumb_count": thumb_count,
                     "stage": STAGE_CUTS, "stage_tick": i})))
            runs_f = frame_runs(
                detect_cuts_with_fades(diffs, wdiffs, FADE_WINDOW), n_frames)
            total = len(runs_f)
            save_scenes(external_id, _prog(
                {"total_frames": total, "ocr_done": 0, "frames": [],
                 "thumb_count": thumb_count}))

            tmpdir = workdir / "fp_ocr_tmp"
            tmpdir.mkdir(parents=True, exist_ok=True)

            # 런마다 '정지' 프레임(인접 diff 최소)을 골라 한 번의 디코드 패스로
            # 일괄 추출한다 — 런마다 -ss 시킹하면 830ms×수천 런=수 분이 시킹에
            # 녹고(실측 총 9분), 흐릿한 중간 프레임을 읽어 오독도 는다.
            picks = [stable_frame(diffs, s, e) for s, e in runs_f]
            batch = extract_frames_at(ffmpeg, scan_src, picks, tmpdir,
                                      tight_rel, proc_key=str(external_id),
                                      workers=_refine_workers())

            def _read_run(item: tuple[int, tuple[int, int]]) -> str:
                # 배치 프레임만 읽는다 — 실패분의 재시도는 아래 패딩 배치와
                # 잔여 시킹 단계가 맡는다. 예전엔 여기서 런마다 개별 시킹
                # 폴백(0.25/0.75)을 했는데, '' 런이 많은 실기(HH0304 1011런)
                # 에서 시킹에만 ~10분이 녹았고 그 뒤의 패딩 배치가 어차피
                # 95%를 살렸다(순서 비효율).
                idx, _span = item
                _check_cancel()
                png = batch.get(picks[idx])
                return (read_slate_line(png, _DEFAULT_DELIMS, top_frac=1.0)
                        if png is not None else "")

            texts: list[str] = []
            done = 0
            try:
                # 런 판독은 서로 독립 — 정밀화·스캔과 같은 이유·설정으로 병렬화.
                with ThreadPoolExecutor(max_workers=_refine_workers()) as pool:
                    for text in pool.map(_read_run, enumerate(runs_f)):
                        texts.append(text)
                        done += 1
                        if done % 10 == 0 or done == total:
                            save_scenes(external_id, _prog(
                                {"total_frames": total, "ocr_done": done,
                                 "frames": [], "thumb_count": thumb_count}))

                # ── 패딩 재판독 배치 — 타이트 구역이 못 읽은 런의 안정 프레임을
                # 패딩 구역(_pad_region)으로 한 번 더 일괄 판독한다. 실기 HH0304:
                # '' 1011런의 상당수가 패딩에서 정상 판독(110_0330~0350은 씬
                # 통째가 이 경로로만 복구). 추가 비용은 미판독 런 수만큼의
                # select 배치 1회.
                # 재시도 단계도 진행률을 쓴다 — 카운터가 total에 닿은 채 몇 분이
                # 흐르면 프론트의 정체 판정(200초 무변화)이 멀쩡한 스캔을 실패로
                # 만든다("스캔이 진행되지 않습니다"). 실기: 타이트 판독이 전멸한
                # 소스에서 미판독 런이 곧 전체 런이라 두 재시도 단계가 스캔의
                # 대부분을 차지했고, 화면은 '판독 중… 2791/2791'에서 굳었다.
                # 정렬 단계와 같은 방식으로 total 뒤에 이어 센다.
                def _stage_prog(n: int, done_k: int) -> None:
                    save_scenes(external_id, _prog(
                        {"total_frames": total + n, "ocr_done": total + done_k,
                         "frames": [], "thumb_count": thumb_count}))

                miss = [i for i, t in enumerate(texts) if not t.strip()]
                if miss:
                    pad_batch = extract_frames_at(
                        ffmpeg, scan_src, [picks[i] for i in miss],
                        tmpdir / "pad", _FULL_REL,
                        proc_key=str(external_id), workers=_refine_workers())
                    for k, i in enumerate(miss):
                        _check_cancel()
                        png = pad_batch.get(picks[i])
                        if png is not None:
                            t = (read_slate_line(png, _DEFAULT_DELIMS,
                                                 top_frac=1.0)
                                 or read_slate_line_rescaled(
                                     png, _DEFAULT_DELIMS, top_frac=1.0))
                            if t:
                                texts[i] = t
                        if k % 10 == 0 or k == len(miss) - 1:
                            _stage_prog(len(miss), k + 1)

                # 패딩 배치도 못 살린 잔여 런만 개별 시킹 재시도(실기 1011→55).
                # 자리를 바꿔(0.25/0.75) 타이트→패딩 순으로 읽는다 — 런 내부는
                # 텍스트가 동일하다는 지문 방식의 전제 그대로.
                def _retry_run(i: int) -> tuple[int, str]:
                    _check_cancel()
                    start_f, end_f = runs_f[i]
                    span = end_f - start_f
                    for frac in (0.25, 0.75):
                        fi = min(end_f - 1, start_f + int(span * frac))
                        for region in (tight_rel, _FULL_REL):
                            dst = tmpdir / f"r_{threading.get_ident()}_{fi}.png"
                            extract_frame(ffmpeg, scan_src,
                                          frame_boundary_ms(fi, fps), dst,
                                          proc_key=str(external_id),
                                          region=region)
                            text = read_slate_line(dst, _DEFAULT_DELIMS,
                                                   top_frac=1.0)
                            if not text and region is _FULL_REL:
                                text = read_slate_line_rescaled(
                                    dst, _DEFAULT_DELIMS, top_frac=1.0)
                            try:
                                dst.unlink()
                            except OSError:
                                pass
                            if text:
                                return i, text
                    return i, ""

                still = [i for i, t in enumerate(texts) if not t.strip()]
                if still:
                    with ThreadPoolExecutor(
                            max_workers=_refine_workers()) as retry_pool:
                        for k, (i, text) in enumerate(
                                retry_pool.map(_retry_run, still)):
                            if text:
                                texts[i] = text
                            # 런마다 시킹 4회까지 도는 가장 느린 단계 — 여기서
                            # 진행률이 멈추면 프론트가 스캔을 포기한다.
                            if k % 10 == 0 or k == len(still) - 1:
                                _stage_prog(len(still), k + 1)

                # ── 판독불가 블록 프레임 단위 귀속 — 서로 다른 라벨 사이 ''
                # 블록이 통째 앞 씬에 붙는 혼입(실기 HH0304 씬 48클립)의 근본
                # 수정. 아래 bounds 정렬은 양쪽이 읽힌 경계만 보므로, 한쪽이
                # ''인 경계는 여기서 먼저 없앤다(블록을 플립에서 갈라 병합).
                fp_cache: dict[int, object] = {}

                def _fp_at(fi: int):
                    fp = fp_cache.get(fi)
                    if fp is None:
                        fp = load_fingerprint(pngs[fi])
                        fp_cache[fi] = fp
                    return fp

                seek_texts: dict[int, str] = {}

                def _read_seek(fi: int) -> str:
                    if fi not in seek_texts:
                        _check_cancel()
                        dst = tmpdir / f"rb_{fi}.png"
                        extract_frame(ffmpeg, scan_src,
                                      frame_boundary_ms(fi, fps), dst,
                                      proc_key=str(external_id),
                                      region=tight_rel)
                        text = read_slate_line(dst, _DEFAULT_DELIMS,
                                               top_frac=1.0)
                        if not text:
                            pdst = tmpdir / f"rbp_{fi}.png"
                            extract_frame(ffmpeg, scan_src,
                                          frame_boundary_ms(fi, fps), pdst,
                                          proc_key=str(external_id),
                                          region=_FULL_REL)
                            text = (read_slate_line(pdst, _DEFAULT_DELIMS,
                                                    top_frac=1.0)
                                    or read_slate_line_rescaled(
                                        pdst, _DEFAULT_DELIMS, top_frac=1.0))
                        seek_texts[fi] = text
                    return seek_texts[fi]

                runs_f, texts = _resolve_unreadable_blocks(
                    runs_f, texts, picks, _DEFAULT_DELIMS, _fp_at, _read_seek)
                picks = [stable_frame(diffs, s, e) for s, e in runs_f]

                # 디졸브 경계 정렬 — 텍스트가 달라지는 컷마다 전후 프레임을 읽어
                # 슬레이트 가독 전환 프레임으로 옮긴다(_align_cut 참조). 전후
                # 프레임은 배치로 미리 뜨고, 걷기(드묾)만 개별 시킹한다.
                texts_c = canonicalize_texts(texts, _DEFAULT_DELIMS)
                bounds = [i for i in range(1, len(runs_f))
                          if texts_c[i - 1] and texts_c[i]
                          and texts_c[i - 1] != texts_c[i]]
                align_dir = tmpdir / "align"
                prefetch = (extract_frames_at(
                    ffmpeg, scan_src,
                    sorted({f for i in bounds
                            for f in (runs_f[i][0] - 1, runs_f[i][0])}),
                    align_dir, tight_rel, proc_key=str(external_id),
                    workers=_refine_workers()) if bounds else {})
                read_cache: dict[int, str] = {}

                def _read_frame(fi: int) -> str:
                    if fi in read_cache:
                        return read_cache[fi]
                    png = prefetch.get(fi)
                    if png is None:
                        png = align_dir / f"nb_{fi}.png"
                        extract_frame(ffmpeg, scan_src,
                                      frame_boundary_ms(fi, fps), png,
                                      proc_key=str(external_id),
                                      region=tight_rel)
                    text = read_slate_line(png, _DEFAULT_DELIMS, top_frac=1.0)
                    if not text:
                        # 패딩 재판독 — 경계 프레임의 판독 깜박임이 걷기·정렬의
                        # 근거를 지우던 것의 회수. 중간본 전체 프레임=패딩 구역.
                        pdst = align_dir / f"nbp_{fi}.png"
                        extract_frame(ffmpeg, scan_src,
                                      frame_boundary_ms(fi, fps), pdst,
                                      proc_key=str(external_id),
                                      region=_FULL_REL)
                        text = (read_slate_line(pdst, _DEFAULT_DELIMS,
                                                top_frac=1.0)
                                or read_slate_line_rescaled(
                                    pdst, _DEFAULT_DELIMS, top_frac=1.0))
                    read_cache[fi] = text
                    return text

                starts = [s for s, _e in runs_f]
                # ① 지문 유사도 정렬 — OCR이 못 읽는 페이드 프레임의 귀속을
                # 픽셀 잔상으로 판정한다(_fp_align 참조). 이동은 OCR 가독성으로
                # 캡(_clamp_fp_move). _fp_at은 위 블록 귀속과 캐시를 공유한다.
                for i in bounds:
                    _check_cancel()
                    aligned = _fp_align(
                        _fp_at, starts[i], _fp_at(picks[i - 1]), _fp_at(picks[i]),
                        lo=starts[i - 1], hi=runs_f[i][1])
                    if aligned is not None and aligned != starts[i]:
                        prev_t, next_t = texts_c[i - 1], texts_c[i]

                        def _side(fi: int, p=prev_t, n=next_t) -> str | None:
                            return _text_side(_read_frame(fi), p, n,
                                              _DEFAULT_DELIMS)

                        starts[i] = _clamp_fp_move(_side, starts[i], aligned)

                # ② OCR 정렬을 '마지막'에 — 읽히는 프레임의 소속은 OCR이 최종
                # 권위다. 유사도가 어떤 이유로든(캡의 판정 불가 프레임 등) 경계를
                # 어긋내면 여기서 교정된다(실기: 하드컷·선명 슬레이트 잔존 오차).
                for bi, i in enumerate(bounds):
                    _check_cancel()
                    starts[i] = _align_cut(
                        _read_frame, starts[i], texts_c[i - 1], texts_c[i],
                        lo=runs_f[i - 1][0], hi=runs_f[i][1],
                        delimiters=_DEFAULT_DELIMS)
                    if bi % 20 == 0 or bi == len(bounds) - 1:
                        save_scenes(external_id, _prog(
                            {"total_frames": total + len(bounds),
                             "ocr_done": total + bi + 1, "frames": [],
                             "thumb_count": thumb_count}))

                # 정렬 결과로 런 재구성 — 연속성 유지(끝=다음 시작), 극단적으로
                # 이웃 경계가 서로를 지나치면(짧은 런 양끝이 동시 이동) 단조 보정.
                for i in range(1, len(starts)):
                    starts[i] = max(starts[i], starts[i - 1] + 1)
                runs_f = [(starts[i],
                           starts[i + 1] if i + 1 < len(starts) else n_frames)
                          for i in range(len(runs_f))]
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

            # cut_diff=각 런을 연 컷의 지문 세기(정렬 후 최종 시작 프레임 기준) —
            # 판독불가 블록 귀속(runs_to_segments)의 유일한 판정 신호다.
            runs = [SceneRun(start_ms=frame_boundary_ms(s, fps),
                             end_ms=frame_boundary_ms(e, fps), text=t,
                             cut_diff=(diffs[s - 1]
                                       if 0 < s <= len(diffs) else 0))
                    for (s, e), t in zip(runs_f, texts)]
            return runs, thumb_count, n_frames

        try:
            runs, thumb_count, n_frames = await asyncio.to_thread(_work)
        finally:
            # 지문용 프레임은 수만 장이라 크고, 스캔 중간본도 수백 MB다 —
            # 실패해도 제거한다.
            shutil.rmtree(frames_dir, ignore_errors=True)
            try:
                scan_src.unlink()
            except OSError:
                pass

        save_scenes(external_id, {
            "scanning": False,
            "method": "fingerprint",
            "video_fps": fps,
            "total_ms": frame_boundary_ms(n_frames, fps),
            "thumb_interval_ms": thumb_interval_ms,
            "thumb_count": thumb_count,
            "frame_count": len(runs),
            "runs": [{"start_ms": r.start_ms, "end_ms": r.end_ms,
                      "text": r.text, "cut_diff": r.cut_diff} for r in runs],
            # frames는 토큰 선택 UI 호환용 — 런 시작 시각을 샘플로 노출한다.
            "frames": [{"t_ms": r.start_ms, "text": r.text} for r in runs],
            "ocr_region": region_out,
        })
    except StaleRunCancelled:
        logger.info("scene fp scan %s cancelled (gen %d)", external_id, generation)
        # 멈추는 쪽이 자기 플래그를 내린다(run_scene_scan과 동일 경합 방지).
        # 부분 판독은 남기지 않되 구역·방식 선택은 보존한다.
        save_scenes(external_id, {"scanning": False, "method": "fingerprint",
                                  "ocr_region": region_out})
    except Exception:  # noqa: BLE001
        if generation != _current_generation(external_id):
            # kill_active로 죽은 ffmpeg가 FfmpegError로 표면화된 경우 —
            # 세대가 넘어갔으면 실패가 아니라 취소이므로 조용히 정리한다.
            logger.info("scene fp scan %s cancelled mid-ffmpeg (gen %d)",
                        external_id, generation)
            return
        logger.exception("scene fp scan %s failed", external_id)
        try:
            save_scenes(external_id, {"scanning": False, "method": "fingerprint",
                                      "frames": [], "ocr_region": region_out,
                                      "error": "스캔에 실패했습니다. 서버 로그를 확인하세요."})
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        _BURN_SEMAPHORE.release()


async def run_scene_export(external_id: UUID, mode: str,
                           out_dir: str | None = None,
                           indices: list[int] | None = None) -> list[str]:
    """확정된 scenes.json 경계로 세그먼트를 재인코딩해 out_dir(미지정 시 잡
    디렉토리 scene_out/)에 슬레이트 라벨 파일명으로 저장한다. 저장 경로 목록 반환.

    indices를 주면 그 세그먼트만 다시 굽는다(부분 익스포트) — 경계를 고친 씬 하나와
    맞닿은 이웃만 갱신하려고 수백 개를 다시 인코딩하지 않게 한다. 파일명 dedupe는
    항상 '전체' 목록으로 계산하므로(아래) 부분 익스포트도 전체 익스포트와 같은
    파일명을 쓴다 — 선택분만으로 dedupe하면 중복 라벨의 접미사가 달라져 원본을
    갱신하지 못하고 유령 파일이 생긴다.

    진행률은 export_status.json에 증분 기록한다(exporting/done/total/error) —
    프론트가 폴링하며 진행바를 표시하고, 완료 시 exporting=False로 전환한다."""
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    try:
        data = load_scenes(external_id)
        if not data:
            raise RuntimeError("먼저 씬 스캔을 실행하세요.")
        key = "segments_sequence" if mode == "sequence" else "segments_scene"
        segments = data.get(key) or []
        if not segments:
            raise RuntimeError("자를 세그먼트가 없습니다 — 규칙을 확정하세요.")

        workdir = job_dir(external_id)
        burned = workdir / "burned.mp4"
        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")
        dest = Path(out_dir) if out_dir else (workdir / "scene_out")
        dest.mkdir(parents=True, exist_ok=True)
        # 컷 경계를 프레임 간 간격 중앙에 놓아 경계 프레임 중복/유실을 없앤다
        # (cut_segment 참조). 소스 전체가 동일 fps라 한 번만 프로브한다.
        fps = video_fps(ffmpeg, burned)
        # 부분 익스포트 대상 — 없으면 전체. 범위 밖 인덱스는 API가 거부하지만, 직접
        # 호출(테스트·스크립트)도 안전하게 무시한다.
        picked = ([i for i in sorted(set(indices)) if 0 <= i < len(segments)]
                  if indices is not None else list(range(len(segments))))
        total = len(picked)
        save_export_status(external_id, {"exporting": True, "done": 0,
                                         "total": total, "out_dir": str(dest),
                                         "error": None})
        # 어디에 쓰는지 로그로 남긴다 — "파일이 안 생긴다" 신고 시 실제 대상 폴더를
        # 확인하는 결정적 단서(사용자가 다른 폴더를 보고 있는지, 서버가 못 쓰는지).
        logger.info("scene export %s → %s (%d개 세그먼트%s, fps=%s)",
                    external_id, dest, total,
                    f" / 전체 {len(segments)} 중 부분" if indices is not None else "",
                    fps)

        def _work() -> list[str]:
            written: list[str] = []
            # 비단조 슬레이트 순서(예: 020→021→020)에서 같은 라벨이 인접하지
            # 않은 채로 두 번 나올 수 있다 — 전체 세그먼트를 미리 dedupe해
            # 파일명 충돌(덮어쓰기로 인한 데이터 손실)을 막는다. 부분 익스포트도
            # 전체 기준 dedupe를 그대로 써야 파일명이 전체 익스포트와 일치한다.
            deduped = dedupe_labels(
                [_sanitize_label(seg["label"]) for seg in segments])
            for done, i in enumerate(picked):
                seg = segments[i]
                if generation != _current_generation(external_id):
                    raise StaleRunCancelled(external_id)
                out_path = dest / f"{deduped[i]}.mp4"
                cut_segment(ffmpeg, burned, out_path,
                            seg["start_ms"], seg["end_ms"],
                            proc_key=str(external_id), fps=fps)
                # 컷 직후 실제 파일 생성을 검증 — ffmpeg가 코드 0으로 끝났는데도
                # 출력이 없거나 0바이트면(권한·경로·디스크·AV 격리 등) 조용히 넘어가면
                # "카운트만 오르고 파일이 없다"가 된다. 실패로 표면화해 원인을 남긴다.
                if not out_path.exists() or out_path.stat().st_size == 0:
                    raise RuntimeError(
                        f"익스포트 파일이 생성되지 않았습니다: {out_path} "
                        "— 저장 폴더의 쓰기 권한/경로를 확인하세요.")
                written.append(str(out_path))
                save_export_status(external_id, {"exporting": True, "done": done + 1,
                                                 "total": total,
                                                 "out_dir": str(dest), "error": None})
            logger.info("scene export %s 완료: %d개 파일 → %s",
                        external_id, len(written), dest)
            return written

        written = await asyncio.to_thread(_work)
        save_export_status(external_id, {"exporting": False, "done": total,
                                         "total": total, "out_dir": str(dest),
                                         "error": None, "files": written})
        return written
    except StaleRunCancelled:
        logger.info("scene export %s cancelled (gen %d)", external_id, generation)
        try:
            st = load_export_status(external_id) or {}
            save_export_status(external_id, {**st, "exporting": False})
        except Exception:  # noqa: BLE001 — 정리 실패가 취소 경로를 깨뜨리지 않게
            logger.exception("failed to clear exporting flag for %s", external_id)
        return []
    except Exception:  # noqa: BLE001
        if generation != _current_generation(external_id):
            # kill_active로 죽은 ffmpeg(cut_segment)가 FfmpegError로 표면화된
            # 경우 — 세대가 이미 넘어갔으면(취소·재생성) 실패가 아니라 취소이다.
            logger.info("scene export %s cancelled mid-ffmpeg (gen %d)",
                        external_id, generation)
            return []
        # fire-and-forget 태스크(start_job_task)라 재발생시키지 않는다 —
        # unretrieved task exception 경고를 피하고, run_burn_job과 달리 여기는
        # 반환값(경로 목록)이 실패 신호를 이미 겸한다.
        logger.exception("scene export %s failed", external_id)
        try:
            save_export_status(external_id, {"exporting": False, "error":
                                             "익스포트에 실패했습니다. 서버 로그를 확인하세요."})
        except Exception:  # noqa: BLE001
            pass
        return []
    finally:
        _BURN_SEMAPHORE.release()


def _clear_refining(external_id: UUID | str) -> None:
    """정밀화 종료(취소 포함) 시 진행 플래그를 내린다. 켜진 채 남으면 프론트가
    끝나지 않는 작업을 영원히 폴링한다."""
    try:
        st = load_refine_status(external_id) or {}
        save_refine_status(external_id, {**st, "refining": False})
    except Exception:  # noqa: BLE001 — 정리 실패가 취소 경로를 깨뜨리지 않게
        logger.exception("failed to clear refining flag for %s", external_id)


async def run_scene_refine(external_id: UUID, mode: str) -> None:
    """현재 모드 세그먼트의 각 경계를 이진탐색 OCR로 실제 전환 프레임까지 좁힌다.

    2초 샘플링 격자로는 컷이 ±1초 어긋나(이웃 시퀀스가 클립에 남음), 중앙정렬로
    반감해도 잔여가 있다. 경계마다 [b-half, b+half] 창을 이진탐색해 라벨이 next로
    바뀌는 지점(<1프레임 정밀도)을 찾아 경계를 그 프레임으로 옮긴다. 진행률은
    refine_status.json에 증분 기록한다(refining/done/total/error)."""
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    try:
        data = load_scenes(external_id)
        if not data or not data.get("rule"):
            raise RuntimeError("먼저 규칙을 확정하세요.")
        seg_key = "segments_sequence" if mode == "sequence" else "segments_scene"
        segments = [dict(s) for s in (data.get(seg_key) or [])]
        # 내부 경계 + (앞머리가 판독실패 구간이면) 첫 세그 시작도 정밀화 대상.
        total = (len(segments) - 1) + (1 if segments and
                                       segments[0]["start_ms"] > 0 else 0)
        if total < 1:
            save_refine_status(external_id, {"refining": False, "done": 0,
                                             "total": 0, "error": None})
            return
        rd = data["rule"]
        delimiters = rd.get("delimiters", ["_", "-"])
        indices = (rd["seq_tokens"] if mode == "sequence"
                   else rd["seq_tokens"] + rd.get("scene_tokens", []))
        upto = max(indices) if indices else -1
        interval_ms = data.get("interval_ms", 2000)
        burned = job_dir(external_id) / "burned.mp4"
        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")
        tmpdir = job_dir(external_id) / "refine_tmp"
        save_refine_status(external_id, {"refining": True, "done": 0,
                                         "total": total, "error": None})

        # 스캔과 같은 영역·밴드로 읽어야 경계가 흔들리지 않는다.
        region = load_ocr_region(external_id)
        band = _band_for(region)

        def label_at(t_ms: int) -> str:
            # 파일명에 스레드 id를 넣는다 — 병렬 워커가 같은 시각을 볼 때 서로의
            # 임시 프레임을 덮어쓰지 않도록.
            dst = tmpdir / f"r_{threading.get_ident()}_{t_ms}.png"
            extract_frame(ffmpeg, burned, t_ms, dst, proc_key=str(external_id),
                          region=region)
            text = read_slate_line(dst, delimiters, top_frac=band)
            try:
                dst.unlink()
            except OSError:
                pass
            toks = tokenize(text, delimiters) if text else []
            return build_label(toks, upto)

        # 경계 하나를 푼다 — '원래' 이웃 값만 보고 계산하며 segments를 건드리지
        # 않는다. 그래야 경계끼리 독립이 되어 병렬로 돌릴 수 있고(적용은 나중에
        # 한 번에), 결과가 순차 실행과 같다.
        def _solve(i: int) -> tuple[int, int] | None:
            if generation != _current_generation(external_id):
                raise StaleRunCancelled(external_id)
            if i == 0:
                # 첫 세그 시작 — 앞머리가 타이틀카드 등 판독실패 구간이면 첫 세그
                # 시작이 첫 유효 샘플에 붙어 실제 시작보다 최대 interval만큼 늦다
                # (실기 010 첫 1초=24프레임 유실). 판독실패("")는 라벨 불일치라
                # 이진탐색 오라클이 자연스럽게 '전환 전'으로 분류한다.
                b, floor = segments[0]["start_ms"], 0
                ceil_ms = segments[0]["end_ms"]
                label, other = segments[0]["label"], ""
            else:
                b = segments[i]["start_ms"]
                floor = segments[i - 1]["start_ms"]
                ceil_ms = segments[i]["end_ms"]
                label, other = segments[i]["label"], segments[i - 1]["label"]

            # 오독 내성 라벨 판정 — OCR이 구분자를 놓쳐 토큰이 붙어 읽혀도
            # ("HH0307_1200010"; 실기에서 경계 2초+ 지각) 같은 쪽으로 분류.
            def at_target(t_ms: int) -> bool:
                return label_matches(label_at(t_ms), label, other, delimiters)

            # 창을 ±interval로 넓힌다 — 스캔 프레임시각(fps 필터)과 컷/정밀화가
            # 쓰는 -ss 시각이 최대 ~1.5초 어긋나므로, ±half(±1초)로는 실제 전환을
            # 못 담는다(실측). 이웃 구간 범위로 클램프해 next-next로 넘치지 않게.
            lo = max(floor, b - interval_ms)
            hi = min(ceil_ms, b + interval_ms)
            # 창 시작이 이미 target이면 전환이 창보다 앞이다(오독 세그먼트가 직전에
            # 흡수돼 사전 경계가 지각한 실측 케이스) — 직전 구간 시작까지 창을
            # 왼쪽으로 확장한다(회당 2×interval, 유한 반복).
            for _ in range(8):
                if lo <= floor or not at_target(lo):
                    break
                lo = max(floor, lo - 2 * interval_ms)
            # 창 끝=target, 창 시작≠target 여야 전환이 창 안에 있다(아니면 중앙정렬
            # 유지). 종료 임계는 1프레임(50fps=20ms)보다 작아야 한다 — 150ms
            # (≈3.6프레임@23.976)로는 경계가 전환 프레임 뒤로 수렴해(실측 10/15
            # 지각) 새 시퀀스 첫 프레임들이 직전 클립 끝에 새 나간다.
            if not (at_target(hi) and not at_target(lo)):
                return None
            while hi - lo > 20:
                mid = (lo + hi) // 2
                if at_target(mid):
                    hi = mid
                else:
                    lo = mid
            return (i, hi)

        def _work() -> list[dict]:
            tmpdir.mkdir(parents=True, exist_ok=True)
            targets = list(range(1, len(segments)))
            if segments and segments[0]["start_ms"] > 0:
                targets.insert(0, 0)

            done = 0
            lock = threading.Lock()

            def _run_one(i: int):
                nonlocal done
                out = _solve(i)
                with lock:
                    done += 1
                    # 진행률 저장은 I/O라 매번 쓰지 않는다(병렬이면 더 잦다).
                    if done % 5 == 0 or done == total:
                        save_refine_status(external_id,
                                           {"refining": True, "done": done,
                                            "total": total, "error": None})
                return out

            # 경계는 서로 독립이고 병목이 ffmpeg 프레임 추출(실측 184ms, 판독의 4배)
            # 이라 병렬로 처리한다. 워커는 물리 코어 절반 수준으로 잡는다 — 더 늘리면
            # ffmpeg끼리 경합해 이득이 줄고 메모리(스레드당 OCR 엔진)만 는다.
            results: list[tuple[int, int] | None] = []
            with ThreadPoolExecutor(max_workers=_refine_workers()) as pool:
                futures = [pool.submit(_run_one, i) for i in targets]
                try:
                    for fut in futures:
                        results.append(fut.result())
                except BaseException:
                    for fut in futures:
                        fut.cancel()
                    raise

            # 적용은 순차로 한 번에 — 병렬 계산 중에는 segments를 건드리지 않았다.
            for out in results:
                if out is None:
                    continue
                i, new_start = out
                segments[i]["start_ms"] = new_start
                if i > 0:
                    segments[i - 1]["end_ms"] = new_start
            return segments

        refined = await asyncio.to_thread(_work)
        shutil.rmtree(tmpdir, ignore_errors=True)
        data[seg_key] = refined
        save_scenes(external_id, data)
        save_refine_status(external_id, {"refining": False, "done": total,
                                         "total": total, "error": None})
    except StaleRunCancelled:
        logger.info("scene refine %s cancelled (gen %d)", external_id, generation)
        # 멈추는 쪽이 자기 플래그를 내린다 — 취소 엔드포인트가 내려도 그 직후
        # 이 워커가 진행률을 다시 써 refining=true로 되살아나던 경합(실기).
        _clear_refining(external_id)
    except Exception:  # noqa: BLE001
        if generation != _current_generation(external_id):
            _clear_refining(external_id)
            return
        logger.exception("scene refine %s failed", external_id)
        try:
            save_refine_status(external_id, {"refining": False, "error":
                                             "경계 정밀화에 실패했습니다. 서버 로그를 확인하세요."})
        except Exception:  # noqa: BLE001
            pass
    finally:
        _BURN_SEMAPHORE.release()


# ────────────────────────── 경계 오류(혼입) 검사 ──────────────────────────
# 씬 모드 세그먼트의 머리·꼬리 프레임을 실제 익스포트 컷과 동일한 프레임 수식으로
# OCR해, 경계 프레임에 이웃 슬레이트가 잡히는(head/tail 혼입) 세그먼트를 표시한다.
# video_fps 미측정 시 NTSC 기본값(24000/1001).
_FALLBACK_FPS = 24000.0 / 1001.0


def _clear_checking(external_id: UUID | str) -> None:
    """경계 검사 종료(취소 포함) 시 진행 플래그를 내린다 — _clear_refining과 동일.
    켜진 채 남으면 프론트가 끝나지 않는 작업을 영원히 폴링한다."""
    try:
        st = load_boundary_status(external_id) or {}
        save_boundary_status(external_id, {**st, "checking": False})
    except Exception:  # noqa: BLE001 — 정리 실패가 취소 경로를 깨뜨리지 않게
        logger.exception("failed to clear checking flag for %s", external_id)


def _boundary_head_tail_ms(seg: dict, fps: float) -> tuple[int, int]:
    """세그먼트 머리·꼬리 프레임의 -ss 시각(ms). 익스포트 컷(cut_segment)과 동일한
    프레임 수식이라 OCR이 실제로 잘리는 프레임을 읽는다.

    머리는 start_ms(=첫 프레임의 frame_boundary_ms). 꼬리는 마지막 프레임 인덱스
    (head_idx + N - 1)의 frame_boundary_ms — N은 익스포트가 쓰는 -frames:v 개수
    round((end-start)*fps/1000). head_idx는 start_ms의 스냅업 프레임."""
    import math
    start_ms, end_ms = seg["start_ms"], seg["end_ms"]
    head_ms = start_ms
    head_idx = math.ceil(start_ms * fps / 1000.0 - 1e-6)
    n = round((end_ms - start_ms) * fps / 1000.0)
    last_idx = head_idx + n - 1
    tail_ms = frame_boundary_ms(last_idx, fps)
    return head_ms, tail_ms


def _sq(s: str) -> str:
    """라벨/OCR 텍스트를 영숫자 소문자만 남겨 정규화 — OCR이 밑줄을 공백으로 읽어도
    (HH0304_020_0220 vs 'HH0304 020 0220') 같은 키로 비교되게 한다."""
    return re.sub(r"[^0-9a-z]", "", s.lower())


def _classify_boundary(head_text: str, tail_text: str, label: str,
                       prev: str | None, next: str | None) -> tuple[bool, bool]:
    """머리/꼬리 혼입 판정. 경계 프레임 '전체 OCR 텍스트'에 이웃 씬의 번호열이
    나타나면 혼입으로 본다(밑줄/공백 무시하는 squash 부분일치) — 디졸브/와이프에서
    두 슬레이트가 겹쳐 보이는 오버랩과, 경계가 어긋나 이웃만 보이는 오배치를 모두
    잡는다. 머리 혼입 = 머리 프레임에 이전 라벨(P≠L)이 보임. 꼬리 혼입 = 꼬리
    프레임에 다음 라벨(X≠L)이 보임. 하드컷은 경계 프레임에 이웃 슬레이트가 없어
    잡히지 않는다.

    이웃 라벨은 '내 라벨을 한 번 걷어낸 나머지'에서 찾는다 — 이웃 라벨이 접두
    유실 오독('18A_S01')이면 그 문자열이 내 슬레이트 판독('Seq18A_S01-Panel5')
    안에 항상 들어 있어, 걷어내지 않으면 멀쩡한 경계가 통째로 혼입 취급된다
    (실기 EASA05). 디졸브 오버랩은 이웃 슬레이트가 별도 텍스트로 남으므로
    걷어낸 뒤에도 잡힌다. squash하면 빈 문자열이 되는 깨진 이웃('一·_,')은
    모든 텍스트에 '포함'되므로 판정 불가로 본다(빈 이웃 라벨과 동급)."""
    own = _sq(label)
    ht, tt = _sq(head_text), _sq(tail_text)
    if own:
        ht = ht.replace(own, "", 1)
        tt = tt.replace(own, "", 1)
    p_sq = _sq(prev) if prev else ""
    n_sq = _sq(next) if next else ""
    head_bad = bool(p_sq) and prev != label and p_sq in ht
    tail_bad = bool(n_sq) and next != label and n_sq in tt
    return head_bad, tail_bad


async def run_boundary_check(external_id: UUID) -> None:
    """씬 모드 세그먼트의 경계 프레임을 OCR해 head/tail 혼입 세그먼트를 표시한다.

    결과는 scenes.json data["boundary_issues"]에(플래그된 것만), 진행률은
    boundary_status.json에(checking/done/total/error) 증분 기록한다. 취소·세마포어
    규약은 run_scene_refine과 동일하다."""
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    try:
        data = load_scenes(external_id)
        segments = [dict(s) for s in ((data or {}).get("segments_scene") or [])]
        total = len(segments)
        ffmpeg = locate_ffmpeg()
        if total < 1 or ffmpeg is None:
            save_boundary_status(external_id, {"checking": False, "done": total,
                                               "total": total, "error": None})
            return
        fps = data.get("video_fps") or _FALLBACK_FPS
        burned = job_dir(external_id) / "burned.mp4"
        tmpdir = job_dir(external_id) / "boundary_tmp"
        # 스캔과 같은 구역으로 크롭해야 경계 판독이 흔들리지 않는다.
        region = load_ocr_region(external_id)
        save_boundary_status(external_id, {"checking": True, "done": 0,
                                           "total": total, "error": None})

        def text_at(t_ms: int) -> str:
            # 프레임 전체 OCR 텍스트(모든 라벨) — 오버랩에서 이웃 슬레이트도 본다.
            # 파일명에 스레드 id를 넣어 병렬 워커가 서로의 임시 프레임을 덮지 않게.
            dst = tmpdir / f"b_{threading.get_ident()}_{t_ms}.png"
            extract_frame(ffmpeg, burned, t_ms, dst, proc_key=str(external_id),
                          region=region)
            text = read_frame_text(dst)
            try:
                dst.unlink()
            except OSError:
                pass
            return text

        def _check_one(i: int) -> dict | None:
            if generation != _current_generation(external_id):
                raise StaleRunCancelled(external_id)
            seg = segments[i]
            label = seg["label"]
            prev = segments[i - 1]["label"] if i > 0 else None
            nxt = segments[i + 1]["label"] if i + 1 < len(segments) else None
            head_ms, tail_ms = _boundary_head_tail_ms(seg, fps)
            head_bad, tail_bad = _classify_boundary(
                text_at(head_ms), text_at(tail_ms), label, prev, nxt)
            if head_bad or tail_bad:
                return {"index": i, "label": label,
                        "head": head_bad, "tail": tail_bad}
            return None

        def _work() -> list[dict]:
            tmpdir.mkdir(parents=True, exist_ok=True)
            done = 0
            lock = threading.Lock()

            def _run_one(i: int):
                nonlocal done
                out = _check_one(i)
                with lock:
                    done += 1
                    # 진행률 저장은 I/O라 매번 쓰지 않는다(정밀화와 동일 5개마다).
                    if done % 5 == 0 or done == total:
                        save_boundary_status(external_id,
                                             {"checking": True, "done": done,
                                              "total": total, "error": None})
                return out

            results: list[dict | None] = []
            with ThreadPoolExecutor(max_workers=_refine_workers()) as pool:
                futures = [pool.submit(_run_one, i) for i in range(total)]
                try:
                    for fut in futures:
                        results.append(fut.result())
                except BaseException:
                    for fut in futures:
                        fut.cancel()
                    raise
            return [r for r in results if r is not None]

        issues = await asyncio.to_thread(_work)
        shutil.rmtree(tmpdir, ignore_errors=True)
        # 재로드 후 기록 — 검사 중 사용자가 세그먼트를 편집했을 수 있으므로 시작
        # 스냅샷을 덮어쓰지 않고 최신 scenes.json에 boundary_issues만 얹는다.
        latest = load_scenes(external_id) or data
        latest["boundary_issues"] = issues
        save_scenes(external_id, latest)
        save_boundary_status(external_id, {"checking": False, "done": total,
                                           "total": total, "error": None})
    except StaleRunCancelled:
        logger.info("boundary check %s cancelled (gen %d)", external_id, generation)
        # 멈추는 쪽이 자기 플래그를 내린다 — 취소 직후 이 워커의 진행률 저장이
        # checking=true를 되살리면 폴링이 안 끝난다(정밀화와 동일 경합).
        _clear_checking(external_id)
    except Exception:  # noqa: BLE001
        if generation != _current_generation(external_id):
            _clear_checking(external_id)
            return
        logger.exception("boundary check %s failed", external_id)
        try:
            save_boundary_status(external_id, {"checking": False, "error":
                                               "경계 오류 검사에 실패했습니다. 서버 로그를 확인하세요."})
        except Exception:  # noqa: BLE001
            pass
    finally:
        _BURN_SEMAPHORE.release()


def _sanitize_label(label: str) -> str:
    """파일명 안전화 — 경로 구분자·제어문자 제거. 공백은 유지(슬레이트 원문 존중),
    빈 라벨은 'segment'로 폴백."""
    bad = '/\\:*?"<>|\n\r\t'
    cleaned = "".join("_" if c in bad else c for c in label).strip()
    return cleaned or "segment"
