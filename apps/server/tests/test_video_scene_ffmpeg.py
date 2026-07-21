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


def test_extract_frames_crops_to_ocr_region(monkeypatch, tmp_path: Path):
    """OCR 영역(비율)을 주면 ffmpeg 단계에서 잘라낸다 — 판독 입력이 작아져 빠르고,
    쇼마다 다른 슬레이트 위치를 코드 가정 없이 처리한다. 비율이라 해상도 무관."""
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Result())[1])
    ff.extract_frames("ffmpeg", tmp_path / "in.mp4", tmp_path / "f",
                      interval_s=2.0, region=(0.02, 0.03, 0.5, 0.08))
    vf = calls[0][calls[0].index("-vf") + 1]
    assert vf == ("fps=1/2.0,"
                  "crop=in_w*0.5000:in_h*0.0800:in_w*0.0200:in_h*0.0300")


def test_extract_frames_without_region_is_unchanged(monkeypatch, tmp_path: Path):
    """영역 미지정은 기존 동작 그대로(전체 프레임) — 하위 호환."""
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Result())[1])
    ff.extract_frames("ffmpeg", tmp_path / "in.mp4", tmp_path / "f", interval_s=2.0)
    assert calls[0][calls[0].index("-vf") + 1] == "fps=1/2.0"


def test_extract_frame_crops_to_ocr_region(monkeypatch, tmp_path: Path):
    """정밀화용 단일 프레임도 같은 영역을 쓴다(스캔과 판독 조건이 같아야 한다)."""
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Result())[1])
    ff.extract_frame("ffmpeg", tmp_path / "in.mp4", 4968, tmp_path / "r.png",
                     region=(0.0, 0.0, 1.0, 0.2))
    vf = calls[0][calls[0].index("-vf") + 1]
    assert vf == "crop=in_w*1.0000:in_h*0.2000:in_w*0.0000:in_h*0.0000"


def test_extract_thumbnail_at_seeks_and_scales(monkeypatch, tmp_path: Path):
    """경계 썸네일: 임의 시각 1프레임을 필름스트립 높이로 축소해 저장.
    -ss는 -i 앞(cut_segment/extract_frame와 같은 시간축) — 그래야 썸네일이
    실제로 잘려 나올 클립의 첫 프레임과 일치한다."""
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Result())[1])
    ff.extract_thumbnail_at("ffmpeg", tmp_path / "in.mp4", 4968,
                            tmp_path / "b.jpg", height=90)
    cmd = calls[0]
    assert cmd[cmd.index("-ss") + 1] == "4.968"
    assert cmd.index("-ss") < cmd.index("-i")
    assert cmd[cmd.index("-vf") + 1] == "scale=-2:90"
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert cmd[-1].endswith("b.jpg")


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
    # 회귀(실기, QuickTime 전용 검정): 입력 시킹 + B-프레임 재정렬로 mp4에 빈
    # 편집 리스트(media time -1)가 생겨 QuickTime이 앞에 검정 프레임을 렌더한다
    # (원본엔 없는데도). setpts로 첫 프레임 PTS를 0으로 리셋 + B-프레임 제거(-bf 0)로
    # 빈 편집 자체를 없앤다.
    vf = cmd[cmd.index("-vf") + 1]
    assert "setpts=PTS-STARTPTS" in vf
    assert cmd[cmd.index("-af") + 1] == "asetpts=PTS-STARTPTS"
    assert cmd[cmd.index("-bf") + 1] == "0"


def test_cut_segment_uses_frames_v_when_fps_given(monkeypatch, tmp_path: Path):
    """회귀(실기, 다음 세그 첫 프레임이 꼬리에 1개 섞임): 정밀화 경계는 실제 전환
    프레임 PTS보다 0~<1프레임 위로 수렴하고, 그 편차가 시작·끝에서 달라 -t(길이)로
    끊으면 다음 세그 첫 프레임이 꼬리에 랜덤하게 섞인다. fps를 주면 끝을 -t 대신
    정확한 프레임 수 -frames:v로 끊는다 — -ss는 그 시각 이하 프레임으로 스냅다운해
    첫 프레임이 정확하고, 세그 프레임 수는 정수라 (end-start)×fps 반올림으로 복원된다."""
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Result())[1])
    # 23.976fps, [520312, 597984) → round(77.672*23.976)=1862 프레임.
    ff.cut_segment("ffmpeg", tmp_path / "in.mp4", tmp_path / "out.mp4",
                   start_ms=520312, end_ms=597984, fps=23.976)
    cmd = calls[0]
    assert cmd[cmd.index("-ss") + 1] == "520.312"
    assert cmd[cmd.index("-frames:v") + 1] == "1862"
    # 끝은 프레임수로 끊는다 — 길이 -t도 디먹서 -to도 쓰지 않는다.
    assert "-t" not in cmd
    assert "-to" not in cmd


