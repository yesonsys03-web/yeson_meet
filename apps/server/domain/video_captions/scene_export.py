"""씬 익스포트 러너 — 확정된 경계로 세그먼트를 재인코딩해 저장(run_scene_export).

pipeline.py에서 분리(2026-07-29 리팩토링, 동작 변경 0).
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from uuid import UUID

from .ffmpeg import cut_segment, locate_ffmpeg, video_fps
from .job_store import (
    job_dir, load_export_status, load_scenes, save_export_status,
)
from .job_tasks import _BURN_SEMAPHORE, _bump_generation, _current_generation
from .scene_split import dedupe_labels
from .transcribe import StaleRunCancelled

# 로거 이름은 분리 전과 동일하게 유지한다(job_tasks 참조).
logger = logging.getLogger("yeson.video.pipeline")


def _sanitize_label(label: str) -> str:
    """파일명 안전화 — 경로 구분자·제어문자 제거. 공백은 유지(슬레이트 원문 존중),
    빈 라벨은 'segment'로 폴백."""
    bad = '/\\:*?"<>|\n\r\t'
    cleaned = "".join("_" if c in bad else c for c in label).strip()
    return cleaned or "segment"


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
