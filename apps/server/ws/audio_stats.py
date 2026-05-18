"""Per-session in-memory audio chunk counters.

In-process only (single-node assumption per ARCH §1). Reset on server restart.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Deque
from uuid import UUID


@dataclass
class AudioStats:
    total_bytes: int = 0
    total_chunks: int = 0
    last_seq: int | None = None
    started_at: datetime | None = None
    last_chunk_at: float | None = None  # monotonic
    stopped_at: datetime | None = None
    stopped_reason: str | None = None
    _recent_chunk_times: Deque[float] = field(default_factory=lambda: deque(maxlen=200))


class AudioStatsRegistry:
    def __init__(self) -> None:
        self._sessions: dict[UUID, AudioStats] = {}
        self._lock = RLock()

    def record(self, session_id: UUID, n_bytes: int) -> None:
        now = time.monotonic()
        with self._lock:
            s = self._sessions.setdefault(session_id, AudioStats())
            s.total_bytes += n_bytes
            s.total_chunks += 1
            s.last_chunk_at = now
            s._recent_chunk_times.append(now)

    def mark_started(self, session_id: UUID, sample_rate: int, channels: int, started_at: datetime) -> None:
        with self._lock:
            s = self._sessions.setdefault(session_id, AudioStats())
            s.started_at = started_at

    def note_seq(self, session_id: UUID, seq: int) -> None:
        with self._lock:
            s = self._sessions.setdefault(session_id, AudioStats())
            s.last_seq = seq

    def mark_stopped(self, session_id: UUID, reason: str | None) -> None:
        with self._lock:
            s = self._sessions.setdefault(session_id, AudioStats())
            s.stopped_at = datetime.now(timezone.utc)
            s.stopped_reason = reason

    def discard(self, session_id: UUID) -> None:
        """Evict a session entry. Call from /ws/sidecar disconnect path to
        bound memory in long-running servers (P1: unbounded dict growth).
        """
        with self._lock:
            self._sessions.pop(session_id, None)

    def snapshot(self, session_id: UUID) -> dict | None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                return None
            now = time.monotonic()
            # chunks per second over the last 1s window
            one_sec_ago = now - 1.0
            cps = sum(1 for t in s._recent_chunk_times if t >= one_sec_ago)
            age_ms = int((now - s.last_chunk_at) * 1000) if s.last_chunk_at else None
            return {
                "total_bytes": s.total_bytes,
                "total_chunks": s.total_chunks,
                "chunks_per_sec_1s": cps,
                "last_seq": s.last_seq,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "stopped_at": s.stopped_at.isoformat() if s.stopped_at else None,
                "stopped_reason": s.stopped_reason,
                "age_ms": age_ms,
            }


audio_stats = AudioStatsRegistry()
