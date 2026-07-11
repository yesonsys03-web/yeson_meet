# === ANCHOR: TEST_APPLE_LIVE_TRANSLATE_START ===
from __future__ import annotations

import sys
import textwrap
import time

from apps.server.ai.apple_live_translate import AppleLiveTranslateProvider
from apps.server.ai.live_session import is_permanent_provider_error


def _fake_bin(tmp_path, body: str) -> list[str]:
    script = tmp_path / "fake_live.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


HAPPY = """\
    import json, sys
    # stdin은 무시 (오디오 소비 시늉만)
    for e in [
        {"type": "status", "state": "ready"},
        {"type": "partial", "seq": 1, "en": "Hello", "ko": "안녕"},
        {"type": "partial", "seq": 1, "en": "Hello there", "ko": "안녕하세요"},
        {"type": "final", "seq": 1, "en": "Hello there.", "ko": "안녕하세요.",
         "t0": 0.0, "t1": 1.5},
        {"type": "final", "seq": 2, "en": "Pencil test.", "ko": "연필 테스트.",
         "t0": 2.0, "t1": 3.0},
    ]:
        print(json.dumps(e, ensure_ascii=False), flush=True)
"""

UNAVAILABLE = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "error",
                      "reason": "unsupported_os"}), flush=True)
    sys.exit(3)
"""

TRANSIENT = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "error",
                      "reason": "live_failed: boom"}), flush=True)
    sys.exit(3)
"""

CRASH = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "ready"}), flush=True)
    print(json.dumps({"type": "partial", "seq": 1, "en": "a", "ko": "아"}), flush=True)
    sys.exit(1)
"""

HANGS_AFTER_EOF = """\
    import json, sys, time
    print(json.dumps({"type": "status", "state": "ready"}), flush=True)
    print(json.dumps({"type": "partial", "seq": 1, "en": "a", "ko": "아"}), flush=True)
    time.sleep(120)
"""


async def _empty_audio():
    yield b"\x00" * 640


async def _no_audio():
    # Async generator with zero items — audio pump completes immediately.
    if False:  # pragma: no cover
        yield b""


async def _collect(provider):
    return [u async for u in provider.stream(_empty_audio(), "en")]


class TestAppleLiveTranslateProvider:
    async def test_partials_and_finals_mapped(self, tmp_path):
        provider = AppleLiveTranslateProvider(argv=_fake_bin(tmp_path, HAPPY))
        out = await _collect(provider)
        assert [(u.seq, u.is_final) for u in out] == [
            (1, False), (1, False), (1, True), (2, True)]
        assert out[2].text_en == "Hello there."
        assert out[0].provider_segment == 1

    async def test_ko_corrections_applied(self, tmp_path):
        provider = AppleLiveTranslateProvider(argv=_fake_bin(tmp_path, HAPPY))
        out = await _collect(provider)
        assert out[3].text_ko == "펜슬 테스트."  # 연필 → 펜슬 (glossary)

    async def test_status_error_is_permanent(self, tmp_path):
        provider = AppleLiveTranslateProvider(argv=_fake_bin(tmp_path, UNAVAILABLE))
        try:
            await _collect(provider)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert is_permanent_provider_error(exc)

    async def test_transient_status_error_is_not_permanent(self, tmp_path):
        provider = AppleLiveTranslateProvider(argv=_fake_bin(tmp_path, TRANSIENT))
        try:
            await _collect(provider)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert not is_permanent_provider_error(exc)

    async def test_crash_raises_transient_error(self, tmp_path):
        provider = AppleLiveTranslateProvider(argv=_fake_bin(tmp_path, CRASH))
        try:
            await _collect(provider)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert not is_permanent_provider_error(exc)  # reconnect 대상

    async def test_provider_segment_increments_per_stream(self, tmp_path):
        provider = AppleLiveTranslateProvider(argv=_fake_bin(tmp_path, HAPPY))
        first = await _collect(provider)
        second = await _collect(provider)
        assert first[0].provider_segment == 1
        assert second[0].provider_segment == 2

    async def test_post_eof_hang_is_guarded(self, tmp_path):
        # 오디오 스트림이 즉시 끝나고(=stdin EOF), 바이너리가 finalize를 하지
        # 않고 영원히 sleep하면 우리 쪽에서 eof_timeout 이후 스스로 정리하고
        # 정상 종료(예외 없이)해야 한다. 이미 받은 partial은 유효.
        provider = AppleLiveTranslateProvider(
            argv=_fake_bin(tmp_path, HANGS_AFTER_EOF), eof_timeout=0.5)
        start = time.monotonic()
        out = [u async for u in provider.stream(_no_audio(), "en")]
        elapsed = time.monotonic() - start
        assert elapsed < 5.0
        assert len(out) == 1
        assert out[0].seq == 1
        assert out[0].is_final is False


class TestCreateProvider:
    def test_selected_when_bin_present(self, tmp_path, monkeypatch):
        from apps.server.ws import sidecar
        monkeypatch.setenv("YESON_AI_PROVIDER", "apple_live_translate")
        fake = tmp_path / "apple-live-translate"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        monkeypatch.setenv("YESON_APPLE_TRANSLATE_BIN", str(fake))
        provider = sidecar.create_ai_provider()
        assert type(provider).__name__ == "AppleLiveTranslateProvider"

    def test_none_when_bin_missing(self, monkeypatch):
        from apps.server.ws import sidecar
        monkeypatch.setenv("YESON_AI_PROVIDER", "apple_live_translate")
        monkeypatch.delenv("YESON_APPLE_TRANSLATE_BIN", raising=False)
        monkeypatch.setattr(
            "apps.server.ai.apple_native.shutil.which", lambda n: None)
        assert sidecar.create_ai_provider() is None
# === ANCHOR: TEST_APPLE_LIVE_TRANSLATE_END ===
