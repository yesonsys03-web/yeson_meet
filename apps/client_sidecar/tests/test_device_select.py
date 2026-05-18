"""Unit tests for find_input_device (sounddevice monkeypatched)."""
from __future__ import annotations

import pytest

from apps.client_sidecar.audio.device import find_input_device

_BLACKHOLE_DEV = {
    "name": "BlackHole 2ch",
    "max_input_channels": 2,
    "default_samplerate": 48000.0,
}
_NOINPUT_DEV = {
    "name": "Built-in Output",
    "max_input_channels": 0,
    "default_samplerate": 48000.0,
}


def _make_query_devices_fn(device_list: list[dict]) -> object:
    """Return a callable that mimics sd.query_devices(i=None) dual signature."""
    def _query(i=None):
        if i is None:
            return device_list
        return device_list[i]
    return _query


def test_regex_match_blackhole(monkeypatch) -> None:
    """Device list with BlackHole 2ch → matching dict returned, _yeson_index set."""
    devices = [_NOINPUT_DEV, _BLACKHOLE_DEV]
    monkeypatch.setattr("sounddevice.query_devices", _make_query_devices_fn(devices))

    result = find_input_device(r"(?i)blackhole")

    assert result["name"] == "BlackHole 2ch"
    assert "_yeson_index" in result
    assert result["_yeson_index"] == 1  # index 1 in the list


def test_no_match_raises_runtime_error(monkeypatch) -> None:
    """Empty device list → RuntimeError with 'BlackHole' in message."""
    monkeypatch.setattr("sounddevice.query_devices", _make_query_devices_fn([]))

    with pytest.raises(RuntimeError, match="BlackHole"):
        find_input_device(r"(?i)blackhole")


def test_no_match_non_blackhole_device(monkeypatch) -> None:
    """Device list without BlackHole → RuntimeError with 'BlackHole' in message."""
    devices = [_NOINPUT_DEV]
    monkeypatch.setattr("sounddevice.query_devices", _make_query_devices_fn(devices))

    with pytest.raises(RuntimeError, match="BlackHole"):
        find_input_device(r"(?i)blackhole")


def test_index_override_returns_device(monkeypatch) -> None:
    """Explicit index_override → device at that index returned."""
    devices = [_NOINPUT_DEV, _BLACKHOLE_DEV]
    monkeypatch.setattr("sounddevice.query_devices", _make_query_devices_fn(devices))

    result = find_input_device(r"(?i)blackhole", index_override=1)

    assert result["name"] == "BlackHole 2ch"
    assert result["max_input_channels"] > 0
