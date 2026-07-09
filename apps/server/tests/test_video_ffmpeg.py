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


@pytest.fixture(autouse=True)
def _clear_encoder_cache():
    ff._encoder_cache.clear()
    yield
    ff._encoder_cache.clear()


@pytest.fixture(autouse=True)
def _clear_proc_registry():
    ff._ACTIVE.clear()
    ff._KILLED.clear()
    yield
    ff._ACTIVE.clear()
    ff._KILLED.clear()


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
    monkeypatch.setenv("YESON_BURN_ENCODER", "libx264")
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
    # P0: 인코딩 파라미터 명시 (암묵적 medium 재인코딩 금지)
    cmd = call["cmd"]
    for flag in ("-c:v", "libx264", "-preset", "veryfast", "-crf", "23"):
        assert flag in cmd


def test_detect_burn_encoder_picks_first_probe_success(monkeypatch):
    monkeypatch.delenv("YESON_BURN_ENCODER", raising=False)
    monkeypatch.setattr(ff, "_HW_CANDIDATES", ("h264_nvenc", "h264_amf"))
    monkeypatch.setattr(ff, "_probe_encoder", lambda f, e: e == "h264_amf")
    assert ff.detect_burn_encoder("ffmpeg", True) == "h264_amf"


def test_detect_burn_encoder_all_probes_fail_falls_back(monkeypatch):
    monkeypatch.delenv("YESON_BURN_ENCODER", raising=False)
    monkeypatch.setattr(ff, "_HW_CANDIDATES", ("h264_nvenc",))
    monkeypatch.setattr(ff, "_probe_encoder", lambda f, e: False)
    assert ff.detect_burn_encoder("ffmpeg", True) == "libx264"


def test_detect_burn_encoder_env_override_skips_probe(monkeypatch):
    def boom(*a):
        raise AssertionError("probe must not run with env override")

    monkeypatch.setattr(ff, "_probe_encoder", boom)
    monkeypatch.setenv("YESON_BURN_ENCODER", "h264_nvenc")
    assert ff.detect_burn_encoder("ffmpeg", True) == "h264_nvenc"
    monkeypatch.setenv("YESON_BURN_ENCODER", "definitely_not_an_encoder")
    assert ff.detect_burn_encoder("ffmpeg", True) == "libx264"


def test_detect_burn_encoder_env_override_wins_even_when_gpu_off(monkeypatch):
    """운영자가 YESON_BURN_ENCODER를 명시하면 GPU 토글이 꺼져 있어도 그 값을 쓴다
    (명시적 오버라이드가 use_gpu보다 우선)."""
    def boom(*a):
        raise AssertionError("probe must not run with env override")

    monkeypatch.setattr(ff, "_probe_encoder", boom)
    monkeypatch.setenv("YESON_BURN_ENCODER", "h264_nvenc")
    assert ff.detect_burn_encoder("ffmpeg", False) == "h264_nvenc"


def test_detect_burn_encoder_use_gpu_false_skips_probe_returns_cpu(monkeypatch):
    """GPU 토글이 꺼져 있으면(use_gpu=False) HW 후보를 아예 프로브하지 않고
    즉시 libx264를 반환한다 — 이전엔 토글과 무관하게 항상 프로브했다(버그)."""
    monkeypatch.delenv("YESON_BURN_ENCODER", raising=False)

    def boom(*a):
        raise AssertionError("probe must not run when use_gpu=False")

    monkeypatch.setattr(ff, "_probe_encoder", boom)
    assert ff.detect_burn_encoder("ffmpeg", False) == "libx264"


def test_detect_burn_encoder_result_is_cached(monkeypatch):
    monkeypatch.delenv("YESON_BURN_ENCODER", raising=False)
    monkeypatch.setattr(ff, "_HW_CANDIDATES", ("h264_nvenc",))
    probes: list[str] = []
    monkeypatch.setattr(ff, "_probe_encoder",
                        lambda f, e: probes.append(e) or True)
    assert ff.detect_burn_encoder("ffmpeg", True) == "h264_nvenc"
    assert ff.detect_burn_encoder("ffmpeg", True) == "h264_nvenc"
    assert probes == ["h264_nvenc"]


