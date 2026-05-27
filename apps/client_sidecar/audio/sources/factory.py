# === ANCHOR: SOURCE_FACTORY_START ===
"""Select AudioSource implementation based on YESON_AUDIO_PROVIDER env.

native — explicit; raises FileNotFoundError if helper binary missing
sounddevice — explicit; uses BlackHole/Voicemeeter compatibility path
auto — try native; on any failure fall back to sounddevice
"""
from __future__ import annotations

import logging
import os

from apps.client_sidecar.audio.source import AudioSource
from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
from apps.client_sidecar.audio.sources.sounddevice_source import SoundDeviceSource
from apps.client_sidecar.config.audio import NATIVE_HELPER_BIN_PATH, YESON_AUDIO_PROVIDER

logger = logging.getLogger(__name__)


def make_source() -> AudioSource:
    provider = os.environ.get("YESON_AUDIO_PROVIDER", YESON_AUDIO_PROVIDER).lower()
    if provider == "sounddevice":
        logger.info("audio provider: sounddevice (explicit)")
        return SoundDeviceSource()
    bin_path = os.environ.get("YESON_NATIVE_HELPER_BIN", NATIVE_HELPER_BIN_PATH)
    if provider == "native":
        if not os.path.isfile(bin_path):
            raise FileNotFoundError(
                f"YESON_AUDIO_PROVIDER=native but helper binary missing: {bin_path}"
            )
        logger.info("audio provider: native (explicit, bin=%s)", bin_path)
        return NativePipeSource(bin_path=bin_path)
    # auto
    if os.path.isfile(bin_path):
        logger.info("audio provider: native (auto, bin=%s)", bin_path)
        return NativePipeSource(bin_path=bin_path)
    logger.warning(
        "audio provider: sounddevice (auto fallback — native helper missing at %s)",
        bin_path,
    )
    return SoundDeviceSource()
# === ANCHOR: SOURCE_FACTORY_END ===
