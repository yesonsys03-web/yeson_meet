"""Job Object orphan-prevention binding (Phase 2b).

The real KILL_ON_JOB_CLOSE behavior only exists on Windows, so these tests cover
the cross-platform contract: the no-op path off Windows, JobHandle close
idempotency, and that NativePipeSource binds on spawn + releases on close.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock

import pytest

from apps.client_sidecar.audio.sources.win_job_object import (
    JobHandle,
    bind_process_to_job,
)


@pytest.mark.skipif(sys.platform == "win32", reason="exercises the non-Windows no-op path")
def test_bind_returns_none_off_windows():
    # No job object, no handle to leak — Unix relies on the Tauri process-group fix.
    assert bind_process_to_job(12345) is None


def test_job_handle_close_is_idempotent_and_closes_once():
    closed = []
    handle = JobHandle(handle=0xABCD, close_handle=closed.append)
    handle.close()
    handle.close()  # second close must be a no-op (no double CloseHandle)
    assert closed == [0xABCD]


@pytest.mark.asyncio
async def test_native_pipe_source_binds_on_spawn_and_releases_on_close(monkeypatch, tmp_path):
    """The helper process is bound to a job on spawn and the handle is released
    (KILL_ON_JOB_CLOSE backstop) when the source closes."""
    import apps.client_sidecar.audio.sources.native_pipe_source as nps

    bin_path = tmp_path / "yeson-helper"
    bin_path.write_bytes(b"\x00")
    bin_path.chmod(0o755)

    fake_proc = MagicMock()
    fake_proc.pid = 4242
    fake_proc.stdout = asyncio.StreamReader()
    fake_proc.stdout.feed_eof()
    fake_proc.stderr = asyncio.StreamReader()
    fake_proc.stderr.feed_eof()
    fake_proc.returncode = 0

    async def fake_create(*args, **kwargs):
        return fake_proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    bound_pids = []
    fake_handle = MagicMock(spec=JobHandle)

    def fake_bind(pid):
        bound_pids.append(pid)
        return fake_handle
    monkeypatch.setattr(nps, "bind_process_to_job", fake_bind)

    src = nps.NativePipeSource(bin_path=str(bin_path))
    async for _ in src.chunks():
        break
    assert bound_pids == [4242]  # bound with the helper's real pid

    await src.close()
    fake_handle.close.assert_called_once()  # handle released on close
    assert src._job is None
