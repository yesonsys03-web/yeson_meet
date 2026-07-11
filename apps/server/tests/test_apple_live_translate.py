# === ANCHOR: TEST_APPLE_LIVE_TRANSLATE_START ===
from __future__ import annotations

import asyncio
import sys
import textwrap
import time

from apps.server.ai.apple_live_translate import (
    AppleLiveTranslateProvider,
    AppleProviderUnavailable,
)
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

# ready를 내보내기 전에 아무 것도 출력하지 않고 멈춘다(언어팩 미설치 시 의심 동작).
NEVER_READY = """\
    import time
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

    async def test_eof_during_inflight_readline_is_guarded(self, tmp_path):
        # TOCTOU 레이스 재현: pump.done()을 확인한 시점엔 아직 안 끝났지만,
        # 그 직후에 건 readline()이 응답 없이 떠 있는 동안 pump가 끝나버리는
        # 경우. 오디오 청크를 하나 보낸 뒤 0.3초를 쉬어(pump가 끝나기 전에
        # readline이 이미 in-flight 상태가 되도록) 스트림을 종료한다. 사전
        # 점검만으로 가드하는 옛 구현이면 이 readline은 무제한으로 걸려
        # 있었을 것 — race를 없앤 구현이어야 eof_timeout 안에 정리된다.
        async def _one_chunk_then_wait():
            yield b"\x00" * 640
            await asyncio.sleep(0.3)

        provider = AppleLiveTranslateProvider(
            argv=_fake_bin(tmp_path, HANGS_AFTER_EOF), eof_timeout=0.5)
        start = time.monotonic()
        out = [u async for u in provider.stream(_one_chunk_then_wait(), "en")]
        elapsed = time.monotonic() - start
        assert elapsed < 3.0
        assert len(out) == 1
        assert out[0].seq == 1
        assert out[0].is_final is False

    async def test_ready_timeout_is_permanent_unavailable(self, tmp_path):
        # 바이너리가 status ready 이전에 조용히 멈추면(언어팩 미설치 의심) ready_timeout
        # 안에 프로세스를 죽이고 영구 에러를 던져야 한다 — 무한 대기 금지.
        provider = AppleLiveTranslateProvider(
            argv=_fake_bin(tmp_path, NEVER_READY), ready_timeout=0.5)
        start = time.monotonic()
        try:
            await _collect(provider)
            assert False, "expected AppleProviderUnavailable"
        except AppleProviderUnavailable as exc:
            assert is_permanent_provider_error(exc)
        elapsed = time.monotonic() - start
        assert elapsed < 2.0

    async def test_binary_not_found_is_permanent_unavailable(self, monkeypatch):
        import apps.server.ai.apple_live_translate as apple_live_translate_mod

        monkeypatch.setattr(
            apple_live_translate_mod, "resolve_apple_bin", lambda: None)
        provider = AppleLiveTranslateProvider(argv=None)
        try:
            await _collect(provider)
            assert False, "expected AppleProviderUnavailable"
        except AppleProviderUnavailable as exc:
            assert is_permanent_provider_error(exc)


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
