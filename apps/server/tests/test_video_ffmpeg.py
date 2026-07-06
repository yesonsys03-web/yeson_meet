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


def test_wav_duration_seconds(tmp_path: Path):
    import wave

    path = tmp_path / "audio.wav"
    framerate = 16000
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * framerate)  # 1초 분량 무음
    assert ff.wav_duration_seconds(path) == pytest.approx(1.0)


class _FakeStdout:
    """Popen.stdout 대역 — 라인 이터러블 + close()."""

    def __init__(self, lines: list[str]):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


class FakePopen:
    """subprocess.Popen 대역 — stdout 라인 이터러블 + wait()."""

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.stdout = _FakeStdout([
            "frame=1\n",
            "out_time_ms=2500000\n",
            "out_time_ms=5000000\n",
            "progress=end\n",
        ])

    def wait(self):
        return 0


def test_burn_progress_parses_out_time_ms(monkeypatch, tmp_path: Path):
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")

    captured_cmd: list[str] = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return FakePopen(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    seen: list[float] = []
    ff.burn_subtitles("ffmpeg", tmp_path / "src.mp4", srt, tmp_path / "out.mp4",
                      "Alignment=2,MarginV=40,Fontsize=18", progress_cb=seen.append)

    assert seen == [2.5, 5.0]
    assert "-progress" in captured_cmd
    assert "pipe:1" in captured_cmd
    assert "-nostats" in captured_cmd
