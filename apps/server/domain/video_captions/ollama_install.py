"""Ollama 공식 설치 프로그램 다운로드 + 실행 (반자동 설치).

플랫폼별 공식 설치 파일을 **서버 머신**에 받아 실행한다. Ollama 자체 설치기가 GPU
런타임·자동시작(서비스/에이전트)·업데이트를 처리하므로, 우리는 `ollama serve` 데몬
수명주기를 관리하지 않는다(그 부류의 고아/포트 사고를 피한다).

설치 완료 후 Ollama가 :11434를 띄우면 translate_ollama가 감지하고 모델 다운로드가
가능해진다. gpu_pack.py의 다운로드+진행률+상태 패턴을 미러(httpx 지연 import).

주의: 설치는 이 서버가 도는 머신에서 진행된다. 클라이언트와 서버가 다른 머신이면
설치 프로그램은 서버 머신 화면에 뜬다(클라는 안내 문구로 이를 알린다).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

logger = logging.getLogger("yeson.video.ollama_install")

STORAGE_ROOT_ENV = "STORAGE_ROOT"
DEFAULT_STORAGE_ROOT = "/var/lib/yeson-meet/storage"

# 공식 안정 URL(ollama.com/download/*)은 최신 GitHub 릴리스 자산으로 307 리다이렉트된다.
_INSTALLERS: dict[str, tuple[str, str]] = {
    "darwin": ("https://ollama.com/download/Ollama-darwin.zip", "Ollama-darwin.zip"),
    "windows": ("https://ollama.com/download/OllamaSetup.exe", "OllamaSetup.exe"),
}

_state: dict = {"downloading": False, "progress": 0, "launched": False, "last_error": None}
_state_lock = threading.Lock()


def _host() -> str | None:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "windows"
    return None  # 리눅스는 공식 스크립트 설치 권장 — 자동화 대상 아님


def is_supported() -> bool:
    return _host() is not None


def install_dir() -> Path:
    root = os.environ.get(STORAGE_ROOT_ENV, DEFAULT_STORAGE_ROOT)
    return Path(root) / "ollama_installer"


def status() -> dict:
    with _state_lock:
        return {"supported": is_supported(), **_state}


def _download(url: str, dest: Path, on_progress) -> None:  # test seam
    import httpx

    with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length") or 0)
        got = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(262_144):
                f.write(chunk)
                got += len(chunk)
                if total:
                    on_progress(min(99, int(got * 100 / total)))


def _launch(installer: Path) -> None:  # test seam
    host = _host()
    if host == "windows":
        os.startfile(str(installer))  # type: ignore[attr-defined]  # Windows 설치 GUI 실행
        return
    # macOS: zip을 풀어 Ollama.app을 실행(첫 실행 시 앱이 이동/서버 시작을 안내).
    target = install_dir() / "app"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(installer) as z:
        z.extractall(target)
    app = target / "Ollama.app"
    subprocess.Popen(["open", str(app)])


def download_and_launch() -> None:
    """블로킹 — 호출부(스레드)에서 실행. 공식 설치 파일을 받아 실행한다."""
    host = _host()
    if host is None:
        with _state_lock:
            _state["last_error"] = "이 플랫폼은 자동 설치를 지원하지 않습니다(수동 설치 필요)."
        return
    with _state_lock:
        if _state["downloading"]:
            return
        _state.update(downloading=True, progress=0, launched=False, last_error=None)
    url, fname = _INSTALLERS[host]
    dest_dir = install_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / fname
    try:
        _download(url, dest, lambda p: _state.__setitem__("progress", p))
        _launch(dest)
        with _state_lock:
            _state.update(launched=True, progress=100)
        logger.info("ollama installer launched: %s", dest)
    except Exception as exc:  # noqa: BLE001 — 실패는 상태로 표면화(클라가 표시)
        with _state_lock:
            _state["last_error"] = str(exc)
        logger.exception("ollama installer download/launch failed")
    finally:
        with _state_lock:
            _state["downloading"] = False
