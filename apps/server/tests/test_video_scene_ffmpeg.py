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


def test_cut_segment_reencodes_with_ss_and_output_t(monkeypatch, tmp_path: Path):
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
    # -ss가 -i 앞이면 입력 시킹(빠름)이지만 재인코딩이라 프레임 정확
    assert cmd.index("-ss") < cmd.index("-i")
    # 회귀(실기): 끝은 입력측 -to가 아니라 출력측 -t(길이) — 입력 -to는 디먹서
    # 패킷 단위로 끊어 B-프레임 재정렬 시 다음 세그먼트 첫 프레임(들)이 꼬리에
    # 섞인다(실측 7/16 클립). 출력 -t는 [start, end) 반열림을 정확히 지킨다.
    assert "-to" not in cmd
    assert cmd[cmd.index("-t") + 1] == "4.500"
    assert cmd.index("-t") > cmd.index("-i")


def test_cut_segment_half_open_frame_count(tmp_path: Path):
    """실 ffmpeg 통합: 24fps 합성영상을 [500ms, 1500ms)로 자르면 정확히 24프레임
    — 경계 프레임(t=1500ms)이 포함되면 안 된다(다른 시퀀스 프레임 섞임 회귀)."""
    import json as _json
    import shutil as _shutil

    import pytest
    ffmpeg = _shutil.which("ffmpeg")
    ffprobe = _shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe 없음")
    src = tmp_path / "src.mp4"
    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i",
                    "testsrc=duration=2:size=128x96:rate=24",
                    "-c:v", "libx264", "-preset", "veryfast", str(src)],
                   check=True, capture_output=True)
    out = tmp_path / "out.mp4"
    ff.cut_segment(ffmpeg, src, out, start_ms=500, end_ms=1500)
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "json", str(out)],
        check=True, capture_output=True, text=True)
    n = int(_json.loads(probe.stdout)["streams"][0]["nb_read_frames"])
    assert n == 24, f"[500,1500)@24fps는 24프레임이어야 하는데 {n}프레임"
