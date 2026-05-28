"""NativePipeSource reads PCM chunks from helper subprocess stdout."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from apps.client_sidecar.audio.source import AudioSource


@pytest.mark.asyncio
async def test_native_pipe_source_yields_chunks_from_stdout(monkeypatch, tmp_path):
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource

    bin_path = tmp_path / "yeson-helper"
    bin_path.write_bytes(b"\x00")
    bin_path.chmod(0o755)

    fake_proc = MagicMock()
    payload = (b"\x00" * 640) + (b"\x11" * 640)
    fake_proc.stdout = asyncio.StreamReader()
    fake_proc.stdout.feed_data(payload)
    fake_proc.stdout.feed_eof()
    fake_proc.stderr = asyncio.StreamReader()
    fake_proc.stderr.feed_eof()
    fake_proc.returncode = 0  # so close() skips terminate

    async def fake_create(*args, **kwargs):
        return fake_proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    src = NativePipeSource(bin_path=str(bin_path))
    assert isinstance(src, AudioSource)
    chunks = []
    async for c in src.chunks():
        chunks.append(c)
        if len(chunks) >= 2:
            break
    await src.close()
    assert len(chunks) == 2
    assert chunks[0] == b"\x00" * 640
    assert chunks[1] == b"\x11" * 640


@pytest.mark.asyncio
async def test_native_pipe_source_raises_if_bin_missing():
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource

    src = NativePipeSource(bin_path="/nonexistent/path/yeson-helper")
    with pytest.raises(FileNotFoundError):
        async for _ in src.chunks():
            break


@pytest.mark.asyncio
async def test_native_pipe_source_raises_on_permission_denied(monkeypatch, tmp_path):
    """Helper fatal/permission_denied surfaces as NativeCaptureError, not silent EOF."""
    from apps.client_sidecar.audio.sources.native_pipe_source import (
        NativeCaptureError,
        NativePipeSource,
    )

    bin_path = tmp_path / "yeson-helper"
    bin_path.write_bytes(b"\x00")
    bin_path.chmod(0o755)

    fake_proc = MagicMock()
    fake_proc.stdout = asyncio.StreamReader()
    fake_proc.stdout.feed_eof()  # no audio: helper died before producing PCM
    fake_proc.stderr = asyncio.StreamReader()
    fake_proc.stderr.feed_data(b'{"event":"permission_required","payload":{"status":"denied"}}\n')
    fake_proc.stderr.feed_data(b'{"event":"fatal","payload":{"reason":"permission_denied"}}\n')
    fake_proc.stderr.feed_eof()
    fake_proc.returncode = 3

    async def fake_create(*args, **kwargs):
        return fake_proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    src = NativePipeSource(bin_path=str(bin_path))
    with pytest.raises(NativeCaptureError) as excinfo:
        async for _ in src.chunks():
            pass
    assert excinfo.value.reason == "permission_denied"
    await src.close()


@pytest.mark.asyncio
async def test_native_pipe_source_drains_stderr_json_events(monkeypatch, tmp_path, caplog):
    """stderr JSON-line events are logged so the operator sees helper lifecycle."""
    import logging
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource

    bin_path = tmp_path / "yeson-helper"
    bin_path.write_bytes(b"\x00")
    bin_path.chmod(0o755)

    fake_proc = MagicMock()
    fake_proc.stdout = asyncio.StreamReader()
    fake_proc.stdout.feed_eof()
    fake_proc.stderr = asyncio.StreamReader()
    fake_proc.stderr.feed_data(b'{"event":"started","payload":{}}\n')
    fake_proc.stderr.feed_eof()
    fake_proc.returncode = 0

    async def fake_create(*args, **kwargs):
        return fake_proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    src = NativePipeSource(bin_path=str(bin_path))
    caplog.set_level(logging.INFO)
    async for _ in src.chunks():
        break
    # Let the stderr drain task run once.
    await asyncio.sleep(0.05)
    await src.close()
    assert any("started" in r.message for r in caplog.records)
