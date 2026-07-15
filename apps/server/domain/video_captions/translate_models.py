"""로컬 번역 모델(Qwen) 사용자 다운로드/삭제 관리.

자막메이커 번역용. 티어 정의는 translate_catalog가 단일 출처이며(빌트인 + 원격
오버레이), 이 모듈은 다운로드/삭제/진행률만 책임진다. 런타임은 플랫폼별로 자동
선택되며 translate_cli.create_translator의 기준과 동일하다:
- 실리콘맥 → MLX (`snapshot_download` → {STORAGE_ROOT}/mlx_models/<repo>)
- 그 외(윈도·인텔맥) → Ollama (:11434 /api/pull)

whisper_models.py 패턴(데몬 스레드 블로킹 다운로드 + 인메모리 _downloading/진행률 +
클라 폴링)을 그대로 미러한다.
"""
from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from apps.server.ai.mlx_live_translate import mlx_model_dir, mlx_model_installed

from . import ollama_install as oi
from . import translate_catalog as tcat
from . import translate_ollama as to

logger = logging.getLogger("yeson.video.translate_models")

# name -> True while a download thread runs; name -> pct (Ollama pull only).
_downloading: dict[str, bool] = {}
_progress: dict[str, int] = {}
_state_lock = threading.Lock()


def runtime() -> str:
    """이 서버에서 로컬 번역이 쓸 런타임 — 실리콘=mlx, 그 외=ollama."""
    return tcat.runtime()


def _entry(name: str) -> tcat.TranslateModel:
    return tcat.get_catalog()[name]  # KeyError for unknown names is intentional


def _require_supported(name: str) -> tcat.TranslateModel:
    """이 서버의 런타임을 지원하지 않는 티어면 RuntimeError.

    UI 비활성에만 의존하지 않는다 — /translate-models는 무인증(LAN 신뢰경계)이라
    UI를 거치지 않는 호출이 정상 경로다. 가드가 없으면 MLX 전용 티어를 윈도우에
    POST했을 때 tag=None이 pull_model까지 흘러 {"model": null} 요청이 나간다.
    """
    entry = _entry(name)
    reason = tcat.unsupported_reason(entry)
    if reason:
        raise RuntimeError(f"모델 '{name}'은(는) 이 서버에서 사용할 수 없습니다({reason}).")
    return entry


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _snapshot_download(repo_id: str, local_dir: str) -> None:  # test seam
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=repo_id, local_dir=local_dir)


def is_installed(name: str) -> bool:
    entry = _entry(name)
    if runtime() == "mlx":
        # mlx_repo가 None이면 mlx_model_installed가 None.replace()로 터진다.
        return bool(entry.mlx_repo) and mlx_model_installed(entry.mlx_repo)
    return to.qwen_ollama_available(to.qwen_ollama_model(name))


def download_model(name: str) -> None:
    """블로킹 다운로드 — 호출부가 워커 스레드에서 실행. 중복 실행은 no-op."""
    entry = _require_supported(name)
    with _state_lock:
        if _downloading.get(name):
            logger.info("download_model(%s): already downloading — skip", name)
            return
        _downloading[name] = True
        _progress[name] = 0
    rt = runtime()
    try:
        if rt == "mlx":
            dest = mlx_model_dir(entry.mlx_repo)
            dest.mkdir(parents=True, exist_ok=True)
            logger.info("download_model(%s): MLX snapshot %s", name, entry.mlx_repo)
            _snapshot_download(entry.mlx_repo, str(dest))
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
    entry = _require_supported(name)
    with _state_lock:
        if _downloading.get(name):
            raise RuntimeError(f"모델 '{name}'은(는) 다운로드 중이라 삭제할 수 없습니다.")
    if runtime() == "mlx":
        shutil.rmtree(mlx_model_dir(entry.mlx_repo), ignore_errors=True)
    else:
        to.delete_model(to.qwen_ollama_model(name))


def _progress_for(name: str, rt: str, entry: tcat.TranslateModel, approx: int) -> int | None:
    if not _downloading.get(name):
        return None
    if rt == "mlx":
        # MLX(snapshot_download)은 콜백이 없어 디스크 크기로 추정(whisper와 동일).
        disk = _dir_size(mlx_model_dir(entry.mlx_repo))
        return min(99, int(disk * 100 / approx)) if approx else None
    return _progress.get(name, 0)


def list_models() -> dict:
    rt = runtime()
    ollama_run = to.ollama_running() if rt == "ollama" else True
    ollama_inst = to.ollama_installed() if rt == "ollama" else True
    models: list[dict] = []
    for entry in tcat.get_catalog().values():
        reason = tcat.unsupported_reason(entry)
        approx = entry.mlx_bytes if rt == "mlx" else entry.ollama_bytes
        models.append({
            "name": entry.name,
            "label": entry.label,
            "runtime": rt,
            "approx_bytes": approx,
            "downloaded": False if reason else is_installed(entry.name),
            "downloading": _downloading.get(entry.name, False),
            "progress": None if reason else _progress_for(entry.name, rt, entry, approx),
            # 이 서버의 런타임을 아예 지원하지 않는 티어면 사유(클라가 회색 비활성).
            # None인데 downloaded=False면 그냥 미설치 — 다운로드하면 쓸 수 있다.
            "reason": reason,
            # 라이브 자막 패널(server_desktop)이 MLX 리포 id로 모델을 식별하고,
            # mlx_bytes로 용량(≈RAM)을 표시한다. approx_bytes는 이 서버의 런타임
            # 값이라 Ollama 서버에서는 MLX 용량을 알 수 없으므로 별도로 싣는다.
            "mlx_repo": entry.mlx_repo,
            "mlx_bytes": entry.mlx_bytes,
            "ollama_tag": entry.ollama_tag,
            # Ollama 런타임인데 미실행이면 다운로드 불가(먼저 Ollama 실행/설치 필요).
            "downloadable": not reason and (rt == "mlx" or ollama_run),
        })
    return {
        "models": models,
        "runtime": rt,
        "ollama_installed": ollama_inst,
        "ollama_running": ollama_run,
        # 반자동 설치 상태(ollama 런타임에서만) — 미설치 시 클라가 '설치' 버튼 표시.
        "ollama_install": oi.status() if rt == "ollama" else None,
    }
