# === ANCHOR: NATIVE_PIPE_SOURCE_START ===
"""AudioSource implementation spawning native ScreenCaptureKit helper process.

Reads 640-byte PCM chunks from helper stdout. Helper stderr JSON-line events
are logged at INFO/WARNING.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator

from apps.client_sidecar.audio.source import AudioSource
from apps.client_sidecar.audio.sources.win_job_object import JobHandle, bind_process_to_job
from apps.client_sidecar.config.audio import CHUNK_BYTES

logger = logging.getLogger(__name__)


class NativeCaptureError(RuntimeError):
    """Native helper failed to capture (permission denied, start failure, crash).

    Carries a machine-readable ``reason`` (from the helper's ``fatal`` event) so
    the sidecar can surface a recognizable status instead of dying silently.
    """

    def __init__(self, reason: str):
        super().__init__(f"native capture failed: {reason}")
        self.reason = reason


class NativePipeSource(AudioSource):
    """Spawn native helper, stream stdout PCM, parse stderr JSON events."""

    def __init__(self, bin_path: str):
        self._bin_path = bin_path
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._failure_reason: str | None = None
        self._job: JobHandle | None = None

    async def _spawn(self) -> asyncio.subprocess.Process:
        if not os.path.isfile(self._bin_path):
            raise FileNotFoundError(f"native helper not found: {self._bin_path}")
        proc = await asyncio.create_subprocess_exec(
            self._bin_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info("native helper spawned pid=%s bin=%s", proc.pid, self._bin_path)
        self._proc = proc
        # Windows: bind the helper to a kill-on-close Job Object so a hard-killed
        # sidecar can't orphan it during silence (no-op / None off Windows).
        self._job = bind_process_to_job(proc.pid)
        self._stderr_task = asyncio.create_task(self._drain_stderr(proc.stderr))
        return proc

    async def _drain_stderr(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            try:
                evt = json.loads(line)
                logger.info("native helper event: %s", evt)
                if evt.get("event") == "fatal":
                    payload = evt.get("payload") or {}
                    self._failure_reason = payload.get("reason") or "fatal"
                    logger.error("native helper fatal: %s", payload)
            except json.JSONDecodeError:
                logger.warning("native helper non-json stderr: %r", line[:200])

    async def chunks(self) -> AsyncIterator[bytes]:
        proc = await self._spawn()
        stdout = proc.stdout
        if stdout is None:
            raise RuntimeError("native helper has no stdout")
        try:
            while True:
                chunk = await stdout.readexactly(CHUNK_BYTES)
                yield chunk
        except asyncio.IncompleteReadError as e:
            if e.partial:
                logger.warning("native helper closed mid-chunk (%d bytes)", len(e.partial))
            else:
                logger.info("native helper stdout closed")

        # Helper exited: let stderr events drain so a `fatal` reason is captured,
        # then surface it as a typed error instead of ending the stream silently.
        if self._stderr_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._stderr_task), timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        if self._failure_reason is not None:
            raise NativeCaptureError(self._failure_reason)

    async def close(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._proc.kill()
            self._proc = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
        # Release the job handle last: closing it KILL_ON_JOB_CLOSE-reaps the
        # helper, a backstop if terminate()/kill() above didn't take.
        if self._job is not None:
            self._job.close()
            self._job = None
# === ANCHOR: NATIVE_PIPE_SOURCE_END ===
