# === ANCHOR: CAPTURE_STATUS_START ===
"""Live capture-status state machine (pure) + reporter + stdout watchdog.

The sidecar can't tell the operator "audio is flowing" today. This emits a
`CAPTURE_STATUS <state>` stdout marker on each transition so the desktop can
show a live chip. Decision logic is pure (timestamps + booleans, injected
clock) so it unit-tests without websockets/asyncio.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

# Silence threshold: well above natural conversational pauses so we flag
# "audio genuinely stopped for a while" (informational), never speech cadence.
SILENCE_THRESHOLD_S = 10.0

# Chunk RMS at/above this dBFS counts as "audio present". Matches
# config.audio.RMS_DBFS_THRESHOLD's default; main.py injects the configured value.
RMS_SILENCE_DBFS = -45.0

CONNECTING = "connecting"
ACTIVE = "active"
SILENT = "silent"
TRANSPORT_DOWN = "transport_down"

MARKER = "CAPTURE_STATUS "


def compute_state(
    *,
    ws_connected: bool,
    ever_connected: bool,
    last_chunk_at: float | None,
    last_loud_at: float | None,
    now: float,
    threshold: float = SILENCE_THRESHOLD_S,
) -> str:
    """Pure: derive the capture state from connection + loudness facts.

    Silence is judged on the last *loud* chunk (RMS >= threshold), not mere chunk
    presence, so it fires on Mac (silent packets keep flowing) as well as Windows
    (no packets in silence). `last_chunk_at` only distinguishes "connecting"
    (no chunk yet) from a flowing-but-quiet stream.
    Priority: transport_down > connecting > silent > active.
    """
    if not ws_connected:
        return TRANSPORT_DOWN if ever_connected else CONNECTING
    if last_chunk_at is None:
        return CONNECTING
    if last_loud_at is None or now - last_loud_at >= threshold:
        return SILENT
    return ACTIVE


class CaptureStatusReporter:
    """Mutable capture facts + transition-coalescing. Updated by the WS loop,
    polled by the watchdog. Tracks the last *loud* chunk for silence."""

    def __init__(
        self,
        threshold: float = SILENCE_THRESHOLD_S,
        rms_threshold_dbfs: float = RMS_SILENCE_DBFS,
    ) -> None:
        self._threshold = threshold
        self._rms_threshold = rms_threshold_dbfs
        self._ws_connected = False
        self._ever_connected = False
        self._last_chunk_at: float | None = None
        self._last_loud_at: float | None = None
        self._emitted: str | None = None

    def set_connected(self, ok: bool) -> None:
        self._ws_connected = ok
        if ok:
            self._ever_connected = True

    def note_chunk(self, now: float, dbfs: float) -> None:
        self._last_chunk_at = now
        if dbfs >= self._rms_threshold:
            self._last_loud_at = now

    def poll(self, now: float) -> str | None:
        """Return the new state iff it changed since the last emit, else None."""
        state = compute_state(
            ws_connected=self._ws_connected,
            ever_connected=self._ever_connected,
            last_chunk_at=self._last_chunk_at,
            last_loud_at=self._last_loud_at,
            now=now,
            threshold=self._threshold,
        )
        if state == self._emitted:
            return None
        self._emitted = state
        return state


async def run_watchdog(
    reporter: CaptureStatusReporter,
    emit: Callable[[str], None],
    *,
    interval: float = 1.0,
    now_fn: Callable[[], float] = time.monotonic,
) -> None:
    """Poll the reporter every `interval`s and `emit` each transition. Runs as a
    standalone asyncio task so it detects silence even while the send loop blocks
    awaiting the next chunk (native path emits no packets during silence)."""
    while True:
        await asyncio.sleep(interval)
        state = reporter.poll(now_fn())
        if state is not None:
            emit(state)
# === ANCHOR: CAPTURE_STATUS_END ===
