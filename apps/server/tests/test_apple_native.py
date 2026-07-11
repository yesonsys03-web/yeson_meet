# === ANCHOR: TEST_APPLE_NATIVE_START ===
from __future__ import annotations

import stat

from apps.server.ai import apple_native


def _make_fake_bin(tmp_path):
    p = tmp_path / "apple-live-translate"
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


class TestResolveAppleBin:
    def test_env_var_wins(self, tmp_path, monkeypatch):
        p = _make_fake_bin(tmp_path)
        monkeypatch.setenv(apple_native.APPLE_BIN_ENV, str(p))
        assert apple_native.resolve_apple_bin() == str(p)

    def test_env_pointing_to_missing_file_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv(apple_native.APPLE_BIN_ENV, str(tmp_path / "nope"))
        monkeypatch.setattr(apple_native.shutil, "which", lambda name: None)
        assert apple_native.resolve_apple_bin() is None


class TestAvailability:
    def test_mt_needs_macos_15(self, tmp_path, monkeypatch):
        p = _make_fake_bin(tmp_path)
        monkeypatch.setenv(apple_native.APPLE_BIN_ENV, str(p))
        monkeypatch.setattr(apple_native, "_is_apple_silicon_mac", lambda: True)
        monkeypatch.setattr(apple_native, "_macos_major", lambda: 15)
        assert apple_native.apple_mt_available() is True
        assert apple_native.apple_stt_available() is False  # STT는 26 필요

    def test_stt_on_macos_26(self, tmp_path, monkeypatch):
        p = _make_fake_bin(tmp_path)
        monkeypatch.setenv(apple_native.APPLE_BIN_ENV, str(p))
        monkeypatch.setattr(apple_native, "_is_apple_silicon_mac", lambda: True)
        monkeypatch.setattr(apple_native, "_macos_major", lambda: 26)
        assert apple_native.apple_stt_available() is True

    def test_unavailable_off_mac(self, monkeypatch):
        monkeypatch.setattr(apple_native, "_is_apple_silicon_mac", lambda: False)
        assert apple_native.apple_mt_available() is False
        assert apple_native.apple_stt_available() is False
# === ANCHOR: TEST_APPLE_NATIVE_END ===
