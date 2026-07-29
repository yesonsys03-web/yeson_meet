"""씬 간격 스캔 러너 — burned.mp4 프레임 샘플 OCR + 경계 계산 데이터 구성.

pipeline.py에서 분리(2026-07-29 리팩토링, 동작 변경 0). 지문 스캔은
scene_scan_fp, 경계 정밀화는 scene_refine 참조.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from .ffmpeg import extract_frames, extract_thumbnails, locate_ffmpeg
from .job_store import _band_for, job_dir, load_ocr_region, save_scenes
from .job_tasks import (
    _BURN_SEMAPHORE, _bump_generation, _current_generation, _refine_workers,
)
from .scene_split import FrameSample, SlateRule, compute_boundaries, hold_keys
from .slate_ocr import read_slate_line
from .transcribe import StaleRunCancelled

# 로거 이름은 분리 전과 동일하게 유지한다(job_tasks 참조).
logger = logging.getLogger("yeson.video.pipeline")


def build_scene_data(samples: list[FrameSample], rule_dict: dict,
                     total_ms: int, min_ms: int = 2000) -> dict:
    """프레임 샘플 + 규칙 → scenes.json 본문(양 모드 경계 포함). 순수 함수."""
    rule = SlateRule(
        delimiters=rule_dict.get("delimiters", ["_", " ", "-"]),
        seq_tokens=rule_dict["seq_tokens"],
        scene_tokens=rule_dict.get("scene_tokens", []),
    )
    # 샘플 간격 — 경계 중앙정렬·1샘플 흡수 판정에 쓴다(스캔은 균일 간격).
    interval_ms = (samples[1].t_ms - samples[0].t_ms) if len(samples) >= 2 else 2000
    scene_keyed = hold_keys(samples, rule, "scene")
    seq_keyed = hold_keys(samples, rule, "sequence")
    # 씬 모드: 경계 중앙정렬만(짧은 진짜 컷이 있을 수 있어 1샘플 흡수는 안 함).
    seg_scene = compute_boundaries(scene_keyed, total_ms, min_ms,
                                   interval_ms=interval_ms)
    # 시퀀스 모드: 중앙정렬 + 내부 1샘플 고립 흡수(오독 제거 — 시퀀스는 1샘플일 리 없음).
    seg_seq = compute_boundaries(seq_keyed, total_ms, min_ms,
                                 interval_ms=interval_ms, absorb_single=True)
    return {
        "rule": rule_dict,
        "frames": [{"t_ms": s.t_ms, "text": s.text} for s in samples],
        "segments_scene": [
            {"label": s.label, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in seg_scene],
        "segments_sequence": [
            {"label": s.label, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in seg_seq],
    }


# 슬레이트 스캔 기본 규칙 — 사용자가 규칙 지정 전, 첫 스캔은 규칙 없이 프레임
# 텍스트만 수집한다(경계는 규칙 확정 시 계산한다). 구분자는 관측된 두 포맷 커버.
# 기본 구분자에서 공백은 제외한다(`_`, `-`만). 슬레이트 필드 구분은 `_`/`-`이고
# 공백은 필드 "안"에 들어가는 경우가 많다(예: "Seq 11B", "Panel 3"). OCR이 같은
# 슬레이트에서 공백을 들쭉날쭉 읽으면("Seq01A" vs "Seq 11B") 공백 분해 시 토큰
# 인덱스가 프레임마다 어긋나 고정 인덱스 규칙이 깨진다(실기 관측). 공백을 필드
# 구분자로 쓰는 슬레이트는 규칙의 delimiters로 명시 지정한다(UI 토글).
# "/"는 OCR이 "_"를 어긋 읽는 상수적 오독(실기 075/0080·120/0010) —
# 구분자로 취급하면 오독 텍스트도 같은 토큰으로 쪼개져 키가 정렬된다.
_DEFAULT_DELIMS = ["_", "-", "/"]

# 스캔 프레임 샘플 간격(초). 슬레이트는 한 샷 내내 떠 있으므로 촘촘히 볼 필요가
# 없다. 2초면 경계 정밀도는 충분하고 긴 영상의 OCR 프레임 수를 절반으로 줄인다
# (22분 영상 실측: 1초=1316프레임 → 2초=658프레임). 경계는 필름스트립에서 수동
# 조정 가능하고, 후속으로 경계 근처만 프레임 단위 재탐색할 수 있다.
_SCAN_INTERVAL_S = 2.0


async def run_scene_scan(external_id: UUID,
                         interval_s: float = _SCAN_INTERVAL_S) -> None:
    """burned.mp4에서 프레임을 추출·OCR해 프레임별 슬레이트 텍스트를 모아
    scenes.json에 저장한다. 경계는 규칙 확정(/scenes/rule) 때 계산한다.

    긴 영상은 OCR이 오래 걸리므로 진행률을 scenes.json에 증분 기록한다
    (`scanning`/`total_frames`/`ocr_done`) — 프론트가 폴링하며 표시하고, 완료 시
    `scanning=False`로 전환한다. 진짜 실패 시 `error`를 기록해 프론트 폴링을
    멈춘다. 취소(세대 변경)는 아무 것도 기록하지 않는다(다음 실행이 덮어씀).
    스캔은 굽기와 세마포어를 공유해 배타적으로 돈다."""
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    try:
        workdir = job_dir(external_id)
        burned = workdir / "burned.mp4"
        if not burned.exists():
            raise RuntimeError("굽기 완료본(burned.mp4)이 없습니다.")
        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")

        frames_dir = workdir / "scene_frames"
        thumbs_dir = workdir / "scene_thumbs"
        # 이전 스캔 잔여 제거
        for d in (frames_dir, thumbs_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

        interval_ms = int(interval_s * 1000)
        # 썸네일 간격은 스캔(OCR) 간격과 분리한다 — 스캔을 0.25s로 촘촘히 떠도
        # 썸네일까지 그러면 필름스트립이 수천 칸이 된다. 썸네일은 최소 2s로 성기게.
        thumb_interval_s = max(2.0, interval_s)
        thumb_interval_ms = int(thumb_interval_s * 1000)
        # 사용자가 지정한 슬레이트 구역 — scenes.json을 덮어쓰기 전에 읽어두고,
        # 이후 모든 저장에 되실어 재스캔해도 지정이 사라지지 않게 한다.
        region = load_ocr_region(external_id)
        band = _band_for(region)
        region_out = ({"x": region[0], "y": region[1],
                       "w": region[2], "h": region[3]} if region else None)

        def _prog(extra: dict) -> dict:
            return {"scanning": True, "interval_ms": interval_ms,
                    "thumb_interval_ms": thumb_interval_ms,
                    "ocr_region": region_out, **extra}

        def _work() -> tuple[list[FrameSample], int]:
            extract_frames(ffmpeg, burned, frames_dir, interval_s,
                           proc_key=str(external_id), region=region)
            extract_thumbnails(ffmpeg, burned, thumbs_dir, thumb_interval_s,
                               proc_key=str(external_id))
            thumb_count = len(list(thumbs_dir.glob("thumb_*.jpg")))
            pngs = sorted(frames_dir.glob("frame_*.png"))
            total = len(pngs)
            # 진행률 초기화 — 긴 영상은 OCR이 오래 걸려 프론트가 폴링하며 표시한다.
            save_scenes(external_id, _prog({"total_frames": total, "ocr_done": 0,
                                            "frames": [], "thumb_count": thumb_count}))
            samples: list[FrameSample] = []
            # 촘촘한 스캔은 OCR 호출이 많으므로 병렬화한다(정밀화와 같은 이유·설정).
            def _read(ipng):
                i, png = ipng
                if generation != _current_generation(external_id):
                    raise StaleRunCancelled(external_id)
                return i, read_slate_line(png, _DEFAULT_DELIMS, top_frac=band)

            texts: dict[int, str] = {}
            done = 0
            with ThreadPoolExecutor(max_workers=_refine_workers()) as pool:
                for i, text in pool.map(_read, enumerate(pngs)):
                    texts[i] = text
                    done += 1
                    # 증분 진행률 — 매 프레임 쓰면 I/O 과다라 20개마다(+마지막).
                    if done % 20 == 0 or done == total:
                        save_scenes(external_id, _prog(
                            {"total_frames": total, "ocr_done": done,
                             "frames": [], "thumb_count": thumb_count}))
            samples = [FrameSample(index=i, t_ms=i * interval_ms,
                                   text=texts.get(i, "")) for i in range(total)]
            return samples, thumb_count

        try:
            samples, thumb_count = await asyncio.to_thread(_work)
        finally:
            # OCR용 원본 프레임은 크므로 제거(썸네일만 남긴다) — 실패해도 제거한다.
            shutil.rmtree(frames_dir, ignore_errors=True)

        save_scenes(external_id, {
            "scanning": False,
            "interval_ms": interval_ms,
            "thumb_interval_ms": thumb_interval_ms,
            "thumb_count": thumb_count,
            "frame_count": len(samples),
            "frames": [{"t_ms": s.t_ms, "text": s.text} for s in samples],
            "ocr_region": region_out,
        })
    except StaleRunCancelled:
        logger.info("scene scan %s cancelled (gen %d)", external_id, generation)
        # 멈추는 쪽이 자기 플래그를 내린다(정밀화와 동일한 경합) — 취소 직후
        # 이 워커의 진행률 저장이 scanning=true를 되살리면 폴링이 안 끝난다.
        # 부분 판독은 남기지 않되(완료로 오인 방지) 구역 설정은 보존한다.
        save_scenes(external_id, {"scanning": False, "interval_ms": interval_ms,
                                  "ocr_region": region_out})
    except Exception:  # noqa: BLE001
        if generation != _current_generation(external_id):
            # kill_active로 죽은 ffmpeg(extract_frames/extract_thumbnails)가
            # FfmpegError로 표면화된 경우 — 세대가 이미 넘어갔으면(취소·재생성)
            # 실패가 아니라 취소이므로 조용히 정리한다.
            logger.info("scene scan %s cancelled mid-ffmpeg (gen %d)",
                        external_id, generation)
            return
        logger.exception("scene scan %s failed", external_id)
        # 진짜 실패 — error를 기록해 프론트 폴링이 멈추게 한다(3분 헛대기 방지).
        try:
            save_scenes(external_id, {"scanning": False, "frames": [],
                                      "error": "스캔에 실패했습니다. 서버 로그를 확인하세요."})
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        _BURN_SEMAPHORE.release()
