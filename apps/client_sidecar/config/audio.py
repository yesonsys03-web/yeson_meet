# === ANCHOR: AUDIO_START ===
"""Audio capture configuration (Slice 2 lock)."""
from __future__ import annotations

import os
import sys

TARGET_SAMPLE_RATE: int = 16000
TARGET_CHANNELS: int = 1
CHUNK_MS: int = 20
CHUNK_SAMPLES: int = 320  # 16000 * 0.02
CHUNK_BYTES: int = 640    # 320 samples * 2 bytes (int16)

# RMS dBFS threshold for silence detection.
RMS_DBFS_THRESHOLD: float = float(os.environ.get("YESON_RMS_DBFS_THRESHOLD", "-45"))
RMS_SILENCE_GATE_ENABLED: bool = os.environ.get(
    "YESON_RMS_SILENCE_GATE_ENABLED", "0"
).lower() not in {"0", "false", "no", "off"}

# === ANCHOR: AUDIO_PROVIDER_START ===
# Native-only capture (2026-06-15 cutover): the OS-level helper (macOS
# ScreenCaptureKit / Windows WASAPI) is the sole path. Missing binary →
# FileNotFoundError (see audio/sources/factory.py). The sounddevice path and
# the YESON_AUDIO_PROVIDER / device-name knobs were removed.
# Where to find the native helper binary (release: bundled by Tauri; dev: target/).
# Dev default is platform-correct so a dev who doesn't set YESON_NATIVE_HELPER_BIN
# still gets a sensible path. Release path is Tauri-injected (unaffected).
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if sys.platform == "win32":
    _NATIVE_HELPER_DEFAULT = os.path.join(
        _REPO_ROOT,
        "apps",
        "native_helper_win",
        "target",
        "release",
        "yeson-win-audio-helper.exe",
    )
else:
    _NATIVE_HELPER_DEFAULT = os.path.join(
        _REPO_ROOT, "target", "native-helper-mac", "yeson-mac-audio-helper"
    )
NATIVE_HELPER_BIN_PATH: str = os.environ.get(
    "YESON_NATIVE_HELPER_BIN", _NATIVE_HELPER_DEFAULT
)
# === ANCHOR: AUDIO_PROVIDER_END ===
# === ANCHOR: AUDIO_END ===
