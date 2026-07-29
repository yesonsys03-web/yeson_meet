"""씬 경계 정밀화 러너 — 이진탐색 OCR로 경계를 프레임 단위로 좁힌다(run_scene_refine).

pipeline.py에서 분리(2026-07-29 리팩토링, 동작 변경 0). 병렬 워커 수는
job_tasks._refine_workers가 단일 출처다(스캔·경계 검사와 공유).
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from .ffmpeg import extract_frame, locate_ffmpeg
from .job_store import (
    _band_for, job_dir, load_ocr_region, load_refine_status, load_scenes,
    save_refine_status, save_scenes,
)
from .job_tasks import (
    _BURN_SEMAPHORE, _bump_generation, _current_generation, _refine_workers,
)
from .scene_split import build_label, label_matches, tokenize
from .slate_ocr import read_slate_line
from .transcribe import StaleRunCancelled

# 로거 이름은 분리 전과 동일하게 유지한다(job_tasks 참조).
logger = logging.getLogger("yeson.video.pipeline")


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
