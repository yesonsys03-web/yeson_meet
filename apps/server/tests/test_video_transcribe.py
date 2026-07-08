from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.server.domain.video_captions import transcribe as tr


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    yield


def _installed(name: str, tmp_path: Path):
    from apps.server.domain.video_captions.whisper_models import model_dir
    d = model_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.bin").write_bytes(b"x")


def test_transcribe_maps_whisper_segments_to_subsegments(monkeypatch, tmp_path):
    _installed("small", tmp_path)

    class FakeModel:
        def transcribe(self, path, **kwargs):
            assert kwargs["language"] == "en"
            assert kwargs["vad_filter"] is True
            # 용어사전 initial_prompt 주입 금지 회귀 가드 — base 모델 30초 윈도우
            # 유실 원인(2026-07-08)
            assert "initial_prompt" not in kwargs
            segs = [SimpleNamespace(start=0.0, end=1.5, text=" Hello there "),
                    SimpleNamespace(start=2.0, end=4.25, text="Second line")]
            return iter(segs), SimpleNamespace(language="en")

    monkeypatch.setattr(tr, "_load_model", lambda name, *a: FakeModel())
    out = tr.transcribe_audio(tmp_path / "audio.wav", "small")
    assert [(s.seq, s.start_ms, s.end_ms, s.text) for s in out] == [
        (1, 0, 1500, "Hello there"), (2, 2000, 4250, "Second line"),
    ]


def test_transcribe_reports_progress_via_callback(monkeypatch, tmp_path):
    _installed("small", tmp_path)

    class FakeModel:
        def transcribe(self, path, **kwargs):
            segs = [SimpleNamespace(start=0.0, end=1.5, text="Hello there"),
                    SimpleNamespace(start=2.0, end=4.25, text="Second line")]
            return iter(segs), SimpleNamespace(language="en", duration=10.0)

    monkeypatch.setattr(tr, "_load_model", lambda name, *a: FakeModel())
    seen: list[float] = []
    tr.transcribe_audio(tmp_path / "audio.wav", "small", seen.append)
    assert seen == [0.15, 0.425]


def test_transcribe_requires_downloaded_model(tmp_path):
    with pytest.raises(tr.ModelNotDownloadedError):
        tr.transcribe_audio(tmp_path / "audio.wav", "small")


def _w(start: float, end: float, word: str):
    return SimpleNamespace(start=start, end=end, word=word)


def test_words_to_cues_prefers_punctuation_boundary():
    # 10s of words; word ending at 4.0s ends with "rig," — a mid-buffer comma.
    # Overflow (max_chars) is triggered by words after 4s with no further
    # punctuation, so the split should land on the comma at 4.0s.
    words = [
        _w(0.0, 0.5, " We"),
        _w(0.5, 1.0, " assembled"),
        _w(1.0, 1.8, " the"),
        _w(1.8, 3.2, " camera"),
        _w(3.2, 4.0, " rig,"),
        _w(4.0, 5.5, " then"),
        _w(5.5, 7.0, " calibrated"),
        _w(7.0, 8.5, " every"),
        _w(8.5, 10.0, " lens"),
    ]
    cues = tr.words_to_cues(words, max_seconds=100.0, max_chars=30)
    assert cues[0].end_ms == 4000
    assert cues[0].text == "We assembled the camera rig,"


def test_words_to_cues_hard_cuts_on_max_seconds_without_punctuation():
    # 8 continuous seconds, no punctuation anywhere — must hard-cut within
    # max_seconds and carry the remainder into a second cue.
    words = [_w(float(i), float(i + 1), f"word{i}") for i in range(8)]
    cues = tr.words_to_cues(words, max_seconds=6.0, max_chars=1000)
    assert len(cues) >= 2
    for cue in cues:
        assert (cue.end_ms - cue.start_ms) / 1000.0 <= 6.0
    # nothing lost
    assert " ".join(c.text for c in cues) == " ".join(f"word{i}" for i in range(8))


def test_words_to_cues_cuts_on_max_chars():
    # short time span but long text — must split before exceeding max_chars.
    words = [_w(i * 0.1, i * 0.1 + 0.1, f"abcdefghi{i}") for i in range(10)]
    cues = tr.words_to_cues(words, max_seconds=1000.0, max_chars=90)
    assert len(cues) >= 2
    for cue in cues:
        assert len(cue.text) <= 90


def test_words_to_cues_reassigns_seq_and_skips_blank_words():
    words = [
        _w(0.0, 1.0, " Hello"),
        _w(1.0, 1.2, "   "),
        _w(1.2, 2.0, " world"),
    ]
    cues = tr.words_to_cues(words, max_seconds=100.0, max_chars=1000)
    assert [c.seq for c in cues] == list(range(1, len(cues) + 1))
    assert cues[0].text == "Hello world"


def test_transcribe_splits_long_segments_via_word_timestamps(monkeypatch, tmp_path):
    _installed("small", tmp_path)

    def _words(n_start: int, n_end: int):
        return [_w(float(i), float(i + 1), f"tok{i}") for i in range(n_start, n_end)]

    class FakeModel:
        def transcribe(self, path, **kwargs):
            assert kwargs["word_timestamps"] is True
            seg1 = SimpleNamespace(start=0.0, end=15.0, text="a" * 15,
                                   words=_words(0, 15))
            seg2 = SimpleNamespace(start=15.0, end=29.0, text="b" * 14,
                                   words=_words(15, 29))
            return iter([seg1, seg2]), SimpleNamespace(language="en", duration=29.0)

    monkeypatch.setattr(tr, "_load_model", lambda name, *a: FakeModel())
    out = tr.transcribe_audio(tmp_path / "audio.wav", "small")
    assert len(out) > 1
    for cue in out:
        assert (cue.end_ms - cue.start_ms) / 1000.0 <= tr.MAX_CUE_SECONDS
    assert [c.seq for c in out] == list(range(1, len(out) + 1))
