"""로컬 번역 모델(Qwen) 카탈로그 + 사용자 다운로드/삭제 관리.

자막메이커 번역용. 런타임은 플랫폼별로 자동 선택되며 translate_cli.create_translator의
기준과 동일하다:
- 실리콘맥 → MLX (`snapshot_download` → {STORAGE_ROOT}/mlx_models/<repo>)
- 그 외(윈도·인텔맥) → Ollama (:11434 /api/pull)

whisper_models.py 패턴(데몬 스레드 블로킹 다운로드 + 인메모리 _downloading/진행률 +
클라 폴링)을 그대로 미러한다. 티어 값(qwen/qwen_lite/qwen_hifi)은 번역 드롭다운·
create_translator와 공유한다.
"""
from __future__ import annotations

import logging
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from apps.server.ai.apple_native import _is_apple_silicon_mac
from apps.server.ai.mlx_live_translate import mlx_model_dir, mlx_model_installed
from . import translate_ollama as to
from .translate_mlx import QWEN_MLX_MODELS

logger = logging.getLogger("yeson.video.translate_models")


@dataclass(frozen=True)
class _Tier:
    name: str          # qwen / qwen_lite / qwen_hifi (드롭다운·create_translator와 공유)
    label: str
    mlx_bytes: int
    ollama_bytes: int


# 라벨은 translate_cli.list_translate_engines의 qwen 라벨과 동일하게 유지.
_TIERS: tuple[_Tier, ...] = (
    _Tier("qwen", "Qwen 9B (로컬)", 5_000_000_000, 6_600_000_000),
    _Tier("qwen_lite", "Qwen 4B (로컬·빠름)", 2_300_000_000, 3_400_000_000),
    _Tier("qwen_hifi", "Qwen 9B (로컬·고품질 8bit)", 10_000_000_000, 10_000_000_000),
)
_TIER_BY_NAME: dict[str, _Tier] = {t.name: t for t in _TIERS}

# name -> True while a download thread runs; name -> pct (Ollama pull only).
_downloading: dict[str, bool] = {}
_progress: dict[str, int] = {}
_state_lock = threading.Lock()


def runtime() -> str:
    """이 서버에서 로컬 번역이 쓸 런타임 — 실리콘=mlx, 그 외=ollama."""
    return "mlx" if _is_apple_silicon_mac() else "ollama"


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _mlx_repo(name: str) -> str:
    return QWEN_MLX_MODELS[name]


def _snapshot_download(repo_id: str, local_dir: str) -> None:  # test seam
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=repo_id, local_dir=local_dir)


def is_installed(name: str) -> bool:
    if runtime() == "mlx":
        return mlx_model_installed(_mlx_repo(name))
    return to.qwen_ollama_available(to.qwen_ollama_model(name))


def download_model(name: str) -> None:
    """블로킹 다운로드 — 호출부가 워커 스레드에서 실행. 중복 실행은 no-op."""
    _TIER_BY_NAME[name]  # KeyError for unknown names is intentional
    with _state_lock:
        if _downloading.get(name):
            logger.info("download_model(%s): already downloading — skip", name)
            return
        _downloading[name] = True
        _progress[name] = 0
    rt = runtime()
    try:
        if rt == "mlx":
            repo = _mlx_repo(name)
            dest = mlx_model_dir(repo)
            dest.mkdir(parents=True, exist_ok=True)
            logger.info("download_model(%s): MLX snapshot %s", name, repo)
            _snapshot_download(repo, str(dest))
        else:
            tag = to.qwen_ollama_model(name)
            logger.info("download_model(%s): ollama pull %s", name, tag)
            to.pull_model(tag, on_progress=lambda pct: _progress.__setitem__(name, pct))
        logger.info("download_model(%s): done", name)
    finally:
        with _state_lock:
            _downloading[name] = False
            _progress.pop(name, None)


def delete_model(name: str) -> None:
    _TIER_BY_NAME[name]
    with _state_lock:
        if _downloading.get(name):
            raise RuntimeError(f"모델 '{name}'은(는) 다운로드 중이라 삭제할 수 없습니다.")
    if runtime() == "mlx":
        shutil.rmtree(mlx_model_dir(_mlx_repo(name)), ignore_errors=True)
    else:
        to.delete_model(to.qwen_ollama_model(name))


def _progress_for(name: str, rt: str, approx: int) -> int | None:
    if not _downloading.get(name):
        return None
    if rt == "mlx":
        # MLX(snapshot_download)은 콜백이 없어 디스크 크기로 추정(whisper와 동일).
        disk = _dir_size(mlx_model_dir(_mlx_repo(name)))
        return min(99, int(disk * 100 / approx)) if approx else None
    return _progress.get(name, 0)


def list_models() -> dict:
    rt = runtime()
    ollama_run = to.ollama_running() if rt == "ollama" else True
    ollama_inst = to.ollama_installed() if rt == "ollama" else True
    models: list[dict] = []
    for t in _TIERS:
        approx = t.mlx_bytes if rt == "mlx" else t.ollama_bytes
        models.append({
            "name": t.name,
            "label": t.label,
            "runtime": rt,
            "approx_bytes": approx,
            "downloaded": is_installed(t.name),
            "downloading": _downloading.get(t.name, False),
            "progress": _progress_for(t.name, rt, approx),
            # Ollama 런타임인데 미실행이면 다운로드 불가(먼저 Ollama 실행/설치 필요).
            "downloadable": rt == "mlx" or ollama_run,
        })
    return {
        "models": models,
        "runtime": rt,
        "ollama_installed": ollama_inst,
        "ollama_running": ollama_run,
    }
