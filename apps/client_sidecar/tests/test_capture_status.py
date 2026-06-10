"""Pure capture-status decider + reporter coalescing."""
from apps.client_sidecar.transport.capture_status import (
    ACTIVE,
    CONNECTING,
    SILENT,
    TRANSPORT_DOWN,
    CaptureStatusReporter,
    compute_state,
)

T = 10.0  # silence threshold used in tests


def test_connecting_before_first_connect():
    assert (
        compute_state(ws_connected=False, ever_connected=False, last_chunk_at=None, now=5.0, threshold=T)
        == CONNECTING
    )


def test_connecting_after_connect_before_first_chunk():
    assert (
        compute_state(ws_connected=True, ever_connected=True, last_chunk_at=None, now=5.0, threshold=T)
        == CONNECTING
    )


def test_active_on_recent_chunk():
    assert (
        compute_state(ws_connected=True, ever_connected=True, last_chunk_at=100.0, now=105.0, threshold=T)
        == ACTIVE
    )


def test_silent_after_threshold():
    # gap exactly at threshold counts as silent
    assert (
        compute_state(ws_connected=True, ever_connected=True, last_chunk_at=100.0, now=110.0, threshold=T)
        == SILENT
    )


def test_transport_down_after_disconnect():
    assert (
        compute_state(ws_connected=False, ever_connected=True, last_chunk_at=100.0, now=101.0, threshold=T)
        == TRANSPORT_DOWN
    )


def test_transport_down_takes_priority_over_silence():
    # ws down + recent chunk → transport_down, not active/silent
    assert (
        compute_state(ws_connected=False, ever_connected=True, last_chunk_at=100.0, now=100.5, threshold=T)
        == TRANSPORT_DOWN
    )


def test_reporter_emits_only_on_transition():
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0)
    assert r.poll(now=101.0) == ACTIVE   # first active → emit
    assert r.poll(now=102.0) is None     # still active → coalesced
    # 10s with no new chunk → silent
    assert r.poll(now=111.0) == SILENT
    assert r.poll(now=112.0) is None     # still silent → coalesced


def test_reporter_instant_recovery_from_silent():
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0)
    assert r.poll(now=111.0) == SILENT
    # a single new chunk → next poll is active immediately (asymmetric hysteresis)
    r.note_chunk(now=111.5)
    assert r.poll(now=111.6) == ACTIVE


def test_reporter_transport_down_then_reconnect():
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0)
    assert r.poll(now=100.1) == ACTIVE
    r.set_connected(False)               # ws dropped
    assert r.poll(now=100.2) == TRANSPORT_DOWN
    r.set_connected(True)                # reconnected; recent chunk still within threshold
    assert r.poll(now=100.3) == ACTIVE


def test_reporter_starts_connecting():
    r = CaptureStatusReporter(threshold=T)
    assert r.poll(now=0.5) == CONNECTING
