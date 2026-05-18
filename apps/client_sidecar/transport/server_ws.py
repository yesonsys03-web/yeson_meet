# === ANCHOR: SERVER_WS_START ===
"""WebSocket client for the yeson-meet server /ws/sidecar endpoint.

Backoff schedule: 1s, 2s, 4s, 8s, 16s, 30s (capped at 30s).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


# === ANCHOR: SERVER_WS_SEND_EVENTS_START ===
async def send_events(url: str, events: AsyncIterator[dict]) -> None:
    """Connect to server WS, stream events as JSON. Auto-reconnect with backoff."""
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                logger.info("connected to %s", url)
                backoff = 1.0  # reset
                async for evt_dict in events:
                    await ws.send(json.dumps(evt_dict))
                    logger.debug("sent seq=%s", evt_dict.get("seq"))
                return  # generator exhausted (unlikely in MVP-α — infinite stream)
        except (ConnectionClosed, OSError) as e:
            logger.warning("ws closed: %s — reconnect in %.1fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
# === ANCHOR: SERVER_WS_SEND_EVENTS_END ===
# === ANCHOR: SERVER_WS_END ===
