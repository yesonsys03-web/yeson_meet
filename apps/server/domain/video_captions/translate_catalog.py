"""로컬 번역 모델(Qwen 티어) 카탈로그 — 단일 진실.

티어 정의(MLX 리포·Ollama 태그·크기·라벨)의 유일한 출처. whisper 전사 모델과 같은
원격 오버레이(catalog_fetch)를 쓰되, 번역 티어는 런타임이 둘(실리콘맥=MLX, 그 외=
윈도·인텔맥=Ollama)이라 정체성이 이중이다 — repo_id 하나로 표현되지 않는다.

양쪽 런타임은 optional이고 최소 한쪽은 있어야 한다. 현재 서버가 지원하지 않는
런타임의 티어는 목록에서 빼지 않고 unsupported_reason()으로 '보이되 비활성'
처리한다(video_models.py의 Apple 항목과 동일 정책 — 플랫폼별로 항목이 사라지는
비대칭을 만들지 않는다).

원격은 추가·오버라이드만 가능하고 빌트인 삭제는 불가하다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from apps.server.ai.apple_native import _is_apple_silicon_mac

from . import catalog_fetch

logger = logging.getLogger("yeson.video.translate_catalog")

STORAGE_ROOT_ENV = "STORAGE_ROOT"
DEFAULT_STORAGE_ROOT = "/var/lib/yeson-meet/storage"

DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/yesonsys03-web/yeson_meet/main/"
    "apps/server/domain/video_captions/translate_catalog.remote.json"
)
CATALOG_URL_ENV = "YESON_TRANSLATE_CATALOG_URL"


@dataclass(frozen=True)
class TranslateModel:
    name: str          # 드롭다운 provider 값 = create_translator 라우팅 키
    label: str
    mlx_repo: str | None
    mlx_bytes: int     # mlx_repo가 없으면 0
    ollama_tag: str | None
    ollama_bytes: int  # ollama_tag가 없으면 0


BUILTIN: dict[str, TranslateModel] = {
    "qwen": TranslateModel(
        "qwen", "Qwen 9B (로컬)",
        "mlx-community/Qwen3.5-9B-4bit", 5_000_000_000,
        "qwen3.5:9b", 6_600_000_000),
    "qwen_lite": TranslateModel(
        "qwen_lite", "Qwen 4B (로컬·빠름)",
        "mlx-community/Qwen3.5-4B-4bit", 2_300_000_000,
        "qwen3.5:4b", 3_400_000_000),
    "qwen_hifi": TranslateModel(
        "qwen_hifi", "Qwen 9B (로컬·고품질 8bit)",
        "mlx-community/Qwen3.5-9B-8bit", 10_000_000_000,
        "qwen3.5:9b-q8_0", 10_000_000_000),
}


def _catalog_url() -> str:
    return os.environ.get(CATALOG_URL_ENV, DEFAULT_CATALOG_URL)


def _cache_path() -> Path:
    # STORAGE_ROOT 직하 — 번역엔 대응하는 단일 모델 디렉터리가 없다(MLX는 mlx_models/,
    # Ollama는 Ollama 자체 저장소). mlx_models/ 밑에 두면 MLX가 없는 윈도우 서버에
    # mlx_models 디렉터리가 생긴다.
    root = os.environ.get(STORAGE_ROOT_ENV, DEFAULT_STORAGE_ROOT)
    return Path(root) / "translate_catalog.cache.json"


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _pos_int(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return value


def _parse(payload: object) -> list[TranslateModel]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("models")
    if not isinstance(raw, list):
        return []
    out: list[TranslateModel] = []
    for item in raw:
        if not isinstance(item, dict):
            logger.warning("translate_catalog: skip non-dict entry: %r", item)
            continue
        name = item.get("name")
        label = item.get("label")
        mlx_repo = _opt_str(item.get("mlx_repo"))
        ollama_tag = _opt_str(item.get("ollama_tag"))
        if not catalog_fetch.valid_name(name) or not isinstance(label, str):
            logger.warning("translate_catalog: skip invalid entry: %r", item)
            continue
        if mlx_repo is None and ollama_tag is None:
            logger.warning(
                "translate_catalog: entry has neither mlx_repo nor ollama_tag: %r", item)
            continue
        mlx_bytes = _pos_int(item.get("mlx_bytes")) if mlx_repo else 0
        ollama_bytes = _pos_int(item.get("ollama_bytes")) if ollama_tag else 0
        if mlx_bytes is None or ollama_bytes is None:
            logger.warning("translate_catalog: skip entry with invalid *_bytes: %r", item)
            continue
        out.append(TranslateModel(
            name=name, label=label,
            mlx_repo=mlx_repo, mlx_bytes=mlx_bytes,
            ollama_tag=ollama_tag, ollama_bytes=ollama_bytes))
    return out


def get_remote_models(force: bool = False) -> list[TranslateModel]:
    """원격 카탈로그의 검증된 티어 목록. 실패 시 캐시→빈 목록 폴백(예외 없음)."""
    return catalog_fetch.get_entries(_catalog_url, _cache_path, _parse, asdict, force)


def get_catalog() -> dict[str, TranslateModel]:
    """빌트인 baseline에 원격(디스크 캐시)을 오버레이한 유효 티어 목록.

    네트워크는 타지 않는다 — 원격 갱신은 /translate-models 엔드포인트가 담당한다.
    """
    merged = dict(BUILTIN)
    for m in catalog_fetch.cached_entries(_cache_path, _parse):
        merged[m.name] = m
    return merged


def runtime() -> str:
    """이 서버에서 로컬 번역이 쓸 런타임 — 실리콘=mlx, 그 외(윈도·인텔맥)=ollama."""
    return "mlx" if _is_apple_silicon_mac() else "ollama"


def unsupported_reason(entry: TranslateModel) -> str | None:
    """이 서버의 런타임을 지원하지 않는 티어면 사유, 아니면 None.

    None이면 '설치만 하면 쓸 수 있음'이고, 값이 있으면 '이 기기에선 다운로드해도
    소용없음'이다 — 클라는 후자에만 다운로드 버튼을 비활성화한다.
    """
    if runtime() == "mlx":
        return None if entry.mlx_repo else "Ollama 전용"
    return None if entry.ollama_tag else "실리콘맥 전용"


def ollama_env_key(name: str) -> str:
    """티어별 Ollama 태그 오버라이드 env 키.

    규칙이 기존 3키(YESON_OLLAMA_QWEN_MODEL / _QWEN_LITE_ / _QWEN_HIFI_)를 그대로
    재현하므로 하위호환이 유지된다.
    """
    return f"YESON_OLLAMA_{name.upper()}_MODEL"
