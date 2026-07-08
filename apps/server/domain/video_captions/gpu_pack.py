"""CUDA "GPU 팩" — Windows NVIDIA 전사 가속용 cuBLAS/cuDNN DLL 옵트인 관리.

faster-whisper(CTranslate2)의 CUDA 실행은 cuBLAS(CUDA 12)+cuDNN 9 DLL을
런타임에 요구하는데, 수백 MB라 프로즌 번들에는 넣지 않는다. whisper 모델과
같은 패턴으로 공식 NVIDIA PyPI 휠에서 DLL만 추출해
``{STORAGE_ROOT}/gpu_pack/bin`` 에 두고, 전사 직전 ``os.add_dll_directory``
로 로드 경로에 추가한다. 사용 여부는 ``enabled`` 플래그 파일(옵트인).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path

from .whisper_models import DEFAULT_STORAGE_ROOT, STORAGE_ROOT_ENV

logger = logging.getLogger("yeson.video.gpu_pack")

WHISPER_DEVICE_ENV = "YESON_WHISPER_DEVICE"  # cpu | cuda (미설정=옵트인 플래그 따름)

# ctranslate2 4.x 요구사항: CUDA 12용 cuBLAS + cuDNN 9 (공식 NVIDIA PyPI 휠)
_WHEELS = ("nvidia-cublas-cu12", "nvidia-cudnn-cu12")
APPROX_PACK_BYTES = 1_300_000_000  # 두 휠 합계 대략치(실측 528+703MB) — UI 안내용

_downloading = False
_progress = 0  # 0-99, 다운로드 중일 때만 의미
_last_error: str | None = None  # 마지막 다운로드 실패 사유 — UI 표면화용
_state_lock = threading.Lock()
_cuda_checked: bool | None = None  # cuda_available() 결과 캐시(프로세스 수명)
_activated = False


def pack_root() -> Path:
    return Path(os.environ.get(STORAGE_ROOT_ENV, DEFAULT_STORAGE_ROOT)) / "gpu_pack"


def bin_dir() -> Path:
    return pack_root() / "bin"


def is_supported() -> bool:
    """GPU 팩 자체가 의미 있는 플랫폼인지 — CTranslate2 CUDA는 Windows(nt)+NVIDIA 전용
    시나리오만 지원한다(mac Metal/AMD ROCm 미지원)."""
    return os.name == "nt"


def is_installed() -> bool:
    d = bin_dir()
    if not d.is_dir():
        return False
    names = [p.name.lower() for p in d.glob("*.dll")]
    return any(n.startswith("cublas") for n in names) and any(
        n.startswith("cudnn") for n in names)


def is_enabled() -> bool:
    return (pack_root() / "enabled").is_file()


def set_enabled(enabled: bool) -> None:
    flag = pack_root() / "enabled"
    if enabled:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("1", encoding="utf-8")
    else:
        flag.unlink(missing_ok=True)


def gpu_name() -> str | None:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        out = subprocess.run(
            [smi, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    name = (out.stdout or "").strip().splitlines()
    return name[0].strip() if out.returncode == 0 and name else None


def activate() -> None:
    """추출한 DLL 디렉터리를 로드 경로에 추가 — ctranslate2 import 전에 호출해야 한다."""
    global _activated
    if _activated or os.name != "nt":
        return
    d = bin_dir()
    if d.is_dir():
        os.add_dll_directory(str(d))  # type: ignore[attr-defined]
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
        _activated = True


def _cuda_device_count() -> int:  # test seam
    import ctranslate2

    return ctranslate2.get_cuda_device_count()


def cuda_available() -> bool:
    """실제 CUDA 디바이스 인식 여부. 무거운 검사라 첫 성공/실패를 프로세스 수명 캐시."""
    global _cuda_checked
    if _cuda_checked is None:
        try:
            activate()
            _cuda_checked = _cuda_device_count() > 0
        except Exception:
            logger.warning("cuda_available: ctranslate2 CUDA 검사 실패", exc_info=True)
            _cuda_checked = False
    return _cuda_checked


def resolve_device() -> tuple[str, str]:
    """전사 (device, compute_type) 결정. env 강제 > 옵트인 플래그+팩 설치+CUDA 인식 > CPU."""
    env = os.environ.get(WHISPER_DEVICE_ENV, "").strip().lower()
    if env == "cpu":
        return ("cpu", "int8")
    if env == "cuda":
        activate()
        return ("cuda", "float16")
    if is_enabled() and is_installed() and cuda_available():
        return ("cuda", "float16")
    return ("cpu", "int8")


def _http_get_json(url: str) -> dict:  # test seam
    # requests(+certifi) 사용 — whisper 모델 다운로드(huggingface_hub→requests)로
    # 프로즌 번들·Windows에서 이미 검증된 HTTP 스택. urllib은 번들 환경 의존이 커서 금지.
    import requests

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _wheel_url(package: str) -> tuple[str, int]:
    """PyPI JSON API에서 최신 버전의 win_amd64 휠 (url, size)를 고른다."""
    data = _http_get_json(f"https://pypi.org/pypi/{package}/json")
    for f in data.get("urls", []):
        name = f.get("filename", "")
        if name.endswith(".whl") and "win_amd64" in name:
            return f["url"], int(f.get("size") or 0)
    raise RuntimeError(f"{package}: win_amd64 휠을 찾지 못했습니다")


def _download_file(url: str, dest: Path, progress_cb) -> None:  # test seam
    import requests

    with requests.get(url, stream=True, timeout=60) as resp, open(dest, "wb") as out:
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=1 << 20):
            out.write(chunk)
            progress_cb(len(chunk))


def _extract_dlls(wheel_path: Path, dest: Path) -> int:
    """휠(zip)에서 DLL만 평탄하게 추출한다 (nvidia/*/bin/*.dll 레이아웃)."""
    count = 0
    with zipfile.ZipFile(wheel_path) as zf:
        for member in zf.namelist():
            if not member.lower().endswith(".dll"):
                continue
            target = dest / Path(member).name
            with zf.open(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count


def download_pack() -> None:
    """Blocking download — callers run this in a worker thread. 중복 호출은 no-op.

    실패는 삼키지 않고 _last_error에 남긴다 — 워커 스레드 예외는 UI에 안 보여서
    "버튼 눌러도 반응 없음"이 되기 때문(2026-07-08 Windows 실기기 증상)."""
    global _downloading, _progress, _cuda_checked, _last_error
    with _state_lock:
        if _downloading:
            logger.info("download_pack: already downloading — skip")
            return
        _downloading = True
        _progress = 0
        _last_error = None
    try:
        targets = [(pkg, *_wheel_url(pkg)) for pkg in _WHEELS]
        total = sum(size for _, _, size in targets) or 1
        done = 0

        def on_bytes(n: int) -> None:
            nonlocal done
            done += n
            global _progress
            _progress = min(99, int(done * 100 / total))

        dest = bin_dir()
        dest.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            for pkg, url, _size in targets:
                logger.info("download_pack: %s 다운로드 시작", pkg)
                wheel = Path(tmp) / f"{pkg}.whl"
                _download_file(url, wheel, on_bytes)
                n = _extract_dlls(wheel, dest)
                logger.info("download_pack: %s → DLL %d개 추출", pkg, n)
                wheel.unlink(missing_ok=True)
        _cuda_checked = None  # 설치 후 CUDA 재검사 허용
        logger.info("download_pack: 완료 (%s)", dest)
    except Exception as exc:  # noqa: BLE001 — 워커 스레드 최종 방어선
        logger.exception("download_pack: 실패")
        _last_error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        _downloading = False


def status() -> dict:
    installed = is_installed()
    return {
        "supported": is_supported(),
        "gpu_name": gpu_name(),
        "installed": installed,
        "downloading": _downloading,
        "progress": _progress if _downloading else None,
        "cuda_available": cuda_available() if installed else False,
        "enabled": is_enabled(),
        "approx_bytes": APPROX_PACK_BYTES,
        "last_error": _last_error,
    }
