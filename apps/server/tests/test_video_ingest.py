from __future__ import annotations

import io
from pathlib import Path

import pytest

from apps.server.domain.video_captions import ingest as ig


def test_download_youtube_uses_h264_mp4_format(monkeypatch, tmp_path: Path):
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            captured["url"] = url
            out = tmp_path / "source.mp4"
            out.write_bytes(b"video")
            return {"title": "My Video", "ext": "mp4"}

    monkeypatch.setattr(ig, "_ytdl", lambda opts: FakeYDL(opts))
    path, title = ig.download_youtube("https://youtu.be/x", tmp_path)
    assert title == "My Video"
    assert path == tmp_path / "source.mp4"
    assert "avc1" in captured["opts"]["format"]
    assert captured["opts"]["noplaylist"] is True


def test_download_youtube_wraps_errors(monkeypatch, tmp_path: Path):
    class Boom:
        def __init__(self, opts): ...
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=True):
            raise RuntimeError("HTTP Error 403")

    monkeypatch.setattr(ig, "_ytdl", lambda opts: Boom(opts))
    with pytest.raises(ig.IngestError, match="yt-dlp"):
        ig.download_youtube("https://youtu.be/x", tmp_path)


async def test_save_upload_streams_chunks(tmp_path: Path):
    class FakeUpload:
        def __init__(self, data: bytes):
            self._buf = io.BytesIO(data)

        async def read(self, n: int) -> bytes:
            return self._buf.read(n)

    dest = tmp_path / "source.mp4"
    await ig.save_upload(FakeUpload(b"a" * (2 * 1024 * 1024 + 5)), dest)
    assert dest.stat().st_size == 2 * 1024 * 1024 + 5