def test_cut_segment_falls_back_to_output_t_without_fps(monkeypatch,
                                                        tmp_path: Path):
    """fps를 못 구하면 기존 출력측 -t(길이)로 폴백 — 하위 호환."""
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Result())[1])
    ff.cut_segment("ffmpeg", tmp_path / "in.mp4", tmp_path / "out.mp4",
                   start_ms=3000, end_ms=7500)
    cmd = calls[0]
    assert "-frames:v" not in cmd
    assert cmd[cmd.index("-t") + 1] == "4.500"
    assert "-to" not in cmd


def test_video_fps_measures_exact_from_frame_pts(monkeypatch, tmp_path: Path):
    """회귀(실기, 꼬리 +1프레임): 표시 fps 23.98(반올림)로 N을 계산하면 긴 클립에서
    누적오차로 N이 +1 틀려 다음 세그 첫 프레임이 섞인다. showinfo로 실제 프레임 PTS
    간격을 재 정확한 fps(24000/1001=23.976)를 얻어야 한다 — 표시값 23.98이 아니라."""
    # 24000/1001 간격(0.0417083s)의 pts_time 라인 — showinfo가 내는 형식.
    step = 1001.0 / 24000.0
    lines = "".join(
        f"[Parsed_showinfo] n:{i} pts_time:{i * step:.6f} other\n" for i in range(30))
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: _Result(returncode=0, stderr=lines))
    fps = ff.video_fps("ffmpeg", tmp_path / "in.mp4")
    assert fps is not None and abs(fps - 24000.0 / 1001.0) < 0.001
    assert abs(fps - 23.98) > 0.002  # 표시 반올림값과 달라야 한다


def test_video_fps_falls_back_to_display_when_no_frames(monkeypatch, tmp_path: Path):
    """showinfo가 프레임을 못 내면(디코드 실패 등) `ffmpeg -i` 표시 fps로 폴백."""
    stderr = ("  Stream #0:0[0x1](und): Video: h264 (High), yuv420p, "
              "1920x1080, 2500 kb/s, 23.98 fps, 23.98 tbr, 24k tbn\n")
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: _Result(returncode=1, stderr=stderr))
    assert ff.video_fps("ffmpeg", tmp_path / "in.mp4") == 23.98


def test_video_fps_none_when_unparseable(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: _Result(returncode=1, stderr="no info"))
    assert ff.video_fps("ffmpeg", tmp_path / "in.mp4") is None


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


def test_cut_segment_fps_frames_v_exact_count_and_no_overrun(tmp_path: Path):
    """실 ffmpeg 통합(23.976fps, 프레임 비정렬 경계): fps를 주면 -frames:v로 끊어
    인접 두 구간의 프레임 수 합이 한 번에 자른 것과 정확히 같다 — 다음 세그 첫
    프레임이 꼬리에 섞이거나(합>전체) 유실되지(합<전체) 않는다는 회귀 잠금."""
    import json as _json
    import shutil as _shutil

    import pytest
    ffmpeg = _shutil.which("ffmpeg")
    ffprobe = _shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe 없음")
    src = tmp_path / "src.mp4"
    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i",
                    "testsrc=duration=3:size=128x96:rate=24000/1001",
                    "-c:v", "libx264", "-preset", "veryfast", str(src)],
                   check=True, capture_output=True)
    fps = 24000.0 / 1001.0

    def nframes(path: Path) -> int:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
             "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
            check=True, capture_output=True, text=True)
        return int(_json.loads(probe.stdout)["streams"][0]["nb_read_frames"])

    # 프레임 그리드(41.708ms)에 걸치지 않는 경계 1000·2000ms로 인접 컷.
    a, b, c = 0, 1000, 2000
    outa, outb, outac = (tmp_path / "a.mp4", tmp_path / "b.mp4",
                         tmp_path / "ac.mp4")
    ff.cut_segment(ffmpeg, src, outa, start_ms=a, end_ms=b, fps=fps)
    ff.cut_segment(ffmpeg, src, outb, start_ms=b, end_ms=c, fps=fps)
    ff.cut_segment(ffmpeg, src, outac, start_ms=a, end_ms=c, fps=fps)
    na, nb, nac = nframes(outa), nframes(outb), nframes(outac)
    # 각 구간은 정확히 round((end-start)*fps/1000) 프레임.
    assert na == round((b - a) * fps / 1000.0)
    assert nb == round((c - b) * fps / 1000.0)
    # 인접 두 컷의 합 = 한 번에 자른 전체 — 중복(초과)도 유실(부족)도 없다.
    assert na + nb == nac