def test_detect_burn_encoder_cache_keyed_by_use_gpu(monkeypatch):
    """toggling off→on→off는 off 쪽 프로브 캐시에 의존하지 않는다 — off는 매번
    프로브 없이 즉시 libx264를 반환하고, on 쪽 캐시만 (ffmpeg, True)로 쌓인다."""
    monkeypatch.delenv("YESON_BURN_ENCODER", raising=False)
    monkeypatch.setattr(ff, "_HW_CANDIDATES", ("h264_nvenc",))
    probes: list[str] = []
    monkeypatch.setattr(ff, "_probe_encoder",
                        lambda f, e: probes.append(e) or True)

    assert ff.detect_burn_encoder("ffmpeg", False) == "libx264"
    assert ff.detect_burn_encoder("ffmpeg", True) == "h264_nvenc"
    assert ff.detect_burn_encoder("ffmpeg", False) == "libx264"
    assert probes == ["h264_nvenc"]  # off 경로는 절대 프로브하지 않았다
    assert ff._encoder_cache == {("ffmpeg", True): "h264_nvenc"}


def test_burn_gpu_failure_falls_back_to_libx264(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("YESON_BURN_ENCODER", raising=False)
    monkeypatch.setattr(ff, "detect_burn_encoder", lambda f, use_gpu: "h264_nvenc")
    cmds: list[list[str]] = []

    def fake_run(cmd, **kw):
        cmds.append(cmd)
        return _Result(1, "nvenc boom") if "h264_nvenc" in cmd else _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    ff.burn_subtitles("ffmpeg", tmp_path / "src.mp4", srt, tmp_path / "out.mp4",
                      "Alignment=2")
    assert "h264_nvenc" in cmds[0]
    assert "libx264" in cmds[1]
    # 실패한 GPU 인코더는 이후 작업에서도 재시도하지 않도록 캐시를 CPU로 고정
    assert ff._encoder_cache[("ffmpeg", True)] == "libx264"


def test_burn_subtitles_use_gpu_false_uses_libx264_without_probe(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("YESON_BURN_ENCODER", raising=False)

    def boom(*a):
        raise AssertionError("probe must not run when GPU toggle is off")

    monkeypatch.setattr(ff, "_probe_encoder", boom)
    calls: list[dict] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: calls.append({"cmd": cmd, "kwargs": kw}) or _Result())
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    ff.burn_subtitles("ffmpeg", tmp_path / "src.mp4", srt, tmp_path / "out.mp4",
                      "Alignment=2", use_gpu=False)
    assert "libx264" in calls[0]["cmd"]
    assert calls[0]["cmd"].count("-c:v") == 1  # 재시도 없이 단 한 번의 호출


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
    monkeypatch.setenv("YESON_BURN_ENCODER", "libx264")
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


def test_burn_progress_exception_kills_ffmpeg(monkeypatch, tmp_path: Path):
    """progress_cb가 예외(취소 신호)를 던지면 ffmpeg 프로세스를 즉시 kill해야 한다 —
    그대로 두면 취소 후에도 인코딩이 끝까지 돌며 CPU/GPU를 태운다."""
    monkeypatch.setenv("YESON_BURN_ENCODER", "libx264")
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")

    killed = {"v": False}

    class KillTrackingPopen(FakePopen):
        def kill(self):
            killed["v"] = True

    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, **kw: KillTrackingPopen(cmd, **kw))

    class Cancel(Exception):
        pass

    def raising_cb(seconds: float) -> None:
        raise Cancel

    with pytest.raises(Cancel):
        ff.burn_subtitles("ffmpeg", tmp_path / "src.mp4", srt, tmp_path / "out.mp4",
                          "Alignment=2,MarginV=40,Fontsize=18", progress_cb=raising_cb)
    assert killed["v"] is True


class _KillTrackingProc:
    """subprocess.Popen 대역 — kill_active가 잡고 즉시 kill할 수 있도록 kill()을
    추적한다. wait()는 kill 후 실제 ffmpeg처럼 nonzero(음수) 종료 코드를 낸다."""

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.killed = False

    def kill(self):
        self.killed = True

    def wait(self):
        return -9 if self.killed else 0


def test_kill_active_kills_registered_proc(monkeypatch):
    """kill_active(key)는 등록된 프로세스를 즉시 kill/wait하고, 등록된 게 있었으면
    True를 반환한다."""
    proc = _KillTrackingProc([])
    ff.register_proc("job-1", proc)

    assert ff.kill_active("job-1") is True
    assert proc.killed is True
    # 이미 pop됐으므로 재호출은 아무 것도 못 찾고 False
    assert ff.kill_active("job-1") is False


