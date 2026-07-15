# === ANCHOR: TRANSLATE_OLLAMA_START ===
"""로컬 Ollama Qwen 배치 번역 (translate.py의 TranslationProvider plug point).

MLX(실리콘 전용)와 달리 Ollama는 전 플랫폼(윈도·인텔맥·리눅스)에서 로컬 Qwen을
돌린다 — GPU 선택(Win=CUDA, Mac=Metal, CPU 폴백)은 Ollama가 자동 처리. 운영자가
Ollama를 설치하고 모델을 pull해 두면(claude/codex CLI provider와 동일한 "운영자가
런타임 설치" 패턴), :11434 HTTP API로 호출한다.

build_translation_prompt(글로서리+의성어+간결 자막 지시)를 공유하고, JSON 배열 KO를
받아 guard_mlx_ko 환각 가드를 통과시킨다(불합격 줄은 원문 EN 유지 — 검수 단계에서
눈에 띄게). 티어 값(qwen/qwen_lite/qwen_hifi)은 translate_catalog.get_catalog()가
단일 출처이며, 백엔드(MLX vs Ollama) 선택은 translate_cli.create_translator가 담당한다.

기본 태그는 Qwen3.5(4B/9B) — RTX 2080(8GB)급에도 올라간다. 더 큰 카드로 업그레이드
후 Qwen3.6 등으로 바꾸려면 각 티어의 env(YESON_OLLAMA_{티어}_MODEL)로 태그만 교체하거나
원격 카탈로그로 티어를 추가.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from .translate import TranslationError, apply_ko_guard, build_translation_prompt
from .translate_cli import _extract_json_array

logger = logging.getLogger("yeson.video.translate_ollama")

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
_URL_ENV = "YESON_OLLAMA_URL"

DEFAULT_BATCH_TIMEOUT = 300.0
_TAGS_TIMEOUT = 0.5  # 검증 경로 — 로컬 :11434는 즉답 또는 즉거부
_AVAIL_TTL = 5.0     # list_translate_engines가 검증마다 호출 → 짧은 캐시로 폭주 방지

# /api/tags 결과 캐시 (monotonic 타임스탬프 + 서버 응답 여부 + pull된 모델명 집합)
_avail_cache: dict = {"at": -_AVAIL_TTL, "up": False, "models": frozenset()}


def ollama_base_url() -> str:
    return (os.environ.get(_URL_ENV) or "").strip() or DEFAULT_OLLAMA_URL


def qwen_ollama_model(provider: str) -> str | None:
    """provider 값 → 실제 Ollama 태그(env 오버라이드 적용). 미지원 값은 None."""
    from .translate_catalog import get_catalog, ollama_env_key

    entry = get_catalog().get(provider)
    if entry is None or not entry.ollama_tag:
        return None
    return (os.environ.get(ollama_env_key(provider)) or "").strip() or entry.ollama_tag


def _get_tags() -> tuple[bool, frozenset[str]]:
    """(서버 응답 여부, pull된 모델명 집합)을 /api/tags 1회 호출로 얻어 5s 캐시한다.

    ollama_running·ollama_installed·qwen_ollama_available가 공유해 폴링당 중복 HTTP를
    없앤다. httpx는 함수 내 지연 import — 미설치 환경(가벼운 사이드카/테스트)에서 모듈
    import가 깨지지 않도록(gpu_pack의 requests 지연 import 관례와 동일).
    """
    now = time.monotonic()
    if now - _avail_cache["at"] < _AVAIL_TTL:
        return _avail_cache["up"], _avail_cache["models"]
    up = False
    models: frozenset[str] = frozenset()
    try:
        import httpx

        resp = httpx.get(f"{ollama_base_url()}/api/tags", timeout=_TAGS_TIMEOUT)
        up = resp.status_code == 200  # 이전 ollama_running과 정확히 동일(200에 한정)
        resp.raise_for_status()
        models = frozenset(
            m["name"] for m in resp.json().get("models", []) if m.get("name")
        )
    except Exception as exc:  # noqa: BLE001 — 서버 down/httpx 미설치/타임아웃 = 미가용
        logger.debug("ollama /api/tags unavailable: %s", exc)
    _avail_cache.update(at=now, up=up, models=models)
    return up, models


def qwen_ollama_available(model_id: str | None) -> bool:
    """Ollama 서버가 살아 있고 해당 태그가 pull되어 있는가."""
    return bool(model_id) and model_id in _get_tags()[1]


def ollama_running() -> bool:
    """Ollama 서버가 :11434에서 응답하는가 (모델 유무와 무관)."""
    return _get_tags()[0]


def ollama_installed() -> bool:
    """Ollama가 설치되어 있는가 — PATH의 CLI 또는 실행 중 서버로 판정."""
    import shutil

    return shutil.which("ollama") is not None or ollama_running()


def pull_model(tag: str, on_progress=None) -> None:
    """`ollama pull` 동등 — /api/pull 스트리밍. on_progress(pct:int)로 진행률 콜백.

    블로킹 — 호출부(다운로드 스레드)에서 돌린다. httpx 지연 import.
    """
    import json

    import httpx

    url = f"{ollama_base_url()}/api/pull"
    try:
        with httpx.stream(
            "POST", url, json={"model": tag, "stream": True}, timeout=None
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total = d.get("total")
                completed = d.get("completed")
                if on_progress is not None and total:
                    pct = min(99, int((completed or 0) * 100 / total))
                    on_progress(pct)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Ollama pull 실패({tag}): {exc}") from exc


def delete_model(tag: str) -> None:
    """`ollama rm` 동등 — /api/delete."""
    import httpx

    url = f"{ollama_base_url()}/api/delete"
    try:
        resp = httpx.request("DELETE", url, json={"model": tag}, timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Ollama 삭제 실패({tag}): {exc}") from exc


class OllamaTranslator:
    """TranslationProvider — 로컬 Ollama Qwen 배치 번역 (:11434 /api/generate)."""

    def __init__(self, model_id: str, *, timeout: float = DEFAULT_BATCH_TIMEOUT):
        self._model_id = model_id
        self._timeout = timeout

    def _generate(self, prompt: str) -> str:  # 블로킹 — 테스트 seam
        import httpx

        url = f"{ollama_base_url()}/api/generate"
        payload = {
            "model": self._model_id,
            "prompt": prompt,
            "stream": False,
            # Qwen3.5/3.6은 thinking 모델 — 끄지 않으면 추론이 thinking 필드로 가고
            # response가 빈 문자열로 온다(2026-07-14 실측). 배치 번역엔 추론 불필요.
            "think": False,
            # format:"json"은 쓰지 않는다 — Qwen이 배열 대신 원문 키 객체
            # ({"hello": "..."})로 응답해 JSON 배열 계약을 깬다(실측). 프롬프트가
            # 이미 "JSON 배열만" 지시하고 _extract_json_array가 펜스/프로즈를 회수한다.
            "options": {"temperature": 0},
        }
        try:
            resp = httpx.post(url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TranslationError(f"Ollama 요청 실패: {exc}") from exc
        return str(resp.json().get("response", ""))

    async def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        prompt = build_translation_prompt(texts)
        raw = await asyncio.to_thread(self._generate, prompt)
        out = _extract_json_array(raw, len(texts))
        if out is None:
            raise TranslationError(f"Ollama 번역 출력 파싱 실패: {raw[:200]!r}")
        return apply_ko_guard(texts, out, marker="ollama_video_guard_reject")
# === ANCHOR: TRANSLATE_OLLAMA_END ===
