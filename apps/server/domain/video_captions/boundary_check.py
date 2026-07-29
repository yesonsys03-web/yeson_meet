"""경계 오류(혼입) 검사 러너 — 씬 세그먼트의 머리·꼬리 프레임 OCR(run_boundary_check).

pipeline.py에서 분리(2026-07-29 리팩토링, 동작 변경 0). 익스포트 컷과 동일한
프레임 수식(_boundary_head_tail_ms)이 이 검사의 근거다.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from .ffmpeg import extract_frame, locate_ffmpeg
from .fingerprint import frame_boundary_ms
from .job_store import (
    job_dir, load_boundary_status, load_ocr_region, load_scenes,
    save_boundary_status, save_scenes,
)
from .job_tasks import (
    _BURN_SEMAPHORE, _bump_generation, _current_generation, _refine_workers,
)
from .slate_ocr import read_frame_text
from .transcribe import StaleRunCancelled

# 로거 이름은 분리 전과 동일하게 유지한다(job_tasks 참조).
logger = logging.getLogger("yeson.video.pipeline")


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
