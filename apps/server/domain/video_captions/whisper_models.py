"""faster-whisper model catalog + user-driven download management.

Models are NOT bundled. Users pick/download from the client tab; files land in
``{STORAGE_ROOT}/whisper_models/{name}`` so the frozen server bundle stays small.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from apps.server.domain.video_captions import remote_catalog

logger = logging.getLogger("yeson.video.whisper_models")

STORAGE_ROOT_ENV = "STORAGE_ROOT"
DEFAULT_STORAGE_ROOT = "/var/lib/yeson-meet/storage"


@dataclass(frozen=True)
class ModelInfo:
    repo_id: str
    approx_bytes: int
    label: str


BUILTIN_CATALOG: dict[str, ModelInfo] = {
    "tiny": ModelInfo("Systran/faster-whisper-tiny", 75_000_000, "가장 빠름, 초벌용"),
    "base": ModelInfo("Systran/faster-whisper-base", 145_000_000, "빠름, 짧은 영상"),
    "small": ModelInfo("Systran/faster-whisper-small", 486_000_000, "권장 기본값 (품질/속도 균형)"),
    "medium": ModelInfo("Systran/faster-whisper-medium", 1_530_000_000, "고품질, 느림"),
    "large-v3": ModelInfo("Systran/faster-whisper-large-v3", 3_090_000_000, "최고 품질, 가장 느림"),
    "large-v3-turbo": ModelInfo(
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        1_620_000_000, "고품질·고속 (large급 품질, 약 5~8배 빠름)"),
}


def get_catalog() -> dict[str, ModelInfo]:
    """빌트인 baseline에 원격 카탈로그(디스크 캐시)를 오버레이한 유효 목록.

    네트워크는 타지 않는다 — 원격 갱신은 /video-models 엔드포인트가 담당한다.
    원격은 새 이름 추가·기존 이름 오버라이드만 가능하고 빌트인 삭제는 불가하다.
    """
    merged = dict(BUILTIN_CATALOG)
    for m in remote_catalog.cached_models():
        merged[m.name] = ModelInfo(m.repo_id, m.approx_bytes, m.label)
    return merged

# name -> True while a download thread is running
_downloading: dict[str, bool] = {}
_state_lock = threading.Lock()


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
    """Blocking download — callers run this in a worker thread. Idempotent no-op if already downloading."""
    info = get_catalog()[name]  # KeyError for unknown names is intentional
    with _state_lock:
        if _downloading.get(name):
            logger.info("download_model(%s): already downloading — skip", name)
            return
        _downloading[name] = True
    dest = model_dir(name)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        logger.info("download_model(%s): start (%s)", name, info.repo_id)
        _snapshot_download(info.repo_id, str(dest))
        logger.info("download_model(%s): done", name)
    finally:
        _downloading[name] = False


def delete_model(name: str) -> None:
    get_catalog()[name]
    with _state_lock:
        if _downloading.get(name):
            raise RuntimeError(f"모델 '{name}'은(는) 다운로드 중이라 삭제할 수 없습니다.")
    shutil.rmtree(model_dir(name), ignore_errors=True)


def list_models() -> list[dict]:
    out: list[dict] = []
    for name, info in get_catalog().items():
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
