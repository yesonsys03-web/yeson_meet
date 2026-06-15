"""Smoke test for audio_main: mocks native source + websockets.

Sets env vars BEFORE importing apps.client_sidecar to avoid import-time reads
of SERVER_WS_BASE (constants.py) being wrong.
"""
from __future__ import annotations

import asyncio
import os

# Set env vars BEFORE any apps.client_sidecar import
os.environ.setdefault("SERVER_WS_BASE", "ws://test")
os.environ.setdefault("YESON_DEVICE_API_KEY", "test")
os.environ.setdefault("YESON_SESSION_ID", "00000000-0000-0000-0000-000000000000")

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from apps.client_sidecar.main import audio_main


def _make_fake_ws():
    """Return a fake WebSocket with async send/recv."""
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock(return_value=None)
    ws.close = AsyncMock()
    return ws


def _make_ws_context_manager(fake_ws):
    """Return an async context manager that yields fake_ws."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_ws)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class _FakeNativeSource:
    """Minimal native source stub: yields silent chunks indefinitely (timeout cancels it)."""

    async def chunks(self):
        while True:
            yield b"\x00" * 640
            await asyncio.sleep(0.01)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_audio_main_smoke(monkeypatch) -> None:
    """audio_main() runs for 0.3s, sends at least 1 message (audio.started), no exception."""
    monkeypatch.setenv("YESON_DEVICE_API_KEY", "test")
    monkeypatch.setenv("YESON_SESSION_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("SERVER_WS_BASE", "ws://test")
    monkeypatch.setenv("YESON_SIDECAR_MODE", "audio")

    fake_ws = _make_fake_ws()
    ws_cm = _make_ws_context_manager(fake_ws)

    # Patch make_source to return a fake native source (no helper binary needed)
    monkeypatch.setattr(
        "apps.client_sidecar.audio.sources.factory.make_source",
        lambda: _FakeNativeSource(),
    )

    with patch("websockets.connect", return_value=ws_cm):
        with pytest.raises((asyncio.CancelledError, asyncio.TimeoutError, Exception)):
            await asyncio.wait_for(audio_main(), timeout=0.3)

    # At minimum audio.started was sent
    assert fake_ws.send.call_count >= 1, (
        f"Expected ws.send called ≥ 1 time, got {fake_ws.send.call_count}"
    )
    # First call should be audio.started JSON
    first_call_arg = fake_ws.send.call_args_list[0][0][0]
    assert "audio.started" in first_call_arg, (
        f"First send should contain 'audio.started', got: {first_call_arg!r}"
    )


def test_audio_main_redacts_device_key(monkeypatch, capsys):
    """The audio startup line is forwarded to the desktop app log, so it must
    never contain the Device API Key or the raw ?key= query string."""
    fake_key = "redactable-placeholder-value"
    monkeypatch.setenv("YESON_DEVICE_API_KEY", fake_key)
    monkeypatch.setenv("YESON_SESSION_ID", "00000000-0000-0000-0000-000000000000")

    class _FakeSource:
        async def chunks(self):
            return
            yield b""  # pragma: no cover — makes this an async generator

        async def close(self):
            pass

    async def _fake_stream_audio(url, chunks, reporter=None):
        return None

    # audio_main imports these names inside the function, so patch them at the
    # source module (the local `from … import` then binds the patched object).
    monkeypatch.setattr(
        "apps.client_sidecar.audio.sources.factory.make_source",
        lambda: _FakeSource(),
    )
    monkeypatch.setattr(
        "apps.client_sidecar.transport.audio_ws.stream_audio",
        _fake_stream_audio,
    )

    asyncio.run(audio_main())

    out = capsys.readouterr().out
    assert fake_key not in out
    assert "key=<redacted>" in out
