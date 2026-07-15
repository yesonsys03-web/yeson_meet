"""원격 카탈로그 fetch/캐시 코어 — 스키마 비의존.

whisper(remote_catalog.py)와 번역(translate_catalog.py)이 공유한다. 스키마는
parse_fn/dump_fn 주입으로 분리하고, 이 모듈은 네트워크·TTL 캐시·https 가드·
폴백만 책임진다.

URL과 캐시 경로를 값이 아니라 **함수**로 받는다 — 둘 다 STORAGE_ROOT 등 환경변수와
소비자 모듈에 의존해 import 시점에 확정할 수 없다(호출을 늦춰 순환 import도 피한다).
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("yeson.video.catalog_fetch")

CACHE_TTL_SECONDS = 6 * 3600
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _now() -> float:  # test seam
    return time.time()


def _http_get(url: str) -> str:  # test seam
    import requests

    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def valid_name(name: object) -> bool:
    """카탈로그 항목 이름 — 경로 조각으로 쓰이므로 정규식 + 점 이름을 거부한다."""
    return (isinstance(name, str) and bool(NAME_RE.match(name))
            and name not in (".", ".."))


def _read_cache(cache_path_fn: Callable[[], Path],
                parse_fn: Callable[[object], list]) -> tuple[float, list] | None:
    path = cache_path_fn()
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text("utf-8"))
        fetched_at = float(blob.get("fetched_at", 0))
        return fetched_at, parse_fn(blob)
    except Exception as exc:  # 손상된 캐시는 없는 것으로 취급
        logger.warning("catalog_fetch: bad cache file %s: %s", path, exc)
        return None


def _write_cache(cache_path_fn: Callable[[], Path], entries: list,
                 dump_fn: Callable[[object], dict]) -> None:
    path = cache_path_fn()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": _now(), "models": [dump_fn(e) for e in entries]}
    path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")


def cached_entries(cache_path_fn: Callable[[], Path],
                   parse_fn: Callable[[object], list]) -> list:
    """디스크 캐시만 읽어 반환(네트워크 없음·예외 없음)."""
    try:
        cached = _read_cache(cache_path_fn, parse_fn)
    except Exception as exc:
        logger.warning("catalog_fetch: cache read failed: %s", exc)
        return []
    return cached[1] if cached else []


def get_entries(url_fn: Callable[[], str], cache_path_fn: Callable[[], Path],
                parse_fn: Callable[[object], list],
                dump_fn: Callable[[object], dict], force: bool = False) -> list:
    """검증된 원격 항목. 네트워크/파싱 실패 시 캐시→빈 목록 폴백(예외 없음)."""
    try:
        cached = _read_cache(cache_path_fn, parse_fn)
    except Exception as exc:  # 캐시 읽기 실패도 "캐시 없음" — 절대 raise 금지
        logger.warning("catalog_fetch: cache read failed: %s", exc)
        cached = None
    if not force and cached is not None:
        fetched_at, entries = cached
        if _now() - fetched_at < CACHE_TTL_SECONDS:
            return entries
    url = url_fn()
    if not url.startswith("https://"):
        logger.warning("catalog_fetch: non-https url ignored: %s", url)
        return cached[1] if cached else []
    try:
        text = _http_get(url)
        entries = parse_fn(json.loads(text))
        _write_cache(cache_path_fn, entries, dump_fn)
        return entries
    except Exception as exc:
        logger.warning("catalog_fetch: fetch failed (%s); using cache/builtin", exc)
        return cached[1] if cached else []
