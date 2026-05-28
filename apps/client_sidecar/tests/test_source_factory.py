"""Provider factory selects source by YESON_AUDIO_PROVIDER env.

Policy: default = ``native``. sounddevice is emergency-fallback opt-in.
``auto`` is a deprecated transition mode kept for back-compat coverage.
"""
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


def test_factory_default_is_native_with_existing_bin(monkeypatch, tmp_path):
    """No YESON_AUDIO_PROVIDER set → default 'native' (not silent sounddevice fallback)."""
    fake_bin = tmp_path / "yeson-mac-audio-helper"
    fake_bin.write_bytes(b"\x00")
    fake_bin.chmod(0o755)
    monkeypatch.delenv("YESON_AUDIO_PROVIDER", raising=False)
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", str(fake_bin))
    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
    assert isinstance(src, NativePipeSource)


def test_factory_default_native_raises_when_bin_missing(monkeypatch):
    """No env set → default 'native' must surface FileNotFoundError, not auto-fallback."""
    monkeypatch.delenv("YESON_AUDIO_PROVIDER", raising=False)
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", "/nonexistent/yeson-helper")
    from apps.client_sidecar.audio.sources.factory import make_source
    with pytest.raises(FileNotFoundError):
        make_source()


def test_factory_native_path_does_not_import_sounddevice(monkeypatch, tmp_path):
    """Lean-bundle guard: the native path must NOT import the sounddevice chain.

    Blocks `sounddevice`/`samplerate` imports and clears cached sidecar audio
    modules, then forces a fresh factory import. Under eager imports the factory
    import itself raises ImportError; under lazy imports the native branch builds
    a NativePipeSource without touching sounddevice.
    """
    import sys

    monkeypatch.setitem(sys.modules, "sounddevice", None)
    monkeypatch.setitem(sys.modules, "samplerate", None)
    # evict cached factory + audio modules so the factory re-import is truly fresh
    for name in list(sys.modules):
        if name.startswith("apps.client_sidecar.audio"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    fake_bin = tmp_path / "yeson-mac-audio-helper"
    fake_bin.write_bytes(b"\x00")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "native")
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", str(fake_bin))

    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
    assert isinstance(src, NativePipeSource)
