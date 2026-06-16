"""Pure capture-status decider + reporter coalescing (RMS-loudness silence)."""
import asyncio

from apps.client_sidecar.transport.capture_status import (
    ACTIVE,
    CONNECTING,
    NO_AUDIO,
    SILENT,
    TRANSPORT_DOWN,
    CaptureStatusReporter,
    compute_state,
    level_marker,
    run_watchdog,
)

T = 10.0      # silence time threshold
LOUD = -10.0  # dBFS above the -45 default → counts as audio
QUIET = -80.0 # dBFS below threshold → silence


def test_connecting_before_first_connect():
    assert compute_state(
        ws_connected=False, ever_connected=False,
        last_chunk_at=None, last_loud_at=None, now=5.0, threshold=T,
    ) == CONNECTING


def test_connecting_after_connect_before_first_chunk():
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=None, last_loud_at=None, now=5.0, threshold=T,
    ) == CONNECTING


def test_active_on_recent_loud_chunk():
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=105.0, last_loud_at=100.0, now=105.0, threshold=T,
    ) == ACTIVE


def test_silent_after_threshold_since_last_loud():
    # gap from last LOUD chunk hits threshold → silent (even though a chunk arrived at 105)
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=105.0, last_loud_at=100.0, now=110.0, threshold=T,
    ) == SILENT


def test_silent_when_chunks_flow_but_never_loud():
    # THE Mac case: chunks present (last_chunk_at recent) but no loud chunk ever → silent
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=110.0, last_loud_at=None, now=110.0, threshold=T,
    ) == SILENT


def test_transport_down_after_disconnect():
    assert compute_state(
        ws_connected=False, ever_connected=True,
        last_chunk_at=100.0, last_loud_at=100.0, now=101.0, threshold=T,
    ) == TRANSPORT_DOWN


def test_transport_down_takes_priority_over_silence():
    assert compute_state(
        ws_connected=False, ever_connected=True,
        last_chunk_at=100.0, last_loud_at=100.0, now=100.5, threshold=T,
    ) == TRANSPORT_DOWN


def test_reporter_emits_only_on_transition():
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0, dbfs=LOUD)
    assert r.poll(now=101.0) == ACTIVE
    assert r.poll(now=102.0) is None
    assert r.poll(now=111.0) == SILENT   # 11s since last loud
    assert r.poll(now=112.0) is None


def test_reporter_instant_recovery_from_silent():
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0, dbfs=LOUD)
    assert r.poll(now=111.0) == SILENT
    r.note_chunk(now=111.5, dbfs=LOUD)         # one loud chunk
    assert r.poll(now=111.6) == ACTIVE          # instant recovery


def test_reporter_mac_silence_despite_flowing_chunks():
    # Regression guard for the whole slice: on Mac, chunks keep arriving during
    # silence (last_chunk_at advances) but they are all quiet → still goes silent.
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0, dbfs=LOUD)
    assert r.poll(now=100.5) == ACTIVE
    for t in (101.0, 103.0, 105.0, 107.0, 109.0):
        r.note_chunk(now=t, dbfs=QUIET)        # quiet chunks STILL arrive (the Mac trap)
    assert r.poll(now=109.5) is None             # 9.5s since last loud — ACTIVE state unchanged (coalesced, no transition)
    r.note_chunk(now=110.5, dbfs=QUIET)
    assert r.poll(now=110.5) == SILENT          # 10.5s since last loud → silent


def test_reporter_transport_down_then_reconnect():
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0, dbfs=LOUD)
    assert r.poll(now=100.1) == ACTIVE
    r.set_connected(False)
    assert r.poll(now=100.2) == TRANSPORT_DOWN
    r.set_connected(True)
    assert r.poll(now=100.3) == ACTIVE


def test_reporter_starts_connecting():
    r = CaptureStatusReporter(threshold=T)
    assert r.poll(now=0.5) == CONNECTING


def test_level_none_before_any_chunk():
    r = CaptureStatusReporter(threshold=T)
    assert r.level(now=1.0) is None


def test_level_mean_of_recent_chunks():
    r = CaptureStatusReporter(threshold=T)
    r.note_chunk(now=100.0, dbfs=-20.0)
    r.note_chunk(now=100.5, dbfs=-30.0)
    assert r.level(now=100.6) == -25.0  # mean of chunks within the 1s window


def test_level_excludes_chunks_outside_window():
    r = CaptureStatusReporter(threshold=T)
    r.note_chunk(now=100.0, dbfs=-60.0)  # >1s before now=101.2 → excluded
    r.note_chunk(now=101.0, dbfs=-20.0)
    assert r.level(now=101.2) == -20.0


def test_level_none_when_stale():
    r = CaptureStatusReporter(threshold=T)
    r.note_chunk(now=100.0, dbfs=-20.0)
    assert r.level(now=102.0) is None  # >1.5s since last chunk → no signal


