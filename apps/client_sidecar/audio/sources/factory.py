# === ANCHOR: SOURCE_FACTORY_START ===
"""Select the AudioSource implementation.

Native-only policy (2026-06-15 cutover): the OS-level helper (macOS
ScreenCaptureKit / Windows WASAPI) is the sole capture path. The legacy
sounddevice (BlackHole/Voicemeeter) path and the `auto` transition mode were
removed once native landed on both platforms (see
docs/superpowers/specs/2026-06-15-native-only-cutover-design.md). A missing
helper binary raises FileNotFoundError so packaging gaps surface loudly.
"""
from __future__ import annotations

import logging
import os

from apps.client_sidecar.audio.source import AudioSource
from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
from apps.client_sidecar.config.audio import NATIVE_HELPER_BIN_PATH

logger = logging.getLogger(__name__)


def make_source() -> AudioSource:
    provider = os.environ.get("YESON_AUDIO_PROVIDER")
    if provider and provider.lower() != "native":
        logger.warning(
            "YESON_AUDIO_PROVIDER=%s is removed (native-only); using native", provider
        )
    bin_path = os.environ.get("YESON_NATIVE_HELPER_BIN", NATIVE_HELPER_BIN_PATH)
    if not os.path.isfile(bin_path):
        raise FileNotFoundError(f"native audio helper binary missing: {bin_path}")
    logger.info("audio provider: native (bin=%s)", bin_path)
    return NativePipeSource(bin_path=bin_path)
# === ANCHOR: SOURCE_FACTORY_END ===