def test_kill_active_noop_when_nothing_registered():
    assert ff.kill_active("no-such-job") is False


class _FakeStdoutOneLine:
    def __init__(self, lines):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


class _KilledDuringBurnPopen:
    """progress_cb 도중 kill_active(proc_key)가 호출되는 상황을 재현하는 Popen 대역
    — 실제로는 kill()이 프로세스를 죽여 다음 wait()가 nonzero를 반환한다."""

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.stdout = _FakeStdoutOneLine(["out_time_ms=1000000\n"])
        self.killed = False

    def kill(self):
        self.killed = True

    def wait(self):
        return -9 if self.killed else 0


def test_burn_subtitles_skips_cpu_retry_when_gpu_run_was_killed(monkeypatch, tmp_path):
    """kill_active로 죽은 GPU 인코딩이 FfmpegError로 표면화돼도 libx264로 재시도하면
    안 된다 — 취소 후에도 새 CPU 인코딩이 시작되는 낭비를 막기 위함."""
    monkeypatch.delenv("YESON_BURN_ENCODER", raising=False)
    monkeypatch.setattr(ff, "detect_burn_encoder", lambda f, use_gpu: "h264_nvenc")
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")

    proc_key = "job-killed"
    calls: list[list[str]] = []

    def fake_popen(cmd, **kw):
        calls.append(cmd)
        return _KilledDuringBurnPopen(cmd, **kw)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    def progress_cb(seconds: float) -> None:
        # 취소 엔드포인트가 cancel_job_task를 통해 하는 일을 재현: 활성 프로세스를
        # 즉시 kill한다(진행률 콜백 자체는 예외를 던지지 않음).
        ff.kill_active(proc_key)

    with pytest.raises(ff.FfmpegError):
        ff.burn_subtitles("ffmpeg", tmp_path / "src.mp4", srt, tmp_path / "out.mp4",
                          "Alignment=2", progress_cb=progress_cb, proc_key=proc_key)

    assert len(calls) == 1  # libx264 재시도가 없었다 — 단 한 번의 ffmpeg 호출뿐
    assert "h264_nvenc" in calls[0]


class _KillDuringCommunicateProc:
    """subprocess.Popen 대역 — communicate() 호출 시점에 kill_active(key)가 등록된
    프로세스를 즉시 죽일 수 있는지 검증한다(ensure_preview의 비-mp4 트랜스코드
    단계 취소 경로)."""

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.returncode = 0

    def communicate(self):
        # 취소 엔드포인트(cancel_job_task)가 하는 일을 재현: 등록된 프로세스를
        # 즉시 kill한다.
        ff.kill_active("job-preview")
        return "", "killed"

    def kill(self):
        self.returncode = -9

    def wait(self):
        return self.returncode


def test_ensure_preview_registers_proc_key_and_is_killable(monkeypatch, tmp_path: Path):
    """ensure_preview(비-mp4 소스)는 extract_audio와 동일하게 proc_key로 레지스트리에
    등록돼 kill_active로 즉시 kill 가능해야 한다 — 회귀 시 이 테스트는 Popen 경로를
    타지 않아(레지스트리 미등록) FfmpegError 대신 다른 예외/성공으로 새며 실패한다."""
    calls: list[list[str]] = []

    def fake_popen(cmd, **kw):
        calls.append(cmd)
        return _KillDuringCommunicateProc(cmd, **kw)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(ff.FfmpegError):
        ff.ensure_preview("ffmpeg", tmp_path / "src.mov", tmp_path / "preview.mp4",
                          proc_key="job-preview")

    assert calls  # Popen(proc_key 등록 가능한) 경로를 실제로 탔다
    assert "job-preview" not in ff._ACTIVE  # kill 후 레지스트리 누수 없음


def test_ensure_preview_mp4_passthrough_does_not_touch_registry(tmp_path: Path):
    """mp4 소스는 트랜스코드/Popen을 타지 않으므로 레지스트리에 손대지 않는다."""
    src = tmp_path / "src.mp4"
    result = ff.ensure_preview("ffmpeg", src, tmp_path / "preview.mp4",
                               proc_key="job-passthrough")
    assert result == src
    assert "job-passthrough" not in ff._ACTIVE