def test_extract_fingerprint_frames_all_frames_cropped_scaled(
        monkeypatch, tmp_path: Path):
    """지문용 추출: fps 필터가 없어야 한다(전 프레임 = 출력 인덱스가 소스 프레임
    번호와 1:1, 컷을 프레임 정확하게 찾는 전제). 크롭 후 축소(scale)로 지문
    해상도만 낮춘다 — 텍스트 유무 변화만 보면 충분(실측 140px 검증)."""
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Result())[1])
    ff.extract_fingerprint_frames("ffmpeg", tmp_path / "in.mp4", tmp_path / "fp",
                                  region=(0.02, 0.03, 0.5, 0.08))
    cmd = calls[0]
    vf = cmd[cmd.index("-vf") + 1]
    assert "fps=" not in vf
    assert vf == ("crop=in_w*0.5000:in_h*0.0800:in_w*0.0200:in_h*0.0300,"
                  "scale=160:-2")
    assert cmd[-1].endswith("f_%06d.png")
    assert (tmp_path / "fp").is_dir()


def test_extract_frames_at_batches_selects_per_chunk(monkeypatch, tmp_path: Path):
    """런 대표 프레임 일괄 추출 — 런마다 -ss 시킹(실측 830ms×2658=9분)이 아니라
    select 디코드 패스로 뽑는다. 필터그래프는 Windows 커맨드라인 32K 한도를
    피해 파일(-filter_script:v)로 전달하고, -frames:v로 청크 마지막 프레임에서
    디코드를 조기 종료한다. 반환은 프레임번호→경로 매핑(청크 내 오름차순 대응)."""
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Result())[1])
    out = ff.extract_frames_at("ffmpeg", tmp_path / "in.mp4", [7, 3, 120],
                               tmp_path / "sel", region=(0.0, 0.0, 0.5, 0.2))
    cmd = calls[0]
    assert "-filter_script:v" in cmd
    graph = Path(cmd[cmd.index("-filter_script:v") + 1]).read_text()
    assert "select=eq(n\\,3)+eq(n\\,7)+eq(n\\,120)" in graph
    assert "crop=in_w*0.5000:in_h*0.2000:in_w*0.0000:in_h*0.0000" in graph
    assert "-fps_mode" in cmd and cmd[cmd.index("-fps_mode") + 1] == "vfr"
    assert "-frames:v" in cmd and cmd[cmd.index("-frames:v") + 1] == "3"
    # 청크 내 오름차순 대응: 3→_00001, 7→_00002, 120→_00003
    assert out[3].name == "c000_00001.png"
    assert out[7].name == "c000_00002.png"
    assert out[120].name == "c000_00003.png"


def test_extract_frames_at_chunks_large_sets(monkeypatch, tmp_path: Path):
    """수천 항짜리 select 식은 ffmpeg 표현식 파서가 'Cannot allocate memory'로
    거부한다(실기: 2658항 즉사) — 청크로 나눠 여러 패스로 돌린다. 각 청크의
    그래프 파일은 그 청크의 프레임만 담고, 매핑은 청크별 패턴을 가리킨다."""
    calls: list[list[str]] = []
    graphs: list[str] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        graphs.append(
            Path(cmd[cmd.index("-filter_script:v") + 1]).read_text())
        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = ff.extract_frames_at("ffmpeg", tmp_path / "in.mp4", [5, 1, 9, 3],
                               tmp_path / "sel", region=(0.0, 0.0, 1.0, 0.35),
                               chunk_size=2)
    assert len(calls) == 2
    assert "select=eq(n\\,1)+eq(n\\,3)" in graphs[0]
    assert "select=eq(n\\,5)+eq(n\\,9)" in graphs[1]
    assert out[1].name == "c000_00001.png"
    assert out[3].name == "c000_00002.png"
    assert out[5].name == "c001_00001.png"
    assert out[9].name == "c001_00002.png"
