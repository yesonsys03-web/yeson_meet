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
            assert "cleanup" in kwargs["initial_prompt"]
            segs = [SimpleNamespace(start=0.0, end=1.5, text=" Hello there "),
                    SimpleNamespace(start=2.0, end=4.25, text="Second line")]
            return iter(segs), SimpleNamespace(language="en")

    monkeypatch.setattr(tr, "_load_model", lambda name: FakeModel())
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

    monkeypatch.setattr(tr, "_load_model", lambda name: FakeModel())
    seen: list[float] = []
    tr.transcribe_audio(tmp_path / "audio.wav", "small", seen.append)
    assert seen == [0.15, 0.425]


def test_transcribe_requires_downloaded_model(tmp_path):
    with pytest.raises(tr.ModelNotDownloadedError):
        tr.transcribe_audio(tmp_path / "audio.wav", "small")


def test_glossary_initial_prompt_contains_terms():
    prompt = tr.glossary_initial_prompt(max_terms=5)
    assert prompt.startswith("Animation production meeting.")
    assert len(prompt) < 600
