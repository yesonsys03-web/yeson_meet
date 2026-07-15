"""Remote whisper catalog overlay — fetch/validate/cache an optional model list.

새 whisper 모델을 앱 재배포 없이 추가하기 위한 오버레이. 리포 main의
``whisper_catalog.remote.json``을 TTL 캐시로 fetch해 빌트인에 병합한다
(병합은 whisper_models.get_catalog()). 빌트인은 항상 유지되는 baseline이며,
원격은 추가/오버라이드만 한다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("yeson.video.remote_catalog")

DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/yesonsys03-web/yeson_meet/main/"
    "apps/server/domain/video_captions/whisper_catalog.remote.json"
)
CATALOG_URL_ENV = "YESON_WHISPER_CATALOG_URL"
CACHE_TTL_SECONDS = 6 * 3600
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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


def _now() -> float:  # test seam
    return time.time()


def _http_get(url: str) -> str:  # test seam
    import requests
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


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
        if (not isinstance(name, str) or not _NAME_RE.match(name)
                or name in (".", "..")
                or not isinstance(repo_id, str) or not repo_id
                or not isinstance(approx, int) or isinstance(approx, bool) or approx <= 0
                or not isinstance(label, str)):
            logger.warning("remote_catalog: skip invalid entry: %r", item)
            continue
        out.append(RemoteModel(name=name, repo_id=repo_id, approx_bytes=approx, label=label))
    return out


def _read_cache() -> tuple[float, list[RemoteModel]] | None:
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text("utf-8"))
        fetched_at = float(blob.get("fetched_at", 0))
        return fetched_at, _parse(blob)
    except Exception as exc:  # 손상된 캐시는 없는 것으로 취급
        logger.warning("remote_catalog: bad cache file: %s", exc)
        return None


def _write_cache(models: list[RemoteModel]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": _now(), "models": [asdict(m) for m in models]}
    path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")


def cached_models() -> list[RemoteModel]:
    """디스크 캐시만 읽어 반환(네트워크 없음·예외 없음). get_catalog() 병합용."""
    try:
        cached = _read_cache()
    except Exception as exc:
        logger.warning("remote_catalog: cache read failed: %s", exc)
        return []
    return cached[1] if cached else []


def get_remote_models(force: bool = False) -> list[RemoteModel]:
    """원격 카탈로그의 검증된 모델 목록. 네트워크/파싱 실패 시 캐시→빈 목록으로 폴백(예외 없음)."""
    try:
        cached = _read_cache()
    except Exception as exc:  # 캐시 읽기 실패도 "캐시 없음"으로 취급 — 절대 raise 금지
        logger.warning("remote_catalog: cache read failed: %s", exc)
        cached = None
    if not force and cached is not None:
        fetched_at, models = cached
        if _now() - fetched_at < CACHE_TTL_SECONDS:
            return models
    url = _catalog_url()
    if not url.startswith("https://"):
        logger.warning("remote_catalog: non-https url ignored: %s", url)
        return cached[1] if cached else []
    try:
        text = _http_get(url)
        models = _parse(json.loads(text))
        _write_cache(models)
        return models
    except Exception as exc:
        logger.warning("remote_catalog: fetch failed (%s); using cache/builtin", exc)
        return cached[1] if cached else []
