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
from collections import deque
from collections.abc import Callable

# Silence threshold: well above natural conversational pauses so we flag
# "audio genuinely stopped for a while" (informational), never speech cadence.
SILENCE_THRESHOLD_S = 10.0

# Long-silence escalation: no loud audio for this many seconds (well past the
# informational SILENCE_THRESHOLD_S) → escalate the silent chip to an actionable
# "check your output device" advisory. Still informational, auto-clears on sound.
STALE_THRESHOLD_S = 30.0

# Chunk RMS at/above this dBFS counts as "audio present". Matches
# config.audio.RMS_DBFS_THRESHOLD's default; main.py injects the configured value.
RMS_SILENCE_DBFS = -45.0

LEVEL_MARKER = "CAPTURE_LEVEL "
LEVEL_WINDOW_S = 1.0  # rolling mean window for the meter
LEVEL_STALE_S = 1.5   # no chunk within this → no signal (None)

CONNECTING = "connecting"
ACTIVE = "active"
SILENT = "silent"
TRANSPORT_DOWN = "transport_down"
NO_AUDIO = "no_audio"

MARKER = "CAPTURE_STATUS "


def compute_state(
    *,
    ws_connected: bool,
    ever_connected: bool,
    last_chunk_at: float | None,
    last_loud_at: float | None,
    now: float,
    threshold: float = SILENCE_THRESHOLD_S,
    connected_at: float | None = None,
    stale_threshold: float = STALE_THRESHOLD_S,
) -> str:
    """Pure: derive the capture state from connection + loudness facts.

    Silence is judged on the last *loud* chunk (RMS >= threshold), not mere chunk
    presence, so it fires on Mac (silent packets keep flowing) as well as Windows
    (no packets in silence). `last_chunk_at` only distinguishes "connecting"
    (no chunk yet) from a flowing-but-quiet stream.

    `no_audio` escalates silence after `stale_threshold`s with no loud audio,
    measured from the last loud chunk or — if audio was never loud — from
    `connected_at`. That second baseline covers the Windows "no packets from the
    start" case, where `last_chunk_at` stays None and the state would otherwise be
    stuck on "connecting" forever.

    Priority: transport_down > no_audio > connecting > silent > active.

    Note: the `no_audio` branch assumes the reporter invariant
    `ws_connected=True ⇒ ever_connected=True`. A direct caller passing
    `ws_connected=True, ever_connected=False` with a stale `connected_at`
    would get NO_AUDIO rather than CONNECTING — that combination is not
    produced by CaptureStatusReporter.
    """
    if not ws_connected:
        return TRANSPORT_DOWN if ever_connected else CONNECTING
    reference = last_loud_at if last_loud_at is not None else connected_at
    if reference is not None and now - reference >= stale_threshold:
        return NO_AUDIO
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
        stale_threshold: float = STALE_THRESHOLD_S,
    ) -> None:
        self._threshold = threshold
        self._rms_threshold = rms_threshold_dbfs
        self._stale_threshold = stale_threshold
        self._ws_connected = False
        self._ever_connected = False
        self._connected_at: float | None = None
        self._last_chunk_at: float | None = None
        self._last_loud_at: float | None = None
        self._levels: deque[tuple[float, float]] = deque(maxlen=200)  # 200 @ ≤50 Hz ≈ 4 s; LEVEL_STALE_S=1.5 s needs ≤75 entries
        self._emitted: str | None = None

    def set_connected(self, ok: bool) -> None:
        self._ws_connected = ok
        if ok:
            self._ever_connected = True

    def note_chunk(self, now: float, dbfs: float) -> None:
        self._last_chunk_at = now
        if dbfs >= self._rms_threshold:
            self._last_loud_at = now
        self._levels.append((now, dbfs))

    def poll(self, now: float) -> str | None:
        """Return the new state iff it changed since the last emit, else None."""
        # Latch here, not in set_connected(), because set_connected() takes no
        # clock. poll() runs on the injected `now`, so we stamp connect time on
        # the first poll while connected (≤1s late vs the 30s threshold —
        # negligible, and keeps the clock injected/testable).
        if self._ws_connected and self._connected_at is None:
            self._connected_at = now
        state = compute_state(
            ws_connected=self._ws_connected,
            ever_connected=self._ever_connected,
            last_chunk_at=self._last_chunk_at,
            last_loud_at=self._last_loud_at,
            now=now,
            threshold=self._threshold,
            connected_at=self._connected_at,
            stale_threshold=self._stale_threshold,
        )
        if state == self._emitted:
            return None
        self._emitted = state
        return state

    def level(self, now: float) -> float | None:
        """Mean dBFS over the last LEVEL_WINDOW_S, or None if no recent chunk.

        Independent of the silence state machine — this feeds the live meter.
        Returns None when the stream is stale (Windows silence = no packets) so
        the desktop never shows a frozen level."""
        if self._last_chunk_at is None or now - self._last_chunk_at > LEVEL_STALE_S:
            return None
        cutoff = now - LEVEL_WINDOW_S
        recent = [d for (t, d) in self._levels if t >= cutoff]
        # staleness guard is the fast path; the window filter also drops chunks older than LEVEL_WINDOW_S
        if not recent:
            return None
        return sum(recent) / len(recent)


def level_marker(dbfs: float) -> str:
    """Canonical CAPTURE_LEVEL stdout line for one meter sample (1 decimal)."""
    rounded = round(dbfs, 1) + 0.0  # + 0.0 collapses -0.0 → 0.0 after rounding
    return f"{LEVEL_MARKER}{rounded:.1f}"


async def run_watchdog(
    reporter: CaptureStatusReporter,
    emit: Callable[[str], None],
    *,
    interval: float = 1.0,
    now_fn: Callable[[], float] = time.monotonic,
) -> None:
    """Poll every `interval`s. On each state transition emit a full
    `CAPTURE_STATUS <state>` line; every tick the stream is live emit a
    `CAPTURE_LEVEL <dbfs>` line. Owning the full marker text here keeps marker
    formatting in one module (the caller just prints whatever it receives).
    Runs standalone so silence is detected even while the send loop blocks."""
    while True:
        await asyncio.sleep(interval)
        now = now_fn()
        state = reporter.poll(now)
        if state is not None:
            emit(f"{MARKER}{state}")
        level = reporter.level(now)
        if level is not None:
            emit(level_marker(level))
# === ANCHOR: CAPTURE_STATUS_END ===
