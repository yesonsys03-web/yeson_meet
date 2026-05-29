"""NATIVE_HELPER_BIN_PATH default must be platform-correct."""
import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def _restore_audio_module():
    """Reloading config.audio mutates it in sys.modules; restore the real-platform
    state after each test so other test files don't inherit a win32/darwin reload."""
    yield
    import apps.client_sidecar.config.audio as audio
    importlib.reload(audio)  # sys.platform is back to real here (monkeypatch undone)


def _reload_with_platform(monkeypatch, platform_str):
    monkeypatch.setattr(sys, "platform", platform_str)
    monkeypatch.delenv("YESON_NATIVE_HELPER_BIN", raising=False)
    import apps.client_sidecar.config.audio as audio
    return importlib.reload(audio)


def test_windows_default_points_at_win_helper_exe(monkeypatch):
    audio = _reload_with_platform(monkeypatch, "win32")
    assert audio.NATIVE_HELPER_BIN_PATH.endswith("yeson-win-audio-helper.exe")
    assert "apps" in audio.NATIVE_HELPER_BIN_PATH
    assert "native_helper_win" in audio.NATIVE_HELPER_BIN_PATH
    assert "target" in audio.NATIVE_HELPER_BIN_PATH
    assert "release" in audio.NATIVE_HELPER_BIN_PATH


def test_macos_default_unchanged(monkeypatch):
    audio = _reload_with_platform(monkeypatch, "darwin")
    assert audio.NATIVE_HELPER_BIN_PATH.endswith("yeson-mac-audio-helper")
