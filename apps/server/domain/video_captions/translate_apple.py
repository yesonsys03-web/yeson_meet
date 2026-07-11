# === ANCHOR: TRANSLATE_APPLE_START ===
"""Apple 온디바이스 배치 번역 (translate.py의 TranslationProvider plug point).

apple-live-translate translate-batch 서브커맨드에 JSON 배열을 stdin으로 주고
같은 길이의 KO 배열을 받는다. 로컬 NMT라 네트워크 왕복이 없어 배치가 초 단위로
끝난다. 프롬프트 주입이 불가하므로 용어 교정은 translate_segments의
apply_ko_corrections 후보정에 전적으로 의존한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from apps.server.ai.apple_native import resolve_apple_bin
from .translate import TranslationError

logger = logging.getLogger("yeson.video.translate_apple")

DEFAULT_TIMEOUT = 120.0
_STRATEGY_ENV = "YESON_APPLE_MT_STRATEGY"
_VALID_STRATEGIES = {"low", "high"}


class AppleTranslator:
    """TranslationProvider backed by the on-device Apple Translation framework."""

    def __init__(self, argv: list[str] | None = None, timeout: float = DEFAULT_TIMEOUT,
                strategy: str = "low"):
        # argv는 테스트 심 — 운영에서는 resolve_apple_bin()으로 지연 해석
        self._argv = argv
        self._timeout = timeout
        self._strategy = strategy if strategy in _VALID_STRATEGIES else "low"

    def _resolved_argv(self) -> list[str]:
        if self._argv is not None:
            return list(self._argv)
        bin_path = resolve_apple_bin()
        if bin_path is None:
            raise TranslationError(
                "apple-live-translate 바이너리를 찾을 수 없습니다 "
                "(YESON_APPLE_TRANSLATE_BIN 또는 PATH 확인 — 실리콘맥 전용)")
        return [bin_path, "translate-batch"]

    async def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        argv = self._resolved_argv()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, _STRATEGY_ENV: self._strategy})
        payload = json.dumps(texts, ensure_ascii=False).encode("utf-8")
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise TranslationError(f"Apple 번역 시간 초과({self._timeout}s)") from exc
        if proc.returncode != 0:
            tail = stderr.decode("utf-8", errors="replace")[-300:]
            logger.warning("Apple 번역 실패 (returncode=%s): %s", proc.returncode, tail)
            raise TranslationError(
                f"Apple 번역 실패 (returncode={proc.returncode}): {tail}")
        try:
            out = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise TranslationError(
                f"Apple 번역이 JSON이 아닌 출력 반환: {stdout[:200]!r}") from exc
        if not isinstance(out, list) or len(out) != len(texts):
            raise TranslationError(
                f"translation count mismatch: sent {len(texts)}, got "
                f"{len(out) if isinstance(out, list) else type(out).__name__}")
        return [str(t) for t in out]
# === ANCHOR: TRANSLATE_APPLE_END ===
