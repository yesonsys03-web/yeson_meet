"""SoundDeviceSource wraps the existing find_input_device + capture_chunks pipeline."""
from __future__ import annotations

import pytest

from apps.client_sidecar.audio.source import AudioSource


@pytest.mark.asyncio
async def test_sounddevice_source_is_audio_source():
    from apps.client_sidecar.audio.sources.sounddevice_source import SoundDeviceSource
    src = SoundDeviceSource()  # constructor with defaults; chunks() not exercised here
    assert isinstance(src, AudioSource)
    await src.close()
