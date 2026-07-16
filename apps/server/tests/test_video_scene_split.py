from __future__ import annotations

from uuid import uuid4

from apps.server.domain.video_captions import pipeline as pl
from apps.server.domain.video_captions.scene_split import (
    FrameSample, Segment, SlateRule, build_label, compute_boundaries,
    grouping_key, hold_keys, tokenize,
)
from apps.server.domain.video_captions.slate_ocr import pick_slate_line

DELIMS = ["_", " ", "-"]


def test_tokenize_underscore_slate():
    assert tokenize("HH0307_020_0150_AC_v01", DELIMS) == [
        "HH0307", "020", "0150", "AC", "v01"]


def test_tokenize_mixed_delimiters_slate():
    # "Seq 07_S08 - Panel 3" — 공백/언더스코어/하이픈 혼용. 하이픈 양옆 공백은
    # 빈 토큰을 만들지 않아야 한다.
    assert tokenize("Seq 07_S08 - Panel 3", DELIMS) == [
        "Seq", "07", "S08", "Panel", "3"]


def test_grouping_key_joins_selected_tokens():
    toks = ["HH0307", "020", "0150", "AC", "v01"]
    assert grouping_key(toks, [1]) == "020"
    assert grouping_key(toks, [1, 2]) == "020\x1f0150"
    assert grouping_key(toks, [9]) is None  # 범위 밖 → 판독 불가


def test_build_label_joins_prefix_through_upto():
    toks = ["HH0307", "020", "0150", "AC", "v01"]
    assert build_label(toks, 1) == "HH0307_020"       # 시퀀스 라벨(고정 접두 포함)
    assert build_label(toks, 2) == "HH0307_020_0150"  # 씬 라벨


def test_hold_keys_fills_unreadable_frames():
    rule = SlateRule(delimiters=DELIMS, seq_tokens=[1], scene_tokens=[2])
    samples = [
        FrameSample(0, 0, "HH0307_020_0150_AC_v01"),
        FrameSample(1, 1000, ""),                       # 판독 실패 → 홀드
        FrameSample(2, 2000, "HH0307_020_0160_AC_v01"),
    ]
    keyed = hold_keys(samples, rule, "scene")
    assert [k for _, k, _ in keyed] == ["020\x1f0150", "020\x1f0150", "020\x1f0160"]


def test_compute_boundaries_scene_mode_two_real_slates():
    rule = SlateRule(delimiters=DELIMS, seq_tokens=[1], scene_tokens=[2])
    samples = [
        FrameSample(0, 0, "HH0307_020_0150_AC_v01"),
        FrameSample(1, 1000, "HH0307_020_0150_AC_v01"),
        FrameSample(2, 2000, "HH0307_020_0150_AC_v01"),
        FrameSample(3, 3000, "HH0307_020_0170_AC_v01"),
        FrameSample(4, 4000, "HH0307_021_0010_AC_v01"),
        FrameSample(5, 5000, "HH0307_021_0010_AC_v01"),
    ]
    keyed = hold_keys(samples, rule, "scene")
    segs = compute_boundaries(keyed, total_ms=6000, min_ms=0)
    assert segs == [
        Segment("HH0307_020_0150", 0, 3000),
        Segment("HH0307_020_0170", 3000, 4000),
        Segment("HH0307_021_0010", 4000, 6000),
    ]


def test_compute_boundaries_sequence_mode_groups_shots():
    rule = SlateRule(delimiters=DELIMS, seq_tokens=[1], scene_tokens=[2])
    samples = [
        FrameSample(0, 0, "HH0307_020_0150_AC_v01"),
        FrameSample(1, 1000, "HH0307_020_0170_AC_v01"),
        FrameSample(2, 2000, "HH0307_021_0010_AC_v01"),
    ]
    keyed = hold_keys(samples, rule, "sequence")
    segs = compute_boundaries(keyed, total_ms=3000, min_ms=0)
    assert segs == [
        Segment("HH0307_020", 0, 2000),
        Segment("HH0307_021", 2000, 3000),
    ]


