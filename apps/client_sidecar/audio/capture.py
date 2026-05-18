"""sounddevice InputStream → asyncio bridge yielding 20ms PCM int16 LE chunks."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import sounddevice as sd

from apps.client_sidecar.audio.resample import Resampler
from apps.client_sidecar.audio.rms import RmsLogger, rms_dbfs
from apps.client_sidecar.config.audio import (
    CHUNK_BYTES,
    CHUNK_SAMPLES,
    RMS_DBFS_THRESHOLD,
    TARGET_SAMPLE_RATE,
)

logger = logging.getLogger(__name__)


async def capture_chunks(device: dict[str, Any]) -> AsyncIterator[bytes]:
    """Yield 640-byte PCM int16 mono LE chunks at TARGET_SAMPLE_RATE.

    Uses a thread-pinned sd.InputStream → asyncio.Queue bridge.
    """
    src_rate = int(device["default_samplerate"])
    dev_idx = device.get("_yeson_index")
    in_channels = min(device["max_input_channels"], 2)  # cap at stereo; downmix to mono

    loop = asyncio.get_running_loop()
    out_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2000)  # ≈40s of 20ms chunks
    err_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=8)

    resampler = Resampler(src_rate=src_rate, dst_rate=TARGET_SAMPLE_RATE)
    rms_logger = RmsLogger(RMS_DBFS_THRESHOLD)
    # Sample buffer (float32, mono, target rate) accumulated between callbacks
    pending = np.zeros(0, dtype=np.float32)
    # Drop counter — incremented when the asyncio.Queue is full (caller can't
    # keep up, typically during reconnect backoff). Bridged out via the loop
    # by the consumer; logged every LOG_EVERY drops to avoid log spam.
    drop_count = 0
    LOG_EVERY = 100

    def _enqueue(payload: bytes) -> None:
        nonlocal drop_count
        try:
            out_queue.put_nowait(payload)
        except asyncio.QueueFull:
            drop_count += 1
            if drop_count % LOG_EVERY == 0:
                logger.warning(
                    "audio chunk dropped: queue full (%d drops total, ~%.1fs lost)",
                    drop_count, drop_count * 0.02,
                )

    def _callback(indata: np.ndarray, frames: int, time_info, status_flags) -> None:
        nonlocal pending
        if status_flags:
            try:
                err_queue.put_nowait(str(status_flags))
            except asyncio.QueueFull:
                pass

        # indata shape: (frames, channels). Downmix to mono.
        if indata.ndim == 2 and indata.shape[1] > 1:
            mono = indata.mean(axis=1, dtype=np.float32)
        else:
            mono = indata.reshape(-1).astype(np.float32, copy=False)

        resampled = resampler.process(mono)
        if resampled.size == 0:
            return
        pending = np.concatenate([pending, resampled])

        # Emit 320-sample chunks
        while pending.size >= CHUNK_SAMPLES:
            chunk = pending[:CHUNK_SAMPLES]
            pending = pending[CHUNK_SAMPLES:]
            dbfs = rms_dbfs(chunk)
            avg, low = rms_logger.observe(dbfs)
            # Convert to int16 LE
            clipped = np.clip(chunk * 32767.0, -32768, 32767).astype(np.int16)
            payload = clipped.tobytes()
            assert len(payload) == CHUNK_BYTES
            try:
                loop.call_soon_threadsafe(_enqueue, payload)
            except RuntimeError:
                # Loop closed during shutdown
                return

    stream = sd.InputStream(
        device=dev_idx,
        channels=in_channels,
        samplerate=src_rate,
        dtype="float32",
        blocksize=0,
        callback=_callback,
    )
    logger.info(
        "audio capture starting: src=%dHz ch=%d → %dHz mono, chunk=%dB",
        src_rate, in_channels, TARGET_SAMPLE_RATE, CHUNK_BYTES,
    )
    stream.start()
    try:
        while True:
            # Drain error queue (non-blocking)
            while not err_queue.empty():
                err = err_queue.get_nowait()
                logger.warning("sounddevice status: %s", err)
            chunk = await out_queue.get()
            yield chunk
    finally:
        stream.stop()
        stream.close()
        logger.info("audio capture stopped")
