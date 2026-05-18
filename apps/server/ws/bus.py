# === ANCHOR: BUS_START ===
"""In-memory pub/sub bus for utterance fan-out. Implementation lands in S1-L1.

The bus routes ``DomainEvent`` payloads from /ws/sidecar to subscribed
/ws/viewer connections, keyed by session_id. Slice 1 keeps it in-process
(no Redis); β-scale moves to a real broker.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from uuid import UUID


# === ANCHOR: BUS_INMEMORYBUS_START ===
class InMemoryBus:
    """Per-process fan-out. Not durable; not multi-worker safe."""

    # === ANCHOR: BUS___INIT___START ===
    def __init__(self) -> None:
        self._queues: dict[UUID, set[asyncio.Queue]] = defaultdict(set)
    # === ANCHOR: BUS___INIT___END ===

    # === ANCHOR: BUS_SUBSCRIBE_START ===
    def subscribe(self, session_id: UUID) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._queues[session_id].add(q)
        return q
    # === ANCHOR: BUS_SUBSCRIBE_END ===

    # === ANCHOR: BUS_UNSUBSCRIBE_START ===
    def unsubscribe(self, session_id: UUID, queue: asyncio.Queue) -> None:
        self._queues[session_id].discard(queue)
        if not self._queues[session_id]:
            del self._queues[session_id]
    # === ANCHOR: BUS_UNSUBSCRIBE_END ===

    # === ANCHOR: BUS_PUBLISH_START ===
# === ANCHOR: BUS_INMEMORYBUS_END ===
    async def publish(self, session_id: UUID, payload: dict) -> None:
        for q in list(self._queues.get(session_id, ())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # drop on overflow; viewer must reconnect with ?since=
                pass
    # === ANCHOR: BUS_PUBLISH_END ===


bus = InMemoryBus()
# === ANCHOR: BUS_END ===
