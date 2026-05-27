# === ANCHOR: SOUNDDEVICE_SOURCE_START ===
"""AudioSource implementation wrapping the existing sounddevice pipeline."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from apps.client_sidecar.audio.capture import capture_chunks
from apps.client_sidecar.audio.device import find_input_device
from apps.client_sidecar.audio.source import AudioSource
from apps.client_sidecar.config.audio import DEVICE_INDEX, DEVICE_NAME_REGEX

logger = logging.getLogger(__name__)


class SoundDeviceSource(AudioSource):
    """BlackHole(macOS) / Voicemeeter(Windows) compatible source.

    Wraps the legacy capture path. Behavior unchanged from pre-Phase-1.
    """

    def __init__(self, name_regex: str | None = None, index: int | None = None):
        self._name_regex = name_regex if name_regex is not None else DEVICE_NAME_REGEX
        self._index = index if index is not None else DEVICE_INDEX

    async def chunks(self) -> AsyncIterator[bytes]:
        device = find_input_device(self._name_regex, self._index)
        async for chunk in capture_chunks(device):
            yield chunk

    async def close(self) -> None:
        # capture_chunks owns its own stream lifecycle (finally block).
        return None
# === ANCHOR: SOUNDDEVICE_SOURCE_END ===
