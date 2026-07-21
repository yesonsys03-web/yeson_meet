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
    MERGE_GAP, MIN_CUT_DIFF, adjacent_diffs, detect_cuts, frame_boundary_ms,
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


# ── frame_boundary_ms ────────────────────────────────────────────────────────
# 실측(2026-07-21, 번들 8.1.2 + select 대조): 입력 -ss X는 "X에 보이는 프레임"이
# 아니라 "PTS ≥ X인 첫 프레임"을 내보낸다(스냅업). 프레임 표시구간 '중앙'을
# 경계로 쓰면 모든 클립이 1프레임 늦게 시작해 꼬리에 다음 씬 1프레임이 섞였다
# (시퀀스 16클립 실기 전수 재현). 경계는 '직전 프레임과의 갭 중앙'이어야 한다.

def test_boundary_ms_targets_gap_before_frame():
    # 24fps: 프레임 1 경계 = 갭 중앙 ≈ 20.83 → 21ms. 프레임 0은 0으로 클램프.
    assert frame_boundary_ms(0, 24.0) == 0
    assert frame_boundary_ms(1, 24.0) == 21
    assert frame_boundary_ms(24, 24.0) == round((24 - 0.5) * 1000 / 24.0)


@pytest.mark.parametrize("fps", [24.0, 24000 / 1001, 30000 / 1001, 25.0])
@pytest.mark.parametrize("start,end", [(1, 2), (1, 1863), (120, 212), (7, 8)])
def test_boundary_ms_reconstructs_exact_frame_count(fps, start, end):
    # cut_segment 규약: N = round((end_ms-start_ms)×fps/1000)가 정확히 프레임
    # 수(end-start)로 떨어져야 꼬리 혼입·유실이 없다(NTSC 장구간 포함).
    # idx=0은 0으로 클램프돼 갭 중앙보다 반 갭 늦으므로 이 속성에서 제외 —
    # 프레임 0에서 시작하는 세그먼트(선두 타이틀카드 없는 영상)만 꼬리 ±1프레임
    # 반올림 에지가 있다(문서화된 트레이드오프: 음수 시각이 scenes.json·UI·
    # thumb-at(ge=0)로 새는 것보다 낫다).
    s = frame_boundary_ms(start, fps)
    e = frame_boundary_ms(end, fps)
    assert round((e - s) * fps / 1000.0) == end - start


@pytest.mark.parametrize("fps", [24.0, 24000 / 1001])
def test_boundary_ms_lands_in_gap_before_frame(fps):
    # 스냅업 정확성: PTS(idx-1) < 경계 < PTS(idx) 여야 -ss가 정확히 frame idx를
    # 내보낸다(±0.5ms 반올림에도 갭 중앙이라 여유 ~20ms).
    for idx in (1, 2, 1000, 35999):
        t = frame_boundary_ms(idx, fps) / 1000.0
        assert (idx - 1) / fps < t < idx / fps


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


# ── stable_frame (런 내 판독 프레임 선택) ────────────────────────────────────
# 런 중간을 맹목적으로 읽으면 가짜 컷을 만든 흐릿한 프레임(디졸브·모션)을
# 읽게 돼 오독률이 치솟는다(실기 11.5%). 인접 diff가 가장 작은 '정지' 프레임을
# 고르면 오독을 원천에서 줄인다.

def test_stable_frame_prefers_static_over_middle():
    from apps.server.domain.video_captions.fingerprint import stable_frame
    # 런 [0,6): 앞쪽(0~2)은 움직임, 3~4는 완전 정지 — 중간(2~3)이 아니라 정지 구간.
    diffs = [50, 40, 30, 0, 0, 400]  # diffs[5]=다음 컷 스파이크
    assert stable_frame(diffs, 0, 6) == 4  # in=diffs[3]=0, out=diffs[4]=0


def test_stable_frame_uniform_picks_middle():
    from apps.server.domain.video_captions.fingerprint import stable_frame
    assert stable_frame([0] * 10, 2, 8) == 4  # 동점이면 중앙 근처


def test_stable_frame_avoids_cut_edges():
    from apps.server.domain.video_captions.fingerprint import stable_frame
    # 시작 프레임은 컷 스파이크(in), 끝 프레임은 다음 컷 스파이크(out)를 진다.
    diffs = [500, 0, 0, 0, 500]
    pick = stable_frame(diffs, 1, 5)
    assert pick in (2, 3)
    assert pick != 1 and pick != 4


def test_stable_frame_single_frame_run():
    from apps.server.domain.video_captions.fingerprint import stable_frame
    assert stable_frame([500, 500], 1, 2) == 1


# ── 느린 페이드 컷 (diff_series / detect_cuts_with_fades) ────────────────────
# 시퀀스 전환 등 느린 디졸브는 인접 diff가 임계를 한 번도 못 넘어 컷이 누락되고,
# 두 씬이 한 런에 흡수된다(실기: 030_0330 씬 통째 소실, 130→140 혼입). 윈도우
# diff(i vs i-W)는 페이드 전체 누적이라 크게 튄다 — 플래토에서 컷 1개를 삽입한다.

def test_diff_series_adjacent_and_windowed(tmp_path):
    from apps.server.domain.video_captions.fingerprint import diff_series
    # 프레임 0-2 패턴A, 3-5 패턴B (하드컷): 인접은 경계 1곳, 윈도우(2)는 걸친 곳들.
    a={(1,1),(2,2)}; b={(5,5),(6,6),(7,7)}
    paths=[]
    for i,px in enumerate([a,a,a,b,b,b]):
        p=tmp_path/f"f_{i:06d}.png"; _write_png(p,px); paths.append(p)
    adj, win = diff_series(paths, window=2)
    assert adj == [0,0,5,0,0]
    # win[k] = frame k+2 vs k → [0v2, 1v3, 2v4, 3v5] = [0,5,5,0]
    assert win == [0,5,5,0]


def test_fade_cut_inserted_on_slow_fade():
    from apps.server.domain.video_captions.fingerprint import detect_cuts_with_fades
    # 인접 diff는 전부 임계(15) 미만인 느린 페이드, 윈도우 diff는 페이드 구간에서
    # 크게 튐 → 컷 1개 삽입(플래토 안 인접 diff 최대 지점).
    adj=[0]*30
    for i in range(10,16): adj[i]=8      # 페이드: 인접 8px씩(임계 미만)
    adj[12]=12                            # 가장 빠른 변화 지점
    win=[0]*25
    for k in range(8,16): win[k]=48      # 윈도우(5) 누적은 임계 초과
    cuts=detect_cuts_with_fades(adj, win, window=5)
    assert cuts == [13]                  # argmax adj(=12) → 새 런 첫 프레임 13


def test_fade_cut_suppressed_near_hard_cut():
    from apps.server.domain.video_captions.fingerprint import detect_cuts_with_fades
    # 하드컷 주변의 윈도우 diff 플래토는 이미 잡힌 컷 — 중복 삽입 금지.
    adj=[0]*30
    adj[9]=500                           # 하드컷 → cut=10
    win=[0]*25
    for k in range(5,11): win[k]=500
    cuts=detect_cuts_with_fades(adj, win, window=5)
    assert cuts == [10]


def test_fade_cuts_absent_without_window_signal():
    from apps.server.domain.video_captions.fingerprint import detect_cuts_with_fades
    adj=[0]*30; adj[9]=500
    cuts=detect_cuts_with_fades(adj, [0]*25, window=5)
    assert cuts == [10]
