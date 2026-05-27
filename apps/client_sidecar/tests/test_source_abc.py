"""Tests for AudioSource abstract base class."""
from __future__ import annotations

import pytest

from apps.client_sidecar.audio.source import AudioSource


def test_abstract_cannot_instantiate():
    with pytest.raises(TypeError):
        AudioSource()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_concrete_impl_yields_bytes():
    class Fake(AudioSource):
        async def chunks(self):
            yield b"\x00" * 640
            yield b"\x01" * 640

        async def close(self):
            pass

    src = Fake()
    out = []
    async for c in src.chunks():
        out.append(c)
        if len(out) >= 2:
            break
    await src.close()
    assert len(out) == 2
    assert all(len(c) == 640 for c in out)
