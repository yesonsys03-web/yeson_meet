# === ANCHOR: APPLE_LIVE_TRANSLATE_START ===
"""Apple 온디바이스 라이브 자막 프로바이더 (STTProvider 구현).

apple-live-translate live 서브커맨드를 subprocess로 띄워 stdin으로 16kHz mono
PCM을 펌핑하고 stdout JSONL(partial/final/status)을 TranslatedUtterance로
변환한다. 세션당 subprocess 1개: 크래시 시 예외가 live_session의 reconnect
루프로 전파되고, provider_segment가 stream() 호출마다 증가해
AISequenceNormalizer가 seq를 재정렬한다.

status:error 중 OS 미지원/에셋 없음 계열(unsupported_os, missing_stt_asset,
missing_mt_asset, no_compatible_audio_format)은 재시도해도 소용없는 영구
에러 — 메시지에 "provider unavailable"을 넣어 is_permanent_provider_error가
매칭되게 한다 (5분 백오프 + 운영자 알림 경로). 그 외 reason(예:
"live_failed: <err>")은 일시적 오류로 취급해 plain RuntimeError를 던지고
live_session의 짧은 백오프 reconnect 루프에 맡긴다.

Post-EOF hang guard: 오디오 pump가 끝나(stdin EOF) 바이너리가 finalize를
못 하고 멈추면 결과 스트림이 영원히 끝나지 않을 수 있다. pump task가
끝난 뒤부터는 다음 stdout 라인을 eof_timeout 안에 못 받으면 프로세스를
죽이고 정상 종료로 취급한다(예외를 던지지 않음) — 이미 yield된
utterance는 그대로 유효하다.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from apps.server.ai.apple_native import resolve_apple_bin
from apps.server.ai.glossary import apply_ko_corrections
from apps.server.ai.providers import TranslatedUtterance

logger = logging.getLogger("yeson.ai.apple_live_translate")

DEFAULT_EOF_TIMEOUT_SECONDS = 10.0
_EOF_TIMEOUT_ENV = "YESON_APPLE_LIVE_EOF_TIMEOUT"

# status:error reason 접두사 중 재시도해도 회복 불가능한 것들 (OS/에셋 미지원).
# 그 외 reason(예: "live_failed: ...")은 일시적 오류로 취급한다.
_PERMANENT_REASON_PREFIXES = (
    "unsupported_os",
    "missing_stt_asset",
    "missing_mt_asset",
    "no_compatible_audio_format",
)


class AppleProviderUnavailable(RuntimeError):
    """영구 에러 — live_session의 signature 매칭용 문구를 메시지에 포함."""

    def __init__(self, reason: str):
        super().__init__(f"apple provider unavailable: {reason}")


def _default_eof_timeout() -> float:
    raw = os.environ.get(_EOF_TIMEOUT_ENV)
    if not raw:
        return DEFAULT_EOF_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_EOF_TIMEOUT_SECONDS


class AppleLiveTranslateProvider:
    def __init__(
        self,
        argv: list[str] | None = None,
        eof_timeout: float | None = None,
    ):
        self._argv = argv  # 테스트 심; None이면 스폰 시점에 해석
        self._segment = 0
        self._eof_timeout = (
            eof_timeout if eof_timeout is not None else _default_eof_timeout()
        )

    def _resolved_argv(self) -> list[str]:
        if self._argv is not None:
            return list(self._argv)
        bin_path = resolve_apple_bin()
        if bin_path is None:
            raise AppleProviderUnavailable("binary not found")
        return [bin_path, "live"]

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        lang_hint: str,
    ) -> AsyncIterator[TranslatedUtterance]:
        self._segment += 1
        segment = self._segment
        argv = self._resolved_argv()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        assert proc.stdin is not None and proc.stdout is not None

        async def _pump_audio() -> None:
            try:
                async for chunk in audio:
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass  # 프로세스 사망은 stdout EOF 쪽에서 처리
            finally:
                with contextlib.suppress(Exception):
                    proc.stdin.close()

        pump = asyncio.create_task(_pump_audio())
        timed_out = False
        try:
            while True:
                if pump.done():
                    # 오디오 pump가 끝난(=stdin EOF) 뒤에도 바이너리가 결과를
                    # 계속 보내지 않으면 finalize가 멈춘 것으로 보고 정리한다.
                    try:
                        line = await asyncio.wait_for(
                            proc.stdout.readline(), timeout=self._eof_timeout)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "apple live: no output within eof_timeout=%.1fs "
                            "after stdin EOF; ending stream",
                            self._eof_timeout,
                        )
                        timed_out = True
                        break
                else:
                    line = await proc.stdout.readline()
                if not line:
                    break  # EOF — 프로세스 종료
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("apple live: non-JSON line: %r", line[:120])
                    continue
                etype = event.get("type")
                if etype == "status":
                    if event.get("state") == "error":
                        reason = str(event.get("reason", "unknown"))
                        if reason.startswith(_PERMANENT_REASON_PREFIXES):
                            raise AppleProviderUnavailable(reason)
                        raise RuntimeError(f"apple live status error: {reason}")
                    continue
                if etype not in ("partial", "final"):
                    continue
                now = datetime.now(timezone.utc)
                yield TranslatedUtterance(
                    seq=int(event["seq"]),
                    text_en=str(event.get("en", "")),
                    text_ko=apply_ko_corrections(str(event.get("ko", "")).strip()),
                    started_at=now,
                    ended_at=now,
                    is_final=(etype == "final"),
                    provider_segment=segment,
                )
            if not timed_out:
                rc = await proc.wait()
                if rc != 0:
                    stderr_tail = (await proc.stderr.read()).decode(
                        "utf-8", errors="replace")[-300:]
                    raise RuntimeError(f"apple live exited rc={rc}: {stderr_tail}")
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
# === ANCHOR: APPLE_LIVE_TRANSLATE_END ===
