from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from apps.server.domain.video_captions import ffmpeg as ff


class _Result:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def test_locate_ffmpeg_env_override(monkeypatch, tmp_path: Path):
    fake = tmp_path / "ffmpeg"
    fake.write_text("")
    monkeypatch.setenv("YESON_FFMPEG_BIN", str(fake))
    assert ff.locate_ffmpeg() == str(fake)


def test_extract_audio_builds_16k_mono_wav_command(monkeypatch, tmp_path: Path):
    calls: list[dict] = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ff.extract_audio("ffmpeg", tmp_path / "in.mp4", tmp_path / "audio.wav")
    cmd = calls[0]["cmd"]
    assert cmd[:2] == ["ffmpeg", "-y"]
    for flag in (["-ac", "1"], ["-ar", "16000"], ["-vn"]):
        assert flag[0] in cmd
    # Windows cp949 교훈: text 모드는 반드시 utf-8 지정
    assert calls[0]["kwargs"]["encoding"] == "utf-8"


def test_burn_runs_with_relative_srt_and_cwd(monkeypatch, tmp_path: Path):
    calls: list[dict] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: calls.append({"cmd": cmd, "kwargs": kw}) or _Result())
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    ff.burn_subtitles("ffmpeg", tmp_path / "src.mp4", srt, tmp_path / "out.mp4",
                      "Alignment=2,MarginV=40,Fontsize=18")
    call = calls[0]
    vf = call["cmd"][call["cmd"].index("-vf") + 1]
    assert vf == "subtitles=subs.srt:force_style='Alignment=2,MarginV=40,Fontsize=18'"
    assert call["kwargs"]["cwd"] == str(tmp_path)


def test_nonzero_returncode_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result(1, "boom"))
    with pytest.raises(ff.FfmpegError):
        ff.extract_audio("ffmpeg", tmp_path / "in.mp4", tmp_path / "audio.wav")
