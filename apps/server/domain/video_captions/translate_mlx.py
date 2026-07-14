# === ANCHOR: TRANSLATE_MLX_START ===
"""로컬 MLX Qwen 배치 번역 (translate.py의 TranslationProvider plug point).

라이브 자막용 MLX 워커(mlx_worker.run_worker)를 잡당 1회 기동·유지하며,
build_translation_prompt(글로서리+의성어+간결 자막 지시)를 raw 프롬프트로 보내
JSON 배열 KO를 받는다. 서브프로세스 격리(크래시·메모리)로 라이브와 동일 아키텍처.
실리콘맥 전용 백엔드 — mlx-lm/모델은 워커 안에서만 로드된다. 그 외 플랫폼(윈도·
인텔맥)의 로컬 Qwen은 translate_ollama가 담당하며, 티어 값(qwen/qwen_lite/
qwen_hifi)은 공유하고 백엔드 선택은 translate_cli.create_translator가 한다.

QWEN_MLX_MODELS는 serverConfig.ts의 MLX_MODELS와 동일하게 유지해야 한다.
"""
from __future__ import annotations

import logging

from apps.server.ai.apple_native import _is_apple_silicon_mac
from apps.server.ai.mlx_live_translate import (
    MlxWorkerClient,
    MlxWorkerUnavailable,
    guard_mlx_ko,
    mlx_model_installed,
)
from .translate import TranslationError, build_translation_prompt
from .translate_cli import _extract_json_array

logger = logging.getLogger("yeson.video.translate_mlx")

# provider 값 → MLX model id. serverConfig.ts MLX_MODELS와 동기화.
QWEN_MLX_MODELS: dict[str, str] = {
    "qwen": "mlx-community/Qwen3.5-9B-4bit",
    "qwen_lite": "mlx-community/Qwen3.5-4B-4bit",
    "qwen_hifi": "mlx-community/Qwen3.5-9B-8bit",
}

DEFAULT_BATCH_TIMEOUT = 300.0


def qwen_mlx_available(model_id: str) -> bool:
    """실리콘맥이고 해당 MLX 모델이 설치되어 있는가.

    MLX 번역은 Apple STT 바이너리·macOS 26과 무관하므로 apple_stt_available()을
    쓰지 않고 실리콘 여부 + 모델 설치만 본다.
    """
    return _is_apple_silicon_mac() and mlx_model_installed(model_id)


class QwenMlxTranslator:
    """TranslationProvider — 로컬 MLX Qwen 배치 번역. 워커는 지연 기동·유지, aclose로 종료."""

    def __init__(self, model_id: str, *, client_factory=None,
                 timeout: float = DEFAULT_BATCH_TIMEOUT):
        self._model_id = model_id
        self._client_factory = client_factory or MlxWorkerClient
        self._timeout = timeout
        self._client = None

    async def _ensure_client(self):
        if self._client is not None and getattr(self._client, "alive", False):
            return self._client
        try:
            client = self._client_factory(model_id=self._model_id)
            await client.start()
        except Exception as exc:  # noqa: BLE001 — 어떤 워커 기동 실패든 원문 유지로 폴백(자막 무중단)
            raise TranslationError(f"MLX 워커 기동 실패: {exc}") from exc
        self._client = client
        return client

    async def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        client = await self._ensure_client()
        prompt = build_translation_prompt(texts)
        try:
            raw = await client.generate(prompt, timeout=self._timeout)
        except (MlxWorkerUnavailable, TimeoutError) as exc:
            raise TranslationError(f"MLX 배치 번역 실패: {exc}") from exc
        out = _extract_json_array(raw, len(texts))
        if out is None:
            raise TranslationError(f"MLX 번역 출력 파싱 실패: {raw[:200]!r}")
        # 환각 가드: 불합격 줄은 원문(EN) 유지(검수 단계에서 눈에 띄게).
        guarded: list[str] = []
        for src, ko in zip(texts, out):
            reason = guard_mlx_ko(src, ko)
            if reason is not None:
                logger.info("mlx_video_guard_reject reason=%s src=%r", reason, src[:60])
                guarded.append(src)
            else:
                guarded.append(ko)
        return guarded

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.close()
# === ANCHOR: TRANSLATE_MLX_END ===
