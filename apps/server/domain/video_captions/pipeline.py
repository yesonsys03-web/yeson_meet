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
from .scene_export import (  # noqa: F401 — 파사드 재수출
    _sanitize_label, run_scene_export,
)
from .scene_scan import (  # noqa: F401 — 파사드 재수출
    _DEFAULT_DELIMS, _SCAN_INTERVAL_S, build_scene_data, run_scene_scan,
)
from .scene_scan_fp import (  # noqa: F401 — 파사드 재수출(테스트가 순수 헬퍼를 직접 읽는다)
    _EXTRACT_TICK_S, _FP_FLANK_MAX_MS, _align_cut, _clamp_fp_move,
    _extract_tick, _fp_align, _pad_region, _relative_region,
    _resolve_unreadable_blocks, _text_side, STAGE_CROP, STAGE_CUTS,
    STAGE_FRAMES, STAGE_THUMBS, build_fingerprint_segments,
    run_scene_scan_fingerprint,
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
