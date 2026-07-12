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
from pathlib import Path

from apps.server.ai.apple_native import apple_stt_available

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
        async with self._lock:
            self._next_id += 1
            req_id = self._next_id
            req = {"id": req_id, "en": en,
                   "context": [[a, b] for a, b in context], "glossary": {}}
            assert self._proc is not None and self._proc.stdin and self._proc.stdout
            self._proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode())
            await self._proc.stdin.drain()
            while True:
                line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=timeout)
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
# === ANCHOR: MLX_LIVE_TRANSLATE_END ===
