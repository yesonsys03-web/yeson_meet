"""Remote whisper catalog overlay — fetch/validate/cache an optional model list.

새 whisper 모델을 앱 재배포 없이 추가하기 위한 오버레이. 리포 main의
``whisper_catalog.remote.json``을 TTL 캐시로 fetch해 빌트인에 병합한다
(병합은 whisper_models.get_catalog()). 빌트인은 항상 유지되는 baseline이며,
원격은 추가/오버라이드만 한다.

네트워크·캐시·TTL은 catalog_fetch 코어가 담당하고, 이 모듈은 whisper 스키마
(name/repo_id/approx_bytes/label)의 검증만 책임진다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from . import catalog_fetch

logger = logging.getLogger("yeson.video.remote_catalog")

DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/yesonsys03-web/yeson_meet/main/"
    "apps/server/domain/video_captions/whisper_catalog.remote.json"
)
CATALOG_URL_ENV = "YESON_WHISPER_CATALOG_URL"
# 하위호환 re-export — 기존 호출부/테스트가 remote_catalog.CACHE_TTL_SECONDS를 참조한다.
CACHE_TTL_SECONDS = catalog_fetch.CACHE_TTL_SECONDS


@dataclass(frozen=True)
class RemoteModel:
    name: str
    repo_id: str
    approx_bytes: int
    label: str


def _catalog_url() -> str:
    return os.environ.get(CATALOG_URL_ENV, DEFAULT_CATALOG_URL)


def _cache_path() -> Path:
    # 지역 import — whisper_models가 이 모듈을 import하므로 순환 방지.
    from apps.server.domain.video_captions.whisper_models import models_root
    return models_root() / "remote_catalog.cache.json"


def _parse(payload: object) -> list[RemoteModel]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("models")
    if not isinstance(raw, list):
        return []
    out: list[RemoteModel] = []
    for item in raw:
        if not isinstance(item, dict):
            logger.warning("remote_catalog: skip non-dict entry: %r", item)
            continue
        name = item.get("name")
        repo_id = item.get("repo_id")
        approx = item.get("approx_bytes")
        label = item.get("label")
        if (not catalog_fetch.valid_name(name)
                or not isinstance(repo_id, str) or not repo_id
                or not isinstance(approx, int) or isinstance(approx, bool) or approx <= 0
                or not isinstance(label, str)):
            logger.warning("remote_catalog: skip invalid entry: %r", item)
            continue
        out.append(RemoteModel(name=name, repo_id=repo_id, approx_bytes=approx, label=label))
    return out


def cached_models() -> list[RemoteModel]:
    """디스크 캐시만 읽어 반환(네트워크 없음·예외 없음). get_catalog() 병합용."""
    return catalog_fetch.cached_entries(_cache_path, _parse)


def get_remote_models(force: bool = False) -> list[RemoteModel]:
    """원격 카탈로그의 검증된 모델 목록. 실패 시 캐시→빈 목록 폴백(예외 없음)."""
    return catalog_fetch.get_entries(_catalog_url, _cache_path, _parse, asdict, force)
