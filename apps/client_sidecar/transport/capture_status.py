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
    now: float,
    threshold: float = SILENCE_THRESHOLD_S,
) -> str:
    """Pure: derive the capture state from connection + chunk-flow facts.

    Priority: transport_down (ws lost after connecting) > connecting (pre-first
    chunk) > silent (gap >= threshold) > active. `ever_connected` distinguishes
    startup (connecting) from a mid-session drop (transport_down).
    """
    if not ws_connected:
        return TRANSPORT_DOWN if ever_connected else CONNECTING
    if last_chunk_at is None:
        return CONNECTING
    if now - last_chunk_at >= threshold:
        return SILENT
    return ACTIVE


class CaptureStatusReporter:
    """Mutable capture facts + transition-coalescing. Updated by the WS loop,
    polled by the watchdog."""

    def __init__(self, threshold: float = SILENCE_THRESHOLD_S) -> None:
        self._threshold = threshold
        self._ws_connected = False
        self._ever_connected = False
        self._last_chunk_at: float | None = None
        self._emitted: str | None = None

    def set_connected(self, ok: bool) -> None:
        self._ws_connected = ok
        if ok:
            self._ever_connected = True

    def note_chunk(self, now: float) -> None:
        self._last_chunk_at = now

    def poll(self, now: float) -> str | None:
        """Return the new state iff it changed since the last emit, else None."""
        state = compute_state(
            ws_connected=self._ws_connected,
            ever_connected=self._ever_connected,
            last_chunk_at=self._last_chunk_at,
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
