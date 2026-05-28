# === ANCHOR: SOURCE_FACTORY_START ===
"""Select AudioSource implementation based on YESON_AUDIO_PROVIDER env.

Policy (see config/audio.py AUDIO_PROVIDER anchor for the canonical
declaration):

    native       — default; OS-level helper. Missing binary raises
                   FileNotFoundError so packaging/build gaps surface
                   loudly instead of silently degrading to sounddevice.
    sounddevice  — emergency fallback (BlackHole/Voicemeeter compat).
                   Opt-in only via env.
    auto         — transition aid: try native, silently fall back to
                   sounddevice when the helper is missing. Deprecated;
                   exists only until Windows native lands and the
                   sounddevice path is removed.
"""
from __future__ import annotations

import logging
import os

from apps.client_sidecar.audio.source import AudioSource
from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
from apps.client_sidecar.audio.sources.sounddevice_source import SoundDeviceSource
from apps.client_sidecar.config.audio import NATIVE_HELPER_BIN_PATH

logger = logging.getLogger(__name__)

# Default kept as a literal so tests don't depend on config.audio's
# import-time read of $YESON_AUDIO_PROVIDER. Must match the constant in
# config/audio.py (single source of truth for the policy).
_DEFAULT_PROVIDER = "native"


def make_source() -> AudioSource:
    provider = os.environ.get("YESON_AUDIO_PROVIDER", _DEFAULT_PROVIDER).lower()
    if provider == "sounddevice":
        logger.warning(
            "audio provider: sounddevice (emergency fallback — opted in via env)"
        )
        return SoundDeviceSource()
    bin_path = os.environ.get("YESON_NATIVE_HELPER_BIN", NATIVE_HELPER_BIN_PATH)
    if provider == "native":
        if not os.path.isfile(bin_path):
            raise FileNotFoundError(
                f"YESON_AUDIO_PROVIDER=native but helper binary missing: {bin_path}"
            )
        logger.info("audio provider: native (explicit, bin=%s)", bin_path)
        return NativePipeSource(bin_path=bin_path)
    # auto — deprecated transition path
    if os.path.isfile(bin_path):
        logger.info("audio provider: native (auto, bin=%s)", bin_path)
        return NativePipeSource(bin_path=bin_path)
    logger.warning(
        "audio provider: sounddevice (auto fallback — native helper missing at %s; "
        "auto mode is deprecated, prefer YESON_AUDIO_PROVIDER=native or =sounddevice)",
        bin_path,
    )
    return SoundDeviceSource()
# === ANCHOR: SOURCE_FACTORY_END ===
