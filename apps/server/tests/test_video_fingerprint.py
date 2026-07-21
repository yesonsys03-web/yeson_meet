"""지문 컷 감지 코어(fingerprint.py) — 순수 계산 검증.

실측 근거(2026-07-21, HZBN307 90초/2158프레임): 씬 내 인접 diff는 대부분 0
(중앙값 0)이라 절대 하한(15px)이 주 방어선이고, 컷에서는 텍스트가 통째로
바뀌어 diff가 크게 튄다. 감지 컷 47개에 실제 씬 19개 전부 포함 — 가짜 컷은
런 OCR 후 동일 라벨 병합으로 흡수하므로 놓침(미검출)만이 치명적이다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from apps.server.domain.video_captions.fingerprint import (
    MERGE_GAP, MIN_CUT_DIFF, adjacent_diffs, detect_cuts, frame_mid_ms,
    frame_runs, load_fingerprint,
)


# ── detect_cuts ──────────────────────────────────────────────────────────────

def test_no_cuts_on_uniform_frames():
    assert detect_cuts([0] * 100) == []


def test_single_cut_at_changed_frame():
    # diffs[i]는 frame i+1 vs i — diffs[9]가 튀면 새 런의 첫 프레임은 10.
    diffs = [0] * 20
    diffs[9] = 500
    assert detect_cuts(diffs) == [10]


def test_small_noise_below_threshold_ignored():
    # 중앙값 0이면 임계=MIN_CUT_DIFF(15) — 그 이하 노이즈는 컷이 아니다.
    diffs = [0, 3, 7, MIN_CUT_DIFF, 2, 0]
    assert detect_cuts(diffs) == []


def test_adjacent_candidates_merge_to_first():
    # 디졸브가 여러 프레임에 걸쳐 임계를 넘으면 첫 프레임 하나만 컷으로 본다.
    diffs = [0] * 30
    diffs[10] = 400
    diffs[11] = 380
    diffs[12] = 350
    assert detect_cuts(diffs) == [11]


def test_candidates_beyond_merge_gap_stay_separate():
    diffs = [0] * 30
    diffs[10] = 400
    diffs[10 + MERGE_GAP + 1] = 400  # 병합 간격 밖 — 별개 컷
    assert detect_cuts(diffs) == [11, 11 + MERGE_GAP + 1]


def test_threshold_scales_with_median():
    # 노이즈가 큰 소스(중앙값 10)면 임계=30 — 25는 컷이 아니고 200은 컷.
    diffs = [10] * 50
    diffs[5] = 25
    diffs[20] = 200
    assert detect_cuts(diffs) == [21]


def test_empty_diffs():
    assert detect_cuts([]) == []


# ── frame_runs ───────────────────────────────────────────────────────────────

def test_runs_include_leading_run():
    assert frame_runs([10, 25], 40) == [(0, 10), (10, 25), (25, 40)]


def test_runs_without_cuts_is_single_run():
    assert frame_runs([], 40) == [(0, 40)]


def test_runs_clamp_out_of_range_cuts():
    # 경계 밖(0 또는 n 이상) 컷은 런을 만들지 않는다.
    assert frame_runs([0, 10, 40, 50], 40) == [(0, 10), (10, 40)]


def test_runs_empty_when_no_frames():
    assert frame_runs([], 0) == []


# ── frame_mid_ms ─────────────────────────────────────────────────────────────

def test_mid_ms_is_frame_display_midpoint():
    # 24fps: 프레임 0의 표시구간 [0, 41.67)ms — 중앙 ≈ 20.83 → 21ms.
    assert frame_mid_ms(0, 24.0) == 21
    assert frame_mid_ms(24, 24.0) == round((24 + 0.5) * 1000 / 24.0)


@pytest.mark.parametrize("fps", [24.0, 24000 / 1001, 30000 / 1001, 25.0])
@pytest.mark.parametrize("start,end", [(0, 1), (0, 1862), (120, 212), (7, 8)])
def test_mid_ms_boundaries_reconstruct_exact_frame_count(fps, start, end):
    # cut_segment 규약: N = round((end_ms-start_ms)×fps/1000)가 정확히 프레임
    # 수(end-start)로 떨어져야 꼬리 혼입·유실이 없다(NTSC 장구간 포함).
    s = frame_mid_ms(start, fps)
    e = frame_mid_ms(end, fps)
    assert round((e - s) * fps / 1000.0) == end - start


@pytest.mark.parametrize("fps", [24.0, 24000 / 1001])
def test_mid_ms_lands_inside_frame_interval(fps):
    # -ss 스냅다운 안전: 중앙 시각은 그 프레임의 [PTS, 다음 PTS) 안에 있어야 한다.
    for idx in (0, 1, 1000, 35999):
        t = frame_mid_ms(idx, fps) / 1000.0
        assert idx / fps < t < (idx + 1) / fps


# ── load_fingerprint / adjacent_diffs ────────────────────────────────────────

def _write_png(path: Path, dark_pixels: set[tuple[int, int]],
               size: tuple[int, int] = (16, 8)) -> None:
    from PIL import Image
    im = Image.new("L", size, 255)  # 밝은 배경
    for x, y in dark_pixels:
        im.putpixel((x, y), 0)  # 어두운 텍스트 픽셀
    im.save(path)


def test_fingerprint_binarizes_dark_pixels(tmp_path):
    p = tmp_path / "f.png"
    _write_png(p, {(1, 1), (2, 3)})
    fp = load_fingerprint(p)
    assert fp.shape == (8, 16)  # (height, width)
    assert int(fp.sum()) == 2
    assert fp[1, 1] == 1 and fp[3, 2] == 1


def test_adjacent_diffs_counts_changed_pixels(tmp_path):
    a = tmp_path / "f_000001.png"
    b = tmp_path / "f_000002.png"
    c = tmp_path / "f_000003.png"
    _write_png(a, {(1, 1), (2, 3)})
    _write_png(b, {(1, 1), (2, 3)})           # 동일 → diff 0
    _write_png(c, {(5, 5), (6, 6), (7, 7)})   # 전부 교체 → diff 5
    assert adjacent_diffs([a, b, c]) == [0, 5]


def test_adjacent_diffs_calls_check_cancel(tmp_path):
    p = tmp_path / "f.png"
    _write_png(p, set())

    class Boom(Exception):
        pass

    def cancel():
        raise Boom

    with pytest.raises(Boom):
        adjacent_diffs([p, p], check_cancel=cancel)
