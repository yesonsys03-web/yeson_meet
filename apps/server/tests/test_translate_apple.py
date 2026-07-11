# === ANCHOR: TEST_TRANSLATE_APPLE_START ===
from __future__ import annotations

import sys
import textwrap

import pytest

from apps.server.domain.video_captions.translate import TranslationError
from apps.server.domain.video_captions.translate_apple import AppleTranslator


def _fake_bin(tmp_path, body: str):
    """stdin JSON 배열을 읽어 body 로직대로 응답하는 가짜 apple-live-translate."""
    script = tmp_path / "fake_apple.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


ECHO_KO = """\
    import json, sys
    texts = json.load(sys.stdin)
    print(json.dumps([f"KO:{t}" for t in texts], ensure_ascii=False))
"""

WRONG_LEN = """\
    import json, sys
    json.load(sys.stdin)
    print(json.dumps(["하나뿐"]))
"""

CRASH = """\
    import sys
    sys.stderr.write("boom: missing language asset\\n")
    sys.exit(1)
"""

SLEEPY = """\
    import time
    time.sleep(5)
"""


class TestAppleTranslator:
    async def test_translates_batch_in_order(self, tmp_path):
        tr = AppleTranslator(argv=_fake_bin(tmp_path, ECHO_KO))
        out = await tr.translate_batch(["Hello", "World"])
        assert out == ["KO:Hello", "KO:World"]

    async def test_length_mismatch_raises(self, tmp_path):
        tr = AppleTranslator(argv=_fake_bin(tmp_path, WRONG_LEN))
        with pytest.raises(TranslationError, match="count mismatch"):
            await tr.translate_batch(["a", "b"])

    async def test_nonzero_exit_raises_with_stderr(self, tmp_path):
        tr = AppleTranslator(argv=_fake_bin(tmp_path, CRASH))
        with pytest.raises(TranslationError, match="missing language asset"):
            await tr.translate_batch(["a"])

    async def test_empty_input_short_circuits(self, tmp_path):
        tr = AppleTranslator(argv=[sys.executable, "/nonexistent.py"])
        assert await tr.translate_batch([]) == []

    async def test_timeout_kills_and_raises(self, tmp_path):
        tr = AppleTranslator(argv=_fake_bin(tmp_path, SLEEPY), timeout=0.3)
        with pytest.raises(TranslationError, match="시간 초과"):
            await tr.translate_batch(["a"])
# === ANCHOR: TEST_TRANSLATE_APPLE_END ===


class TestWiring:
    def test_create_translator_apple(self):
        from apps.server.domain.video_captions.translate_cli import create_translator
        assert type(create_translator(provider="apple")).__name__ == "AppleTranslator"

    def test_engine_listed(self, monkeypatch):
        from apps.server.domain.video_captions import translate_cli
        monkeypatch.setattr(translate_cli, "apple_mt_available", lambda: True)
        engines = translate_cli.list_translate_engines()
        apple = [e for e in engines if e["value"] == "apple"]
        assert apple and apple[0]["available"] is True
