# === ANCHOR: TEST_TRANSCRIBE_APPLE_START ===
from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

import pytest

from apps.server.domain.video_captions.transcribe import StaleRunCancelled
from apps.server.domain.video_captions.transcribe_apple import transcribe_audio_apple


def _fake_bin(tmp_path, body: str) -> list[str]:
    script = tmp_path / "fake_apple.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


TOKENS = """\
    import json
    events = [
        {"type": "status", "state": "ready"},
        {"type": "token", "t0": 0.0, "t1": 0.4, "text": "Hello"},
        {"type": "token", "t0": 0.5, "t1": 0.9, "text": "world."},
        {"type": "progress", "frac": 0.5},
        {"type": "token", "t0": 7.0, "t1": 7.4, "text": "Next"},
        {"type": "token", "t0": 7.5, "t1": 7.9, "text": "cue"},
        {"type": "progress", "frac": 1.0},
        {"type": "done"},
    ]
    for e in events:
        print(json.dumps(e))
"""

FAILS = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "error", "reason": "missing_stt_asset"}))
    sys.exit(1)
"""

STATUS_ERROR_HANGS = """\
    import json, sys, time
    print(json.dumps({"type": "status", "state": "error", "reason": "missing_stt_asset"}))
    sys.stdout.flush()
    time.sleep(10)
"""

MALFORMED_TOKEN = """\
    import json
    events = [
        {"type": "status", "state": "ready"},
        {"type": "token", "t0": 0.0, "t1": 0.4, "text": "Hello"},
        {"type": "token", "t0": 1.0, "text": "bad"},
        {"type": "token", "t0": 2.0, "t1": 2.4, "text": "world."},
        {"type": "done"},
    ]
    for e in events:
        print(json.dumps(e))
"""


class TestTranscribeAudioApple:
    def test_tokens_become_cues_via_words_to_cues(self, tmp_path):
        cues = transcribe_audio_apple(Path("unused.wav"), None,
                                      argv=_fake_bin(tmp_path, TOKENS))
        # 0.9→7.0 사이 6초 초과 갭 → words_to_cues가 두 큐로 분할
        assert len(cues) == 2
        assert cues[0].text == "Hello world."
        assert cues[0].start_ms == 0 and cues[0].end_ms == 900
        assert cues[1].seq == 2

    def test_progress_callback_invoked(self, tmp_path):
        seen: list[float] = []
        transcribe_audio_apple(Path("unused.wav"), seen.append,
                               argv=_fake_bin(tmp_path, TOKENS))
        assert seen == [0.5, 1.0]

    def test_stale_cancel_propagates_and_kills_proc(self, tmp_path):
        def cancel(_frac: float) -> None:
            raise StaleRunCancelled()
        with pytest.raises(StaleRunCancelled):
            transcribe_audio_apple(Path("unused.wav"), cancel,
                                   argv=_fake_bin(tmp_path, TOKENS))

    def test_binary_error_raises_runtime_error(self, tmp_path):
        with pytest.raises(RuntimeError, match="missing_stt_asset"):
            transcribe_audio_apple(Path("unused.wav"), None,
                                   argv=_fake_bin(tmp_path, FAILS))

    def test_status_error_with_open_stdout_raises_promptly(self, tmp_path):
        start = time.monotonic()
        with pytest.raises(RuntimeError, match="missing_stt_asset"):
            transcribe_audio_apple(Path("unused.wav"), None,
                                   argv=_fake_bin(tmp_path, STATUS_ERROR_HANGS))
        elapsed = time.monotonic() - start
        assert elapsed < 5.0

    def test_malformed_token_skipped(self, tmp_path):
        cues = transcribe_audio_apple(Path("unused.wav"), None,
                                      argv=_fake_bin(tmp_path, MALFORMED_TOKEN))
        assert len(cues) == 1
        assert cues[0].text == "Hello world."


class TestWiring:
    def test_transcribe_audio_routes_to_apple(self, tmp_path, monkeypatch):
        from apps.server.domain.video_captions import transcribe, transcribe_apple
        called = {}
        monkeypatch.setattr(transcribe_apple, "transcribe_audio_apple",
                            lambda path, cb, argv=None: called.setdefault("hit", []) or [])
        assert transcribe.transcribe_audio(Path("x.wav"), "apple") == []
        assert "hit" in called

    def test_require_model_rejects_apple_when_unavailable(self, monkeypatch):
        from fastapi import HTTPException
        from apps.server.api.v1 import video_jobs
        monkeypatch.setattr(video_jobs, "apple_stt_available", lambda: False)
        with pytest.raises(HTTPException) as exc:
            video_jobs._require_model("apple")
        assert exc.value.status_code == 409
# === ANCHOR: TEST_TRANSCRIBE_APPLE_END ===
