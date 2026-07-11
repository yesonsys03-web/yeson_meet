# === ANCHOR: APPLE_NATIVE_START ===
"""Apple 온디바이스 바이너리(apple-live-translate) 탐색 + 기능별 가용성.

게이팅은 기능별로 다르다 (스펙 §4.2): 번역(translate-batch)은 macOS 15+,
전사/라이브(SpeechTranscriber)는 macOS 26+. 모두 Apple Silicon 전용.
언어 에셋 유무 같은 깊은 체크는 여기서 하지 않는다 — 바이너리가 기동 시
status:error로 보고하고, 그 메시지가 운영자에게 표출된다.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys

APPLE_BIN_ENV = "YESON_APPLE_TRANSLATE_BIN"
# 자막메이커 whisper_model 필드에 넣는 센티널 — DB 스키마 변경 없이 엔진 선택.
APPLE_TRANSCRIBE_MODEL = "apple"
_BIN_NAME = "apple-live-translate"


def _is_apple_silicon_mac() -> bool:  # test seam
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _macos_major() -> int:  # test seam
    try:
        return int(platform.mac_ver()[0].split(".")[0])
    except (ValueError, IndexError):
        return 0


def resolve_apple_bin() -> str | None:
    """env 우선, 없으면 PATH. env가 없는 파일을 가리키면 무시하고 PATH 폴백."""
    env_path = os.environ.get(APPLE_BIN_ENV, "").strip()
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        return env_path
    return shutil.which(_BIN_NAME)


def apple_mt_available() -> bool:
    return (_is_apple_silicon_mac() and _macos_major() >= 15
            and resolve_apple_bin() is not None)


def apple_stt_available() -> bool:
    return (_is_apple_silicon_mac() and _macos_major() >= 26
            and resolve_apple_bin() is not None)
# === ANCHOR: APPLE_NATIVE_END ===
