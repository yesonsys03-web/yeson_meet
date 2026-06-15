"""Provider factory: native-only (sounddevice/auto removed, 2026-06-15 cutover)."""
from __future__ import annotations

import logging

import pytest

from apps.client_sidecar.audio.source import AudioSource


def _fake_helper(tmp_path):
    fake_bin = tmp_path / "yeson-mac-audio-helper"
    fake_bin.write_bytes(b"\x00")
    fake_bin.chmod(0o755)
    return fake_bin


def test_factory_returns_native_when_bin_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("YESON_AUDIO_PROVIDER", raising=False)
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", str(_fake_helper(tmp_path)))
    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
    assert isinstance(src, NativePipeSource)
    assert isinstance(src, AudioSource)


def test_factory_raises_when_bin_missing(monkeypatch):
    monkeypatch.delenv("YESON_AUDIO_PROVIDER", raising=False)
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", "/nonexistent/yeson-helper")
    from apps.client_sidecar.audio.sources.factory import make_source
    with pytest.raises(FileNotFoundError):
        make_source()


def test_factory_warns_and_uses_native_for_removed_provider(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "sounddevice")  # removed value
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", str(_fake_helper(tmp_path)))
    from apps.client_sidecar.audio.sources.factory import make_source
    with caplog.at_level(logging.WARNING):
        src = make_source()
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
    assert isinstance(src, NativePipeSource)
    assert any("removed" in r.getMessage() for r in caplog.records)


def test_factory_native_path_does_not_import_sounddevice(monkeypatch, tmp_path):
    """Lean-bundle guard: the native path must not import the sounddevice chain."""
    import sys

    monkeypatch.setitem(sys.modules, "sounddevice", None)
    monkeypatch.setitem(sys.modules, "samplerate", None)
    for name in list(sys.modules):
        if name.startswith("apps.client_sidecar.audio"):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.delenv("YESON_AUDIO_PROVIDER", raising=False)
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", str(_fake_helper(tmp_path)))
    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
    assert isinstance(src, NativePipeSource)
