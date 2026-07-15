"""로컬 번역 모델(Qwen) 카탈로그/다운로드/삭제 엔드포인트.

전사 모델(video_models.py)과 동일하게 UNAUTHENTICATED — LAN 신뢰 경계 정책
(뷰어 토큰·/video-jobs capability URL과 동일 수용). 런타임(MLX/Ollama)은
서버 플랫폼에 따라 translate_models가 자동 선택한다.
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from apps.server.domain.video_captions import ollama_install as oinst
from apps.server.domain.video_captions import translate_catalog as tcat
from apps.server.domain.video_captions import translate_models as tmods

router = APIRouter(tags=["translate-models"], prefix="/translate-models")


def _spawn_download(name: str) -> None:  # test seam
    threading.Thread(target=tmods.download_model, args=(name,), daemon=True).start()


def _spawn_ollama_install() -> None:  # test seam
    threading.Thread(target=oinst.download_and_launch, daemon=True).start()


@router.post("/ollama/install", status_code=status.HTTP_202_ACCEPTED)
async def install_ollama() -> dict:
    """공식 Ollama 설치 프로그램을 서버 머신에 받아 실행(반자동). 설치는 서버에서 진행됨."""
    if not oinst.is_supported():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "이 서버 플랫폼은 자동 설치를 지원하지 않습니다. ollama.com에서 수동 설치하세요.")
    if oinst.status()["downloading"]:
        return {"status": "downloading"}
    _spawn_ollama_install()
    return {"status": "started"}


@router.get("")
async def list_translate_models(refresh: bool = False) -> dict:
    # 원격 카탈로그 갱신은 블로킹 requests.get이므로 스레드풀로 오프로드(루프 정지 방지).
    # force=False면 TTL 캐시가 신선할 때 네트워크를 타지 않는다(탭 열 때 갱신).
    await run_in_threadpool(tcat.get_remote_models, refresh)
    return tmods.list_models()


@router.post("/{name}/download", status_code=status.HTTP_202_ACCEPTED)
async def download_translate_model(name: str) -> dict:
    catalog = tcat.get_catalog()
    if name not in catalog:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown model")
    # 이 서버의 런타임을 지원하지 않는 티어는 거부 — 무인증 API라 UI 비활성에만
    # 의존할 수 없다. 통과시키면 tag=None이 pull_model까지 흘러간다.
    reason = tcat.unsupported_reason(catalog[name])
    if reason:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"모델 '{name}'은(는) 이 서버에서 사용할 수 없습니다({reason}).")
    if tmods.is_installed(name):
        return {"status": "already_downloaded"}
    if tmods._downloading.get(name):
        return {"status": "downloading"}
    # Ollama 런타임인데 서버가 안 떠 있으면 pull 자체가 불가 — 명확히 409로 안내.
    if tmods.runtime() == "ollama" and not tmods.to.ollama_running():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Ollama 서버가 실행 중이 아닙니다. Ollama를 설치·실행한 뒤 다시 시도하세요.")
    _spawn_download(name)
    return {"status": "started"}


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_translate_model(name: str) -> None:
    if name not in tcat.get_catalog():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown model")
    try:
        tmods.delete_model(name)   # 미지원 런타임·다운로드 중이면 RuntimeError
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
