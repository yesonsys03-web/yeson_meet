"""영상 작업의 파일 저장소 — 작업 폴더 경로와 scenes/export/refine/boundary JSON.

pipeline.py에서 분리(2026-07-29 리팩토링, 동작 변경 0). 러너들이 진행률·결과를
증분 기록하고 프론트가 폴링해 읽는 파일들의 단일 출처다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID


def video_jobs_root() -> Path:
    root = os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")
    return Path(root) / "video_jobs"


def job_dir(external_id: UUID | str) -> Path:
    return video_jobs_root() / str(external_id)


def scenes_json_path(external_id: UUID | str) -> Path:
    return job_dir(external_id) / "scenes.json"


def save_scenes(external_id: UUID | str, data: dict) -> None:
    path = scenes_json_path(external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_scenes(external_id: UUID | str) -> dict | None:
    path = scenes_json_path(external_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_ocr_region(external_id: UUID | str) -> tuple | None:
    """저장된 OCR 영역(비율 x,y,w,h) — 사용자가 드래그로 지정한 슬레이트 구역.
    없으면 None(전체 프레임 + 상단 밴드 가정, 기존 동작)."""
    data = load_scenes(external_id) or {}
    r = data.get("ocr_region")
    if not r:
        return None
    try:
        return (float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"]))
    except (KeyError, TypeError, ValueError):
        return None


_TOP_BAND_DEFAULT = 0.35


# 크롭된 입력에서는 상단 밴드 가정을 쓰지 않는다 — 크롭 자체가 영역 필터다.
def _band_for(region: tuple | None) -> float:
    return 1.0 if region else _TOP_BAND_DEFAULT


def export_status_path(external_id: UUID | str) -> Path:
    return job_dir(external_id) / "export_status.json"


def save_export_status(external_id: UUID | str, data: dict) -> None:
    path = export_status_path(external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_export_status(external_id: UUID | str) -> dict | None:
    path = export_status_path(external_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def refine_status_path(external_id: UUID | str) -> Path:
    return job_dir(external_id) / "refine_status.json"


def save_refine_status(external_id: UUID | str, data: dict) -> None:
    path = refine_status_path(external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_refine_status(external_id: UUID | str) -> dict | None:
    path = refine_status_path(external_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def boundary_status_path(external_id: UUID | str) -> Path:
    return job_dir(external_id) / "boundary_status.json"


def save_boundary_status(external_id: UUID | str, data: dict) -> None:
    path = boundary_status_path(external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_boundary_status(external_id: UUID | str) -> dict | None:
    path = boundary_status_path(external_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
