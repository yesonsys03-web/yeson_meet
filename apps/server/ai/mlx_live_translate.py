# === ANCHOR: MLX_LIVE_TRANSLATE_START ===
"""하이브리드 B: 파이널 번역만 MLX 로컬 LLM으로 정제하는 데코레이터 프로바이더.

스펙: docs/superpowers/specs/2026-07-12-hybrid-b-mlx-live-translate-design.md
- 파셜은 inner(Apple) 그대로 통과, 파이널은 홀드 후 MLX KO로 확정(가드 통과 시).
- 가드 불합격/타임아웃/워커 사망/백로그 초과 → Apple KO 폴백. 자막 무중단이 최우선.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path

from apps.server.ai.apple_native import apple_stt_available
from apps.server.ai.glossary import apply_ko_corrections
from apps.server.ai.providers import STTProvider, TranslatedUtterance

logger = logging.getLogger("yeson.ai.mlx_live_translate")

# --- 환각 가드 --------------------------------------------------------------
# 2026-07-12 벤치 실측 실패 유형을 각각 겨냥한 5규칙. 전부 정규식/문자열 연산.
_FOREIGN_RE = re.compile(
    "[一-鿿"      # CJK 한자
    "぀-ヿ"        # 히라가나+가타카나
    "Ѐ-ӿ"        # 키릴
    "฀-๿"        # 태국 문자
    "�]"              # 깨진 문자
)
_DIGIT_RUN_RE = re.compile(r"\d+")
_ASCII_ALPHA_RE = re.compile(r"[A-Za-z]")
# 10자 이상 구절이 (원본 포함) 3회 이상 등장 = 같은 구절이 2회 더 반복
_REPEAT_RE = re.compile(r"(.{10,}?)(?:.*?\1){2,}", re.DOTALL)

_LEN_RATIO_MIN = 0.2
_LEN_RATIO_MAX = 3.0
_ASCII_LEAK_MAX = 0.6


def guard_mlx_ko(en: str, ko: str) -> str | None:
    """MLX 번역 결과 검증. 통과 시 None, 불합격 시 사유 문자열."""
    ko_stripped = ko.strip()
    if not ko_stripped:
        return "empty"
    if _FOREIGN_RE.search(ko_stripped):
        return "foreign_script"
    en_digits = set(_DIGIT_RUN_RE.findall(en))
    for run in _DIGIT_RUN_RE.findall(ko_stripped):
        if run not in en_digits:
            return "invented_number"
    ratio = len(ko_stripped) / max(1, len(en.strip()))
    if not (_LEN_RATIO_MIN <= ratio <= _LEN_RATIO_MAX):
        return "length_ratio"
    ascii_alpha = len(_ASCII_ALPHA_RE.findall(ko_stripped))
    if ascii_alpha / max(1, len(ko_stripped)) > _ASCII_LEAK_MAX:
        return "english_leak"
    if _REPEAT_RE.search(ko_stripped):
        return "repetition"
    return None


# --- 모델 해석/게이팅 ---------------------------------------------------------
DEFAULT_MLX_MODEL = "mlx-community/Qwen3.5-9B-4bit"
_MODEL_ENV = "YESON_MLX_MODEL"
_STORAGE_ROOT_ENV = "STORAGE_ROOT"
_DEFAULT_STORAGE_ROOT = "/var/lib/yeson-meet/storage"  # glossary.py와 동일 관례


def mlx_model_id() -> str:
    return os.environ.get(_MODEL_ENV, "").strip() or DEFAULT_MLX_MODEL


def mlx_model_dir(model_id: str) -> Path:
    root = os.environ.get(_STORAGE_ROOT_ENV) or _DEFAULT_STORAGE_ROOT
    return Path(root) / "mlx_models" / model_id.replace("/", "--")


def mlx_model_installed(model_id: str) -> bool:
    return (mlx_model_dir(model_id) / "config.json").is_file()


def mlx_live_available() -> bool:
    """apple 전사 게이팅(macOS 26+/arm/바이너리) + 선택 모델 설치 여부."""
    return apple_stt_available() and mlx_model_installed(mlx_model_id())


# --- 워커 클라이언트 ---------------------------------------------------------
DEFAULT_READY_TIMEOUT_SECONDS = 120.0  # 5GB 모델 콜드 페이지-인 감안 (스펙)


class MlxWorkerUnavailable(RuntimeError):
    """워커 기동 실패/사망 — 데코레이터는 Apple KO 폴백으로만 대응한다."""


def _worker_argv() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]  # PyInstaller 번들: 자기 자신 재실행
    return [sys.executable, "-m", "apps.server_desktop.sidecar.server_entry"]


class MlxWorkerClient:
    def __init__(self, argv: list[str] | None = None,
                 ready_timeout: float = DEFAULT_READY_TIMEOUT_SECONDS) -> None:
        self._argv = argv
        self._ready_timeout = ready_timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._lock = asyncio.Lock()  # 요청은 순차 — 워커도 순차 처리

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        argv = self._argv if self._argv is not None else _worker_argv()
        env = dict(os.environ)
        env.update({
            "YESON_MLX_WORKER": "1",
            "YESON_MLX_MODEL_PATH": str(mlx_model_dir(mlx_model_id())),
            "HF_HUB_OFFLINE": "1",  # 회의 중 네트워크 0 (스펙)
        })
        self._proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=env)
        try:
            line = await asyncio.wait_for(
                self._proc.stdout.readline(), timeout=self._ready_timeout)
            event = json.loads(line) if line else {}
        except (asyncio.TimeoutError, json.JSONDecodeError) as exc:
            await self.close()
            raise MlxWorkerUnavailable(f"worker not ready: {exc!r}") from exc
        if event.get("type") != "status" or event.get("state") != "ready":
            reason = event.get("reason", "no ready event")
            await self.close()
            raise MlxWorkerUnavailable(f"worker start failed: {reason}")

    async def translate(self, en: str, context: list[tuple[str, str]],
                        timeout: float) -> str:
        if not self.alive:
            raise MlxWorkerUnavailable("worker not running")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout  # 총 예산 — 라인 단위로 재무장하지 않는다
        async with self._lock:
            proc = self._proc
            if (proc is None or proc.returncode is not None
                    or proc.stdin is None or proc.stdout is None):
                raise MlxWorkerUnavailable("worker not running")
            self._next_id += 1
            req_id = self._next_id
            req = {"id": req_id, "en": en,
                   "context": [[a, b] for a, b in context], "glossary": {}}
            try:
                proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode())
                await proc.stdin.drain()
            except (ConnectionError, BrokenPipeError, RuntimeError) as exc:
                raise MlxWorkerUnavailable(f"worker pipe closed: {exc}") from exc
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                if not line:  # EOF = 워커 사망
                    raise MlxWorkerUnavailable("worker died mid-request")
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if resp.get("id") == req_id:
                    return str(resp.get("ko", ""))

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if proc.stdin:
                proc.stdin.close()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()


# --- 데코레이터 프로바이더 ----------------------------------------------------
DEFAULT_SENTENCE_TIMEOUT_SECONDS = 6.0
DEFAULT_MAX_PENDING = 3
DEFAULT_MAX_RESPAWNS = 2
_CONTEXT_WINDOW = 3
_SENTINEL = object()  # inner 스트림 종료 표식


class MlxRefinedAppleProvider:
    """Apple 라이브 스트림의 파이널 KO만 MLX로 정제하는 데코레이터 (스펙 §데이터 흐름)."""

    def __init__(
        self,
        inner: STTProvider | None = None,
        client_factory: Callable[[], MlxWorkerClient] | None = None,
        sentence_timeout: float = DEFAULT_SENTENCE_TIMEOUT_SECONDS,
        max_pending: int = DEFAULT_MAX_PENDING,
        max_respawns: int = DEFAULT_MAX_RESPAWNS,
    ) -> None:
        if inner is None:
            from apps.server.ai.apple_live_translate import AppleLiveTranslateProvider

            inner = AppleLiveTranslateProvider()
        self._inner = inner
        self._client_factory = client_factory or MlxWorkerClient
        self._sentence_timeout = sentence_timeout
        self._max_pending = max_pending
        self._max_respawns = max_respawns

    async def stream(
        self, audio: AsyncIterator[bytes], lang_hint: str
    ) -> AsyncIterator[TranslatedUtterance]:
        out_q: asyncio.Queue = asyncio.Queue()
        holds: deque[TranslatedUtterance] = deque()
        context: deque[tuple[str, str]] = deque(maxlen=_CONTEXT_WINDOW)
        client: MlxWorkerClient | None = None
        client_ready = False
        respawns = 0
        inner_error: BaseException | None = None
        spawn_lock = asyncio.Lock()  # 워밍업/첫 _refine의 동시 스폰 경합 방지

        async def _ensure_client() -> MlxWorkerClient | None:
            """기동 시도. 실패는 None — 파이널은 Apple KO로 흐른다."""
            nonlocal client, client_ready, respawns
            if client_ready and client is not None and client.alive:
                return client
            async with spawn_lock:
                # 이중 확인 — 락 대기 중 다른 호출자가 이미 스폰을 끝냈을 수 있다
                if client_ready and client is not None and client.alive:
                    return client
                if respawns > self._max_respawns:
                    return None
                fresh = self._client_factory()
                try:
                    await fresh.start()
                except asyncio.CancelledError:
                    # 시작 도중 취소 — 이미 뜬 서브프로세스가 nonlocal에 연결되기
                    # 전이므로 여기서 직접 정리하지 않으면 영구 누수된다.
                    await fresh.close()
                    raise
                except MlxWorkerUnavailable as exc:
                    # start()는 실패 시 내부적으로 이미 close()를 호출하지만,
                    # close()는 멱등이므로 방어적으로 한 번 더 호출해도 무해하다.
                    await fresh.close()
                    respawns += 1
                    logger.warning("mlx worker unavailable (attempt %d): %s", respawns, exc)
                    return None
                if client is not None:
                    await client.close()  # 교체되는 구 클라이언트 누수 방지
                client, client_ready = fresh, True
                return client

        async def _refine(utterance: TranslatedUtterance) -> TranslatedUtterance:
            """파이널 1건 정제. 어떤 실패든 Apple KO 그대로 반환 (예외 금지)."""
            nonlocal client_ready, respawns
            active = await _ensure_client()
            if active is None:
                return utterance
            try:
                # 외부 wait_for는 방어적 백스톱 — 실제 MlxWorkerClient는 timeout
                # 파라미터로 스스로 예산을 지키지만, 만약을 대비해 호출부에서도
                # 동일 예산을 강제해 자막 무중단을 보장한다.
                mlx_ko = await asyncio.wait_for(
                    active.translate(
                        utterance.text_en, list(context), timeout=self._sentence_timeout),
                    timeout=self._sentence_timeout + 0.5)
            except asyncio.TimeoutError:
                logger.warning("mlx sentence timeout seq=%d", utterance.seq)
                return utterance
            except MlxWorkerUnavailable as exc:
                client_ready = False
                respawns += 1
                logger.warning("mlx worker died (respawn %d/%d): %s",
                               respawns, self._max_respawns, exc)
                return utterance
            reason = guard_mlx_ko(utterance.text_en, mlx_ko)
            if reason is not None:
                logger.info("mlx_guard_reject reason=%s seq=%d", reason, utterance.seq)
                return utterance
            return replace(utterance, text_ko=apply_ko_corrections(mlx_ko))

        async def _pump_inner() -> None:
            """inner 스트림 소비: 파셜 즉시 발행, 파이널은 홀드 큐 → 순차 정제."""
            nonlocal inner_error
            try:
                async for utterance in self._inner.stream(audio, lang_hint):
                    if not utterance.is_final:
                        await out_q.put(utterance)
                        continue
                    holds.append(utterance)
                    # 백로그: 홀드가 상한 초과면 최고참부터 MLX 생략(Apple KO 즉시)
                    while len(holds) > self._max_pending:
                        stale = holds.popleft()
                        logger.info("mlx_backlog_skip seq=%d", stale.seq)
                        context.append((stale.text_en, stale.text_ko))
                        await out_q.put(stale)
                    # 순차 정제 (워커도 순차 — 홀드 최고참부터)
                    while holds:
                        pending = holds.popleft()
                        refined = await _refine(pending)
                        context.append((refined.text_en, refined.text_ko))
                        await out_q.put(refined)
            except asyncio.CancelledError:
                # 취소는 삼키지 않는다 — finally에서 플러시한 뒤 그대로 전파.
                raise
            except BaseException as exc:  # noqa: BLE001 — 잔여 홀드 플러시 후 재전파
                inner_error = exc
            finally:
                # 참고: 현재 인라인 펌프 구조상 "while holds:" 정제 루프가
                # 매 utterance마다 holds를 완전히 비운 뒤에야 inner의 다음
                # __anext__를 호출하므로, inner_error 발생 시점엔 holds가
                # 항상 비어 있다 (백로그 스킵도 동일한 while holds 안에서 처리됨).
                # 즉 아래 플러시는 현재 코드로는 도달 불가능하지만, refine을
                # 루프 밖으로 빼는 미래 리팩터링에서 홀드가 비어있지 않은 채
                # 종료될 수 있으므로 안전망으로 유지한다.
                while holds:  # inner 종료/예외: 잔여 홀드는 Apple KO로 플러시
                    await out_q.put(holds.popleft())
                await out_q.put(_SENTINEL)

        pump = asyncio.create_task(_pump_inner())
        # 워커는 백그라운드 선기동 — 로드가 끝나기 전 파이널은 _ensure_client의
        # respawn 카운트를 소모하지 않도록 start를 미리 시도해 둔다.
        warmup = asyncio.create_task(_ensure_client())
        try:
            while True:
                item = await out_q.get()
                if item is _SENTINEL:
                    break
                yield item
            if inner_error is not None:
                raise inner_error
        finally:
            warmup.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await warmup
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
            if client is not None:
                await client.close()
# === ANCHOR: MLX_LIVE_TRANSLATE_END ===
