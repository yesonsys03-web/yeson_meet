"""Whisper model catalog / download / delete endpoints.

Deliberately UNAUTHENTICATED (product decision 2026-07-06): this deployment
treats the LAN as the trust boundary, extending the same acceptance already
made for viewer tokens and the /video-jobs capability URLs.
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from apps.server.ai.apple_native import APPLE_TRANSCRIBE_MODEL, apple_stt_available
from apps.server.domain.video_captions import gpu_pack
from apps.server.domain.video_captions import whisper_models as wm

router = APIRouter(tags=["video-models"], prefix="/video-models")


def _spawn_download(name: str) -> None:  # test seam
    threading.Thread(target=wm.download_model, args=(name,), daemon=True).start()


def _spawn_gpu_pack_download() -> None:  # test seam
    threading.Thread(target=gpu_pack.download_pack, daemon=True).start()


@router.get("")
async def list_video_models() -> dict:
    models = wm.list_models()
    # Apple 온디바이스 전사 모델은 항상 목록에 노출한다(번역 엔진과 동일 정책).
    # 인텔맥/윈도우/구버전 macOS에서는 available=False로만 표시돼 클라가 회색
    # 비활성 처리 — 플랫폼별로 항목 자체가 사라지던 비대칭을 없앤다.
    apple_ok = apple_stt_available()
    models.insert(0, {
        "name": APPLE_TRANSCRIBE_MODEL,
        "label": "Apple 온디바이스 (실리콘맥, 초고속)",
        "approx_bytes": 0, "downloaded": apple_ok, "disk_bytes": 0,
        "downloading": False, "progress": None,
        "builtin": True,        # 클라: 다운로드/삭제 버튼 숨김 플래그
        "available": apple_ok,  # 이 기기에서 실제 선택 가능한지(불가 시 비활성)
    })
    return {"models": models}


# GPU 라우트는 /{name} 계열 동적 라우트보다 먼저 선언 — 선언 순서 매칭 규약
# (translate-engines와 동일 이유, video_jobs.py 참고).

@router.get("/gpu")
async def gpu_status() -> dict:
    return gpu_pack.status()


@router.post("/gpu/pack", status_code=status.HTTP_202_ACCEPTED)
async def download_gpu_pack() -> dict:
    if not gpu_pack.is_supported():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "GPU 팩은 Windows+NVIDIA 서버에서만 지원됩니다.")
    if gpu_pack.is_installed():
        return {"status": "already_installed"}
    if gpu_pack._downloading:
        return {"status": "downloading"}
    _spawn_gpu_pack_download()
    return {"status": "started"}


class GpuEnableIn(BaseModel):
    enabled: bool


@router.post("/gpu/enable")
async def set_gpu_enabled(body: GpuEnableIn) -> dict:
    if body.enabled and not gpu_pack.is_installed():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "GPU 팩이 설치되어 있지 않습니다. 먼저 다운로드하세요.")
    gpu_pack.set_enabled(body.enabled)
    return {"enabled": gpu_pack.is_enabled()}


@router.post("/{name}/download", status_code=status.HTTP_202_ACCEPTED)
async def download_video_model(name: str) -> dict:
    if name not in wm.CATALOG:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown model")
    if wm.is_downloaded(name):
        return {"status": "already_downloaded"}
    if wm._downloading.get(name):
        return {"status": "downloading"}
    _spawn_download(name)
    return {"status": "started"}


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video_model(name: str) -> None:
    if name not in wm.CATALOG:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown model")
    try:
        wm.delete_model(name)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
