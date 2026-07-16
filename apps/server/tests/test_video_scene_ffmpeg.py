from __future__ import annotations

import subprocess
from pathlib import Path

from apps.server.domain.video_captions import ffmpeg as ff


class _Result:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def test_extract_frames_builds_fps_command(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Result())[1])
    ff.extract_frames("ffmpeg", tmp_path / "in.mp4", tmp_path / "frames",
                      interval_s=1.0)
    cmd = calls[0]
    assert cmd[0] == "ffmpeg"
    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert vf == "fps=1/1.0"
    assert cmd[-1].endswith("frame_%05d.png")


def test_extract_thumbnails_scales_by_height(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Result())[1])
    ff.extract_thumbnails("ffmpeg", tmp_path / "in.mp4", tmp_path / "th",
                          interval_s=2.0, height=90)
    vf = calls[0][calls[0].index("-vf") + 1]
    assert "fps=1/2.0" in vf
    assert "scale=-2:90" in vf
    assert calls[0][-1].endswith("thumb_%05d.jpg")


def test_cut_segment_reencodes_with_ss_to(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Result())[1])
    ff.cut_segment("ffmpeg", tmp_path / "in.mp4", tmp_path / "out.mp4",
                   start_ms=3000, end_ms=7500)
    cmd = calls[0]
    # 재인코딩(정확) — -c copy 금지, libx264 + aac
    assert "-c" not in cmd or "copy" not in cmd
    assert "libx264" in cmd
    assert "aac" in cmd
    assert cmd[cmd.index("-ss") + 1] == "3.000"
    assert cmd[cmd.index("-to") + 1] == "7.500"
    # -ss가 -i 앞이면 입력 시킹(빠름)이지만 재인코딩이라 프레임 정확
    assert cmd.index("-ss") < cmd.index("-i")
