"""PDF 번역 작업의 파일 저장소 경로 (video_captions/job_store.py와 동형)."""
from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID


def pdf_jobs_root() -> Path:
    root = os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")
    return Path(root) / "pdf_jobs"


def pdf_job_dir(external_id: UUID | str) -> Path:
    return pdf_jobs_root() / str(external_id)