def test_compute_boundaries_absorbs_sub_min_blips():
    rule = SlateRule(delimiters=DELIMS, seq_tokens=[1], scene_tokens=[2])
    samples = [
        FrameSample(0, 0, "HH0307_020_0150_AC_v01"),
        FrameSample(1, 1000, "HH0307_020_0150_AC_v01"),
        FrameSample(2, 2000, "HH0307_020_9999_AC_v01"),  # 1초 튐(오독)
        FrameSample(3, 3000, "HH0307_020_0150_AC_v01"),
        FrameSample(4, 4000, "HH0307_020_0150_AC_v01"),
    ]
    keyed = hold_keys(samples, rule, "scene")
    segs = compute_boundaries(keyed, total_ms=5000, min_ms=2000)
    # 2초 미만 구간은 인접(직전) 구간에 흡수 → 단일 세그먼트
    assert segs == [Segment("HH0307_020_0150", 0, 5000)]


def test_pick_slate_line_prefers_most_tokens():
    lines = [
        ("cleanup", 0.99),
        ("HH0307_020_0150_AC_v01", 0.97),
        ("00:02:50:00", 0.98),
    ]
    assert pick_slate_line(lines, ["_", " ", "-"], min_tokens=3) == \
        "HH0307_020_0150_AC_v01"


def test_pick_slate_line_returns_empty_when_no_candidate():
    lines = [("1", 0.99), ("x", 0.5)]
    assert pick_slate_line(lines, ["_", " ", "-"], min_tokens=3) == ""


def test_read_slate_line_swallows_malformed_result(monkeypatch):
    """회귀: OCR 결과가 파싱 불가능한 형태여도 예외를 삼키고 '' 반환."""
    from apps.server.domain.video_captions import slate_ocr
    # 2개 원소 리스트를 반환하는 가짜 엔진 (item[2] 접근 시 IndexError 발생)
    monkeypatch.setattr(slate_ocr, "_get_engine",
                        lambda: (lambda _p: ([["box", "two-only"]], 0.0)))
    assert slate_ocr.read_slate_line("x.png", ["_", " ", "-"]) == ""


def test_read_slate_line_swallows_unparseable_score(monkeypatch):
    """회귀: OCR 결과의 스코어가 float로 파싱 불가능해도 예외를 삼키고 '' 반환."""
    from apps.server.domain.video_captions import slate_ocr
    # 스코어가 float 파싱 불가능한 값
    monkeypatch.setattr(slate_ocr, "_get_engine",
                        lambda: (lambda _p: ([["box", "text", "not-a-number"]], 0.0)))
    assert slate_ocr.read_slate_line("x.png", ["_", " ", "-"]) == ""


def test_save_and_load_scenes_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    ext = uuid4()
    (tmp_path / "video_jobs" / str(ext)).mkdir(parents=True)
    assert pl.load_scenes(ext) is None
    pl.save_scenes(ext, {"rule": {"seq_tokens": [1]}, "segments_scene": []})
    loaded = pl.load_scenes(ext)
    assert loaded["rule"]["seq_tokens"] == [1]


def test_build_scene_data_produces_both_modes():
    from apps.server.domain.video_captions.scene_split import FrameSample
    samples = [
        FrameSample(0, 0, "HH0307_020_0150_AC_v01"),
        FrameSample(1, 1000, "HH0307_020_0170_AC_v01"),
        FrameSample(2, 2000, "HH0307_021_0010_AC_v01"),
    ]
    rule = {"delimiters": ["_", " ", "-"], "seq_tokens": [1], "scene_tokens": [2]}
    data = pl.build_scene_data(samples, rule, total_ms=3000, min_ms=0)
    scene_labels = [s["label"] for s in data["segments_scene"]]
    seq_labels = [s["label"] for s in data["segments_sequence"]]
    assert scene_labels == ["HH0307_020_0150", "HH0307_020_0170", "HH0307_021_0010"]
    assert seq_labels == ["HH0307_020", "HH0307_021"]
    assert len(data["frames"]) == 3
