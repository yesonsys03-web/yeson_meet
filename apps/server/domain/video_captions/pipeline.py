"""Video caption job orchestration — 재수출 파사드.

2026-07-29 리팩토링으로 실제 구현은 기반 2개 + 러너 모듈로 나뉘었다(동작 변경 0):

  job_tasks       태스크 레지스트리·세대·세마포어·DB 상태 갱신·워커 수
  job_store       작업 폴더 경로·scenes/export/refine/boundary JSON·OCR 구역
  caption_run     run_video_job (인제스트→추출→전사→번역)
  burn_run        run_burn_job (자막 하드번)
  scene_scan      run_scene_scan·build_scene_data (간격 스캔)
  scene_scan_fp   run_scene_scan_fingerprint·build_fingerprint_segments (지문 스캔)
  scene_export    run_scene_export (세그먼트 재인코딩 저장)
  scene_refine    run_scene_refine (경계 이진탐색 정밀화)
  boundary_check  run_boundary_check (머리·꼬리 혼입 검사)
  maintenance     재시작 스윕·리텐션 프루닝

이 모듈은 기존 임포트 경로(main.py·api/v1/video_jobs.py·스크립트)를 그대로
살리는 이름 재수출만 한다. ★새 코드는 구현 모듈을 직접 임포트할 것 — 특히
monkeypatch는 파사드가 아니라 '코드가 사는 모듈'에 해야 닿는다.

Long-running per-job work runs as an asyncio task with its OWN
``AsyncSessionLocal()`` (the request session is closed by then) — same rule as
the report FTS background task. CPU-bound stages go through asyncio.to_thread.
StaleRunCancelled(취소·재생성 감지용 예외)는 pipeline↔transcribe 순환 임포트를
피해 transcribe.py에 정의돼 있고, 전사·굽기 진행 콜백이 공용으로 던진다.
"""
from __future__ import annotations

import logging

# 테스트가 pl.AsyncSessionLocal·pl.gpu_pack(.is_enabled 패치)을 읽는다.
from apps.server.db.session import AsyncSessionLocal  # noqa: F401
from . import gpu_pack  # noqa: F401
from .boundary_check import (  # noqa: F401
    _FALLBACK_FPS, _boundary_head_tail_ms, _classify_boundary, _clear_checking,
    run_boundary_check,
)
from .burn_run import run_burn_job  # noqa: F401
from .caption_run import run_video_job  # noqa: F401
from .job_store import (  # noqa: F401
    _TOP_BAND_DEFAULT, _band_for, boundary_status_path, export_status_path,
    job_dir, load_boundary_status, load_export_status, load_ocr_region,
    load_refine_status, load_scenes, refine_status_path, save_boundary_status,
    save_export_status, save_refine_status, save_scenes, scenes_json_path,
    video_jobs_root,
)
from .job_tasks import (  # noqa: F401
    _BURN_SEMAPHORE, _JOB_SEMAPHORE, _bump_generation, _current_generation,
    _load_job, _refine_workers, _set_progress, _set_status, _try_set_error,
    cancel_job_task, start_job_task, start_task,
)
from .maintenance import (  # noqa: F401
    _INFLIGHT_STATUSES, _another_instance_is_serving, _prune_pre_delete_hook,
    RETENTION_KEEP, clear_stale_scan_flags_at_startup,
    fail_inflight_video_jobs_at_startup, prune_old_video_jobs,
    prune_old_video_jobs_at_startup,
)
from .scene_export import _sanitize_label, run_scene_export  # noqa: F401
from .scene_refine import _clear_refining, run_scene_refine  # noqa: F401
from .scene_scan import (  # noqa: F401
    _DEFAULT_DELIMS, _SCAN_INTERVAL_S, build_scene_data, run_scene_scan,
)
from .scene_scan_fp import (  # noqa: F401
    _EXTRACT_TICK_S, _FP_FLANK_MAX_MS, _align_cut, _clamp_fp_move,
    _extract_tick, _fp_align, _pad_region, _relative_region,
    _resolve_unreadable_blocks, _text_side, STAGE_CROP, STAGE_CUTS,
    STAGE_FRAMES, STAGE_THUMBS, build_fingerprint_segments,
    run_scene_scan_fingerprint,
)

# 구현 모듈들과 같은 로거 — 분리 전 로그 계보 유지.
logger = logging.getLogger("yeson.video.pipeline")