def test_level_marker_format():
    assert level_marker(-28.37) == "CAPTURE_LEVEL -28.4"
    assert level_marker(-6.0) == "CAPTURE_LEVEL -6.0"
    assert level_marker(-0.049) == "CAPTURE_LEVEL 0.0"


async def test_watchdog_emits_full_state_and_level_lines():
    emitted: list[str] = []
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0, dbfs=LOUD)

    task = asyncio.create_task(
        run_watchdog(r, emitted.append, interval=0.001, now_fn=lambda: 100.0)
    )
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # State line is fully formed (MARKER prepended by the watchdog, not the caller)
    assert "CAPTURE_STATUS active" in emitted
    # Level telemetry emitted every tick while the stream is live
    assert any(m.startswith("CAPTURE_LEVEL ") for m in emitted)


async def test_watchdog_skips_level_when_no_chunk():
    emitted: list[str] = []
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)  # connected but no chunk yet → connecting, no level

    task = asyncio.create_task(
        run_watchdog(r, emitted.append, interval=0.001, now_fn=lambda: 100.0)
    )
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert "CAPTURE_STATUS connecting" in emitted
    assert not any(m.startswith("CAPTURE_LEVEL ") for m in emitted)


# --- no_audio state tests ---

STALE = 30.0  # no-audio escalation threshold


def test_no_audio_when_silent_from_start_after_stale():
    # Windows zero-packets from the start: no chunk ever, connected 30s ago → no_audio
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=None, last_loud_at=None, now=130.0,
        threshold=T, connected_at=100.0, stale_threshold=STALE,
    ) == NO_AUDIO


def test_connecting_before_stale_with_no_chunk():
    # same case, only 29s in → still connecting (not yet escalated)
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=None, last_loud_at=None, now=129.0,
        threshold=T, connected_at=100.0, stale_threshold=STALE,
    ) == CONNECTING


def test_no_audio_after_stale_since_last_loud():
    # was active, then quiet past the stale threshold → no_audio (escalates from silent)
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=130.0, last_loud_at=100.0, now=130.0,
        threshold=T, connected_at=100.0, stale_threshold=STALE,
    ) == NO_AUDIO


def test_silent_between_thresholds_since_last_loud():
    # 10s..30s since last loud → still silent, not yet no_audio
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=120.0, last_loud_at=100.0, now=120.0,
        threshold=T, connected_at=100.0, stale_threshold=STALE,
    ) == SILENT


def test_transport_down_takes_priority_over_no_audio():
    assert compute_state(
        ws_connected=False, ever_connected=True,
        last_chunk_at=None, last_loud_at=None, now=200.0,
        threshold=T, connected_at=100.0, stale_threshold=STALE,
    ) == TRANSPORT_DOWN


def test_reporter_escalates_connecting_to_no_audio():
    r = CaptureStatusReporter(threshold=T, stale_threshold=STALE)
    r.set_connected(True)
    assert r.poll(now=100.0) == CONNECTING   # connected, no chunk yet; connected_at=100
    assert r.poll(now=129.0) is None          # state unchanged (still connecting, 29s)
    assert r.poll(now=130.0) == NO_AUDIO       # 30s with no audio → escalate


def test_reporter_recovers_from_no_audio_on_loud_chunk():
    r = CaptureStatusReporter(threshold=T, stale_threshold=STALE)
    r.set_connected(True)
    assert r.poll(now=100.0) == CONNECTING
    assert r.poll(now=130.0) == NO_AUDIO
    r.note_chunk(now=131.0, dbfs=LOUD)
    assert r.poll(now=131.1) == ACTIVE          # instant recovery


def test_reporter_active_silent_no_audio_progression():
    r = CaptureStatusReporter(threshold=T, stale_threshold=STALE)
    r.set_connected(True)
    r.note_chunk(now=100.0, dbfs=LOUD)
    assert r.poll(now=100.5) == ACTIVE
    assert r.poll(now=111.0) == SILENT          # 11s since last loud
    assert r.poll(now=131.0) == NO_AUDIO         # 31s since last loud


def test_reporter_no_audio_persists_across_reconnect():
    # Spec: connected_at is NOT reset on reconnect. After no_audio escalates,
    # a transport_down then reconnect re-fires no_audio immediately (>=30s elapsed).
    r = CaptureStatusReporter(threshold=T, stale_threshold=STALE)
    r.set_connected(True)
    assert r.poll(now=100.0) == CONNECTING
    assert r.poll(now=130.0) == NO_AUDIO
    r.set_connected(False)
    assert r.poll(now=131.0) == TRANSPORT_DOWN
    r.set_connected(True)
    assert r.poll(now=132.0) == NO_AUDIO   # connected_at NOT reset → still >=30s → no_audio
