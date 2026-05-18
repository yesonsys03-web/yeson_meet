# === ANCHOR: AUDIO_WS_START ===
"""S2 audio mode WebSocket sender: audio.started + binary chunks + chunk_meta + audio.stopped.

Reconnects with exponential backoff (1→30s). Memory-only queue (Slice 5 will add SQLite).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import websockets
from websockets.exceptions import ConnectionClosed

from apps.client_sidecar.config.audio import (
    TARGET_CHANNELS,
    TARGET_SAMPLE_RATE,
)

logger = logging.getLogger(__name__)

CHUNK_META_INTERVAL = 50  # emit chunk_meta every N chunks (≈1s)


# === ANCHOR: AUDIO_WS_STREAM_AUDIO_START ===
async def stream_audio(url: str, chunks: AsyncIterator[bytes]) -> None:
    """Connect, send audio.started, stream binary chunks, periodic chunk_meta, audio.stopped on exit."""
    backoff = 1.0
    seq = 0
    stopped_reason: str | None = None

    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                logger.info("audio ws connected: %s", _safe_url(url))
                backoff = 1.0

                # audio.started
                await ws.send(json.dumps({
                    "type": "audio.started",
                    "sample_rate": TARGET_SAMPLE_RATE,
                    "channels": TARGET_CHANNELS,
                    "format": "pcm_s16le",
                    "started_at": _iso_now(),
                }))

                try:
                    async for chunk in chunks:
                        seq += 1
                        await ws.send(chunk)  # binary
                        if seq % CHUNK_META_INTERVAL == 0:
                            await ws.send(json.dumps({
                                "type": "chunk_meta",
                                "seq": seq,
                                "started_at": _iso_now(),
                            }))
                    stopped_reason = "stream exhausted"
                except (asyncio.CancelledError, KeyboardInterrupt):
                    stopped_reason = "user cancel"
                    raise
                finally:
                    try:
                        await ws.send(json.dumps({
                            "type": "audio.stopped",
                            "reason": stopped_reason,
                        }))
                    except Exception:
                        pass
                return
        except (ConnectionClosed, OSError) as e:
            logger.warning("audio ws closed: %s — reconnect in %.1fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
# === ANCHOR: AUDIO_WS_STREAM_AUDIO_END ===


# === ANCHOR: AUDIO_WS__ISO_NOW_START ===
def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
# === ANCHOR: AUDIO_WS__ISO_NOW_END ===


# === ANCHOR: AUDIO_WS__SAFE_URL_START ===
def _safe_url(url: str) -> str:
    # mask api key in query string for logs
    return url.split("?")[0] + "?key=<redacted>"
# === ANCHOR: AUDIO_WS__SAFE_URL_END ===
# === ANCHOR: AUDIO_WS_END ===
