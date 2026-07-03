"""faster-whisper model catalog + user-driven download management.

Models are NOT bundled. Users pick/download from the client tab; files land in
``{STORAGE_ROOT}/whisper_models/{name}`` so the frozen server bundle stays small.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("yeson.video.whisper_models")

STORAGE_ROOT_ENV = "STORAGE_ROOT"
DEFAULT_STORAGE_ROOT = "/var/lib/yeson-meet/storage"


@dataclass(frozen=True)
class ModelInfo:
    repo_id: str
    approx_bytes: int
    label: str


CATALOG: dict[str, ModelInfo] = {
    "tiny": ModelInfo("Systran/faster-whisper-tiny", 75_000_000, "가장 빠름, 초벌용"),
    "base": ModelInfo("Systran/faster-whisper-base", 145_000_000, "빠름, 짧은 영상"),
    "small": ModelInfo("Systran/faster-whisper-small", 486_000_000, "권장 기본값 (품질/속도 균형)"),
    "medium": ModelInfo("Systran/faster-whisper-medium", 1_530_000_000, "고품질, 느림"),
    "large-v3": ModelInfo("Systran/faster-whisper-large-v3", 3_090_000_000, "최고 품질, 가장 느림"),
}

# name -> True while a download thread is running
_downloading: dict[str, bool] = {}


def models_root() -> Path:
    return Path(os.environ.get(STORAGE_ROOT_ENV, DEFAULT_STORAGE_ROOT)) / "whisper_models"


def model_dir(name: str) -> Path:
    return models_root() / name


def is_downloaded(name: str) -> bool:
    return (model_dir(name) / "model.bin").is_file()


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _snapshot_download(repo_id: str, local_dir: str) -> None:  # test seam
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=repo_id, local_dir=local_dir)


def download_model(name: str) -> None:
    """Blocking download — callers run this in a worker thread."""
    info = CATALOG[name]  # KeyError for unknown names is intentional
    dest = model_dir(name)
    dest.mkdir(parents=True, exist_ok=True)
    _downloading[name] = True
    try:
        _snapshot_download(info.repo_id, str(dest))
    finally:
        _downloading[name] = False


def delete_model(name: str) -> None:
    CATALOG[name]
    shutil.rmtree(model_dir(name), ignore_errors=True)


def list_models() -> list[dict]:
    out: list[dict] = []
    for name, info in CATALOG.items():
        disk = _dir_size(model_dir(name))
        downloading = _downloading.get(name, False)
        out.append({
            "name": name,
            "label": info.label,
            "approx_bytes": info.approx_bytes,
            "downloaded": is_downloaded(name),
            "disk_bytes": disk,
            "downloading": downloading,
            "progress": min(99, int(disk * 100 / info.approx_bytes)) if downloading else None,
        })
    return out
