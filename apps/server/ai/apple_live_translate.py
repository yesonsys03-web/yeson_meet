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

Pre-ready hang guard: 스폰 직후 첫 기대 이벤트는 `status ready`다. 바이너리가
ready를 내보내기 전에 멈추면(스파이크 노트: EN→KO 언어팩 미설치 시 의심되는
동작 — 미검증) 세션이 무한히 조용히 대기한다. saw_ready 이전에는 stdout 첫
라인을 ready_timeout 안에 못 받으면 프로세스를 죽이고 AppleProviderUnavailable을
던진다(영구 에러 → 5분 백오프 + 운영자 알림, reconnect 스팸 방지). ready_timeout은
첫 실행 STT 에셋 자동 다운로드(~20s)를 감안해 넉넉하게 잡는다(기본 60s).

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
import tempfile
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from apps.server.ai.apple_native import resolve_apple_bin
from apps.server.ai.glossary import apply_ko_corrections
from apps.server.ai.providers import TranslatedUtterance

logger = logging.getLogger("yeson.ai.apple_live_translate")

DEFAULT_EOF_TIMEOUT_SECONDS = 10.0
_EOF_TIMEOUT_ENV = "YESON_APPLE_LIVE_EOF_TIMEOUT"

# 첫 실행 STT 에셋 자동 다운로드가 ~20s 걸리므로(스파이크 노트) 넉넉하게 60s.
DEFAULT_READY_TIMEOUT_SECONDS = 60.0
_READY_TIMEOUT_ENV = "YESON_APPLE_LIVE_READY_TIMEOUT"

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


def _default_ready_timeout() -> float:
    raw = os.environ.get(_READY_TIMEOUT_ENV)
    if not raw:
        return DEFAULT_READY_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_READY_TIMEOUT_SECONDS


class AppleLiveTranslateProvider:
    def __init__(
        self,
        argv: list[str] | None = None,
        # None → env YESON_APPLE_LIVE_EOF_TIMEOUT(기본 10.0)을 인스턴스 생성
        # 시점에 매번 해석한다. 리터럴 기본값으로 되돌리지 말 것 — 테스트/운영
        # 환경별 오버라이드가 이 지연 평가에 의존한다.
        eof_timeout: float | None = None,
        # None → env YESON_APPLE_LIVE_READY_TIMEOUT(기본 60.0). eof_timeout과
        # 동일하게 인스턴스 생성 시점에 지연 해석한다.
        ready_timeout: float | None = None,
    ):
        self._argv = argv  # 테스트 심; None이면 스폰 시점에 해석
        self._segment = 0
        self._eof_timeout = (
            eof_timeout if eof_timeout is not None else _default_eof_timeout()
        )
        self._ready_timeout = (
            ready_timeout if ready_timeout is not None else _default_ready_timeout()
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
        # 회의 길이만큼 이어지는 스트림이라 stderr=PIPE로 두면(파이프 버퍼가
        # 다 차도록 아무도 안 읽는) 데드락 위험이 있다 — transcribe_apple.py의
        # 동일 수정과 같은 방식으로 실제 파일에 흘려보내고 크래시 시에만 tail을
        # 읽는다.
        with tempfile.TemporaryFile() as stderr_file:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr_file)
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
            saw_ready = False
            read_t: asyncio.Future | None = None
            try:
                while True:
                    read_t = asyncio.ensure_future(proc.stdout.readline())
                    if not saw_ready:
                        # 스폰 직후: 첫 status 이벤트(보통 ready)가 오기 전엔 pump가
                        # 아직 오디오를 무한히 흘리는 중이라 pump-race가 무의미하다.
                        # 첫 stdout 라인을 ready_timeout으로 직접 제한 — 못 받으면
                        # 언어팩(EN→KO) 미설치 등으로 바이너리가 조용히 멈춘 것으로
                        # 보고 영구 에러를 던진다.
                        try:
                            line = await asyncio.wait_for(
                                read_t, timeout=self._ready_timeout)
                        except asyncio.TimeoutError:
                            logger.error(
                                "apple live: no output within ready_timeout=%.1fs "
                                "after spawn; killing (EN→KO 언어팩 미설치 의심)",
                                self._ready_timeout,
                            )
                            raise AppleProviderUnavailable(
                                f"ready timeout after {self._ready_timeout}s — "
                                "언어팩(EN→KO) 미설치 가능성, 시스템 설정에서 번역 "
                                "언어 다운로드 확인")
                    else:
                        # ready 이후: pump.done()을 먼저 확인하고 나서 readline()을
                        # 무제한으로 거는 것(TOCTOU)이 아니라, readline()을 항상 먼저
                        # 걸어두고 pump와 경합시킨다 — 그 사이 pump가 끝나버려도 이미
                        # 진행 중인 readline을 eof_timeout으로 다시 감쌀 수 있다.
                        if not pump.done():
                            await asyncio.wait(
                                {read_t, pump}, return_when=asyncio.FIRST_COMPLETED)
                        if read_t.done():
                            line = read_t.result()
                        else:
                            # 펌프 종료됨(오디오 끝) — 이후 줄은 eof_timeout 안에 와야 한다
                            try:
                                line = await asyncio.wait_for(
                                    read_t, timeout=self._eof_timeout)
                            except asyncio.TimeoutError:
                                logger.warning(
                                    "apple live: no output within eof_timeout=%.1fs "
                                    "after stdin EOF; ending stream",
                                    self._eof_timeout,
                                )
                                timed_out = True
                                break
                    if not line:
                        break  # EOF — 프로세스 종료
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("apple live: non-JSON line: %r", line[:120])
                        continue
                    etype = event.get("type")
                    if etype == "status":
                        saw_ready = True  # 첫 status 관측 → ready_timeout 가드 해제
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
                        stderr_file.seek(0)
                        stderr_tail = stderr_file.read().decode(
                            "utf-8", errors="replace")[-300:]
                        raise RuntimeError(f"apple live exited rc={rc}: {stderr_tail}")
            finally:
                if read_t is not None and not read_t.done():
                    read_t.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await read_t
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pump
                if proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    with contextlib.suppress(Exception):
                        await proc.wait()
# === ANCHOR: APPLE_LIVE_TRANSLATE_END ===
