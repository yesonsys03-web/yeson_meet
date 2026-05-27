"""Provider factory selects source by YESON_AUDIO_PROVIDER env, with auto fallback."""
from __future__ import annotations

import pytest

from apps.client_sidecar.audio.source import AudioSource


def test_factory_returns_sounddevice_for_explicit_env(monkeypatch):
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "sounddevice")
    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.sounddevice_source import SoundDeviceSource
    assert isinstance(src, SoundDeviceSource)


def test_factory_returns_native_when_explicit_and_bin_exists(monkeypatch, tmp_path):
    fake_bin = tmp_path / "yeson-mac-audio-helper"
    fake_bin.write_bytes(b"\x00")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "native")
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", str(fake_bin))
    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
    assert isinstance(src, NativePipeSource)


def test_factory_auto_falls_back_to_sounddevice_if_native_bin_missing(monkeypatch):
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "auto")
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", "/nonexistent/yeson-helper")
    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.sounddevice_source import SoundDeviceSource
    assert isinstance(src, SoundDeviceSource)


def test_factory_native_explicit_with_missing_bin_raises(monkeypatch):
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "native")
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", "/nonexistent/yeson-helper")
    from apps.client_sidecar.audio.sources.factory import make_source
    with pytest.raises(FileNotFoundError):
        make_source()


def test_factory_auto_prefers_native_when_bin_exists(monkeypatch, tmp_path):
    fake_bin = tmp_path / "yeson-mac-audio-helper"
    fake_bin.write_bytes(b"\x00")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "auto")
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", str(fake_bin))
    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
    assert isinstance(src, NativePipeSource)
