"""Whisper model catalog / download / delete endpoints.

Deliberately UNAUTHENTICATED (product decision 2026-07-06): this deployment
treats the LAN as the trust boundary, extending the same acceptance already
made for viewer tokens and the /video-jobs capability URLs.
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, status

from apps.server.domain.video_captions import whisper_models as wm

router = APIRouter(tags=["video-models"], prefix="/video-models")


def _spawn_download(name: str) -> None:  # test seam
    threading.Thread(target=wm.download_model, args=(name,), daemon=True).start()


@router.get("")
async def list_video_models() -> dict:
    return {"models": wm.list_models()}


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
