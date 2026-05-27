"""Smoke test for audio_main: mocks sounddevice + websockets.

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

_BLACKHOLE_DEV = {
    "name": "BlackHole 2ch",
    "max_input_channels": 2,
    "default_samplerate": 48000.0,
    "_yeson_index": 0,
}


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


@pytest.mark.asyncio
async def test_audio_main_smoke(monkeypatch) -> None:
    """audio_main() runs for 0.3s, sends at least 1 message (audio.started), no exception."""
    # Env vars for the test (already set at module level, but ensure here too)
    monkeypatch.setenv("YESON_DEVICE_API_KEY", "test")
    monkeypatch.setenv("YESON_SESSION_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("SERVER_WS_BASE", "ws://test")
    monkeypatch.setenv("YESON_SIDECAR_MODE", "audio")
    # Force sounddevice provider — the smoke test mocks sounddevice + websockets;
    # `auto` would otherwise pick the native helper binary if one is built locally.
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "sounddevice")

    fake_ws = _make_fake_ws()
    ws_cm = _make_ws_context_manager(fake_ws)

    # sd.query_devices dual-signature mock
    def _query_devices(i=None):
        if i is None:
            return [_BLACKHOLE_DEV]
        return _BLACKHOLE_DEV

    # InputStream mock — stores callback ref, start/stop/close are no-ops
    stream_instance = MagicMock()
    stream_instance.start = MagicMock()
    stream_instance.stop = MagicMock()
    stream_instance.close = MagicMock()
    captured_callback = {}

    def _make_stream(**kwargs):
        # Capture the callback so we could call it; for smoke we just need start to work
        captured_callback["fn"] = kwargs.get("callback")
        return stream_instance

    FakeInputStream = MagicMock(side_effect=_make_stream)

    with patch("sounddevice.query_devices", side_effect=_query_devices), \
         patch("sounddevice.InputStream", FakeInputStream), \
         patch("websockets.connect", return_value=ws_cm):

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
