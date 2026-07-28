from __future__ import annotations

import time
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
        ("cleanup", 0.99, 0.05),
        ("HH0307_020_0150_AC_v01", 0.97, 0.08),
        ("00:02:50:00", 0.98, 0.07),
    ]
    assert pick_slate_line(lines, ["_", " ", "-"], min_tokens=3) == \
        "HH0307_020_0150_AC_v01"


def test_pick_slate_line_returns_empty_when_no_candidate():
    lines = [("1", 0.99, 0.05), ("x", 0.5, 0.05)]
    assert pick_slate_line(lines, ["_", " ", "-"], min_tokens=3) == ""


def test_pick_slate_line_ignores_bottom_watermark():
    """회귀(실기): 하단 워터마크가 슬레이트보다 토큰이 많아도(6>5) 상단 밴드
    밖이므로 제외 — 좌상단 슬레이트가 선택돼야 한다."""
    lines = [
        ("HH0307_090_0080_AC_v01", 0.90, 0.08),  # 좌상단 슬레이트
        ("HZBN307_AnimaticAssembly_FINAL_LOCK_V01_20260331", 0.99, 0.93),
    ]
    assert pick_slate_line(lines, ["_", " ", "-"], min_tokens=2) == \
        "HH0307_090_0080_AC_v01"


def test_pick_slate_line_accepts_space_read_separator():
    """회귀(실기 FL102): OCR이 슬레이트의 '_'를 공백으로 읽는 쇼가 있다 —
    12프레임 표본에서 '_'로 읽힌 적 0회, 공백 10회. 구분자로만 세면 1토큰이라
    후보에서 탈락해 그 쇼 전체가 판독실패가 됐다(구역 미리읽기도 '판독 실패')."""
    lines = [("FL102 I032", 0.89, 0.94)]
    assert pick_slate_line(lines, ["_", "-", "/"], min_tokens=2, top_frac=1.0) \
        == "FL102 I032"


def test_pick_slate_line_space_leniency_requires_digit_fields():
    """공백 관용은 '숫자를 품은 필드'로 갈라질 때만 — 공백이 필드 '안'에 있는
    슬레이트("Seq 11B")나 타이틀카드("THE END")를 슬레이트로 오인하면 안 된다."""
    assert pick_slate_line([("Seq 11B", 0.99, 0.05)], ["_", "-"], min_tokens=2) == ""
    assert pick_slate_line([("THE END", 0.99, 0.05)], ["_", "-"], min_tokens=2) == ""


def test_pick_slate_line_ranks_by_delimiter_tokens():
    """공백 관용은 '후보 자격'에만 쓰고 순위는 구분자 토큰 수 그대로 —
    공백으로 잘게 쪼개지는 텍스트가 진짜 슬레이트를 이기면 안 된다."""
    lines = [("HH0307_020_0150", 0.90, 0.05), ("A1 B2 C3 D4", 0.99, 0.05)]
    assert pick_slate_line(lines, ["_", "-"], min_tokens=2) == "HH0307_020_0150"


def test_pick_slate_line_no_top_band_candidate_returns_empty():
    """상단 밴드에 읽힌 게 없으면 하단 텍스트로 폴백하지 않고 판독실패("") —
    "" 는 hold_keys가 직전 유효값으로 홀드하므로 안전하다."""
    lines = [
        ("HZBN307_AnimaticAssembly_FINAL_LOCK_V01_20260331", 0.99, 0.93),
    ]
    assert pick_slate_line(lines, ["_", " ", "-"], min_tokens=2) == ""


def test_ocr_engine_is_per_thread(monkeypatch):
    """정밀화를 병렬로 돌리므로 엔진을 스레드끼리 공유하지 않는다 — RapidOCR
    래퍼가 호출 중 self에 상태를 두면 동시 호출이 서로를 오염시킬 수 있다.
    스레드마다 자기 엔진을 쓰면 그 위험 자체가 없어진다."""
    import threading

    from apps.server.domain.video_captions import slate_ocr
    monkeypatch.setattr(slate_ocr, "_new_engine", lambda **kw: object())
    slate_ocr._reset_engines()
    seen = []
    seen.append(slate_ocr._get_engine())
    seen.append(slate_ocr._get_engine())  # 같은 스레드 → 재사용
    other: list = []
    t = threading.Thread(target=lambda: other.append(slate_ocr._get_engine()))
    t.start(); t.join()
    assert seen[0] is seen[1], "같은 스레드에서는 엔진을 재사용해야 한다"
    assert other[0] is not seen[0], "다른 스레드는 자기 엔진을 써야 한다"


def test_ocr_engine_caps_detection_upscale(monkeypatch):
    """회귀(실측): RapidOCR 기본 검출 설정(limit_type=min, 736)은 작은 이미지를
    짧은 변 기준으로 '확대'한다. 사용자가 지정한 구역(336x63)이 약 3900x736으로
    부풀려져 판독이 1014ms까지 걸렸고, 과확대가 검출을 망가뜨려 텍스트가 잘리기도
    했다("HH0307_030"만 읽힘). max 기준으로 바꾸면 확대가 없어진다(크롭 40ms,
    전체 프레임도 894→675ms, 결과는 동일)."""
    from apps.server.domain.video_captions import slate_ocr
    captured = {}

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(slate_ocr, "_new_engine", lambda **kw: FakeRapidOCR(**kw))
    slate_ocr._reset_engines()
    slate_ocr._get_engine()
    assert captured.get("det_limit_type") == "max"
    assert captured.get("det_limit_side_len") == 960


def test_read_slate_line_full_band_when_region_cropped(monkeypatch, tmp_path):
    """사용자가 OCR 영역을 지정하면 프레임이 그 영역으로 잘려 들어온다 — 크롭
    자체가 영역 필터이므로 상단 밴드 가정을 적용하면 안 된다(잘린 이미지에서는
    슬레이트가 세로 중앙·하단에 올 수 있다). top_frac=1.0으로 전 영역 허용."""
    from PIL import Image

    from apps.server.domain.video_captions import slate_ocr
    png = tmp_path / "c.png"
    Image.new("RGB", (640, 80)).save(png)
    result = [
        [[[10, 50], [400, 50], [400, 75], [10, 75]],  # y중심 0.78 — 밴드 밖
         "HH0307_010_0010_AC_v01", 0.95],
    ]
    monkeypatch.setattr(slate_ocr, "_get_engine",
                        lambda: (lambda _p: (result, 0.0)))
    # 기본(전체 프레임 가정)에서는 걸러지고
    assert slate_ocr.read_slate_line(png, ["_", "-"]) == ""
    # 크롭된 입력에서는 그대로 읽힌다
    assert slate_ocr.read_slate_line(png, ["_", "-"], top_frac=1.0) == \
        "HH0307_010_0010_AC_v01"


def test_read_slate_line_maps_box_y_to_fraction(monkeypatch, tmp_path):
    """read_slate_line이 박스 y중심/이미지 높이 → y_frac으로 환산해 상단
    슬레이트를 고르는지 엔드투엔드 확인(가짜 엔진 + 실제 PNG)."""
    from PIL import Image

    from apps.server.domain.video_captions import slate_ocr
    png = tmp_path / "f.png"
    Image.new("RGB", (1280, 720)).save(png)
    result = [
        [[[60, 40], [300, 40], [300, 70], [60, 70]],
         "HH0307_020_0150_AC_v01", 0.90],
        [[[400, 650], [900, 650], [900, 690], [400, 690]],
         "HZBN307_AnimaticAssembly_FINAL_LOCK_V01_20260331", 0.99],
    ]
    monkeypatch.setattr(slate_ocr, "_get_engine",
                        lambda: (lambda _p: (result, 0.0)))
    assert slate_ocr.read_slate_line(png, ["_", " ", "-"]) == \
        "HH0307_020_0150_AC_v01"


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


def test_compute_boundaries_centers_first_run_after_invalid_lead():
    """회귀(실기): 영상 앞머리가 타이틀카드(판독실패)면 첫 세그먼트가 첫 유효
    샘플에서 시작해 실제 시작보다 늦다 — 내부 경계처럼 반 간격 당겨야 한다."""
    rule = SlateRule(delimiters=["_"], seq_tokens=[1], scene_tokens=[2])
    samples = [
        FrameSample(0, 0, ""),        # 타이틀 카드(판독실패)
        FrameSample(1, 2000, ""),
        FrameSample(2, 4000, ""),
        FrameSample(3, 6000, "HH0307_010_0010_AC_v01"),
        FrameSample(4, 8000, "HH0307_010_0010_AC_v01"),
    ]
    keyed = hold_keys(samples, rule, "sequence")
    segs = compute_boundaries(keyed, total_ms=10000, min_ms=2000,
                              interval_ms=2000, absorb_single=True)
    assert segs[0].start_ms == 5000  # 6000 - interval/2


def test_refine_runs_boundaries_concurrently(monkeypatch, tmp_path):
    """경계끼리는 독립이라 병렬로 처리한다(실측 병목은 프레임 추출 184ms).
    동시에 여러 경계가 진행되는지 확인하고, 결과는 순차와 같아야 한다 —
    각 경계는 '원래' 이웃 값으로 계산하고 적용은 나중에 한 번에 한다."""
    import asyncio
    import math
    import threading

    from uuid import uuid4

    from apps.server.domain.video_captions import pipeline as pl
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    eid = uuid4()
    frame = 1001 / 24
    inflight = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake_extract(ffmpeg, src, t_ms, dst, proc_key=None, region=None):
        with lock:
            inflight["now"] += 1
            inflight["max"] = max(inflight["max"], inflight["now"])
        time.sleep(0.02)  # 추출 지연 흉내 — 동시성이 없으면 max가 1로 남는다
        with lock:
            inflight["now"] -= 1
        calls[str(dst)] = t_ms

    calls: dict[str, int] = {}
    # 경계 i의 전환 시각 = i*4000ms (각 구간 4초)
    def fake_read(dst, delimiters, top_frac=1.0):
        t = calls[str(dst)]
        idx = int(math.ceil(t / 4000)) if t % 4000 else t // 4000
        seq = min(4, max(0, int(t // 4000) + (1 if t % 4000 else 0)))
        return f"HH_{seq:03d}_0010_AC"

    monkeypatch.setattr(pl, "extract_frame", fake_extract)
    monkeypatch.setattr(pl, "read_slate_line", fake_read)
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    segs = [{"label": f"HH_{i:03d}", "start_ms": i * 4000,
             "end_ms": (i + 1) * 4000} for i in range(5)]
    pl.save_scenes(eid, {
        "scanning": False, "interval_ms": 2000, "frames": [],
        "rule": {"delimiters": ["_"], "seq_tokens": [1], "scene_tokens": [2]},
        "segments_sequence": segs, "segments_scene": [],
    })
    asyncio.run(pl.run_scene_refine(eid, "sequence"))
    assert inflight["max"] > 1, "경계가 순차로만 처리되면 병렬화 의미가 없다"
    out = pl.load_scenes(eid)["segments_sequence"]
    assert len(out) == 5
    # 시간축이 이어져 있어야 한다(구간 사이에 구멍/역전 없음)
    for a, b in zip(out, out[1:]):
        assert a["end_ms"] == b["start_ms"]
        assert a["start_ms"] < a["end_ms"]


def test_refine_clears_refining_flag_when_cancelled(monkeypatch, tmp_path):
    """회귀(실기): 취소 엔드포인트가 플래그를 내려도, 그 직후 아직 돌던 워커가
    진행률을 다시 써 refining=true로 되살아났다. 워커는 다음 반복에서 취소를
    감지하고 조용히 끝나므로 플래그가 켜진 채 남아 프론트가 영원히 폴링한다.
    멈추는 쪽이 자기 플래그를 내려야 한다."""
    import asyncio

    from uuid import uuid4

    from apps.server.domain.video_captions import pipeline as pl
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    eid = uuid4()
    calls = {"n": 0}

    def fake_extract(ffmpeg, src, t_ms, dst, proc_key=None, region=None):
        calls["n"] += 1
        if calls["n"] == 3:  # 진행 중 취소(세대 증가)
            pl._bump_generation(eid)

    monkeypatch.setattr(pl, "extract_frame", fake_extract)
    monkeypatch.setattr(pl, "read_slate_line",
                        lambda dst, delimiters, top_frac=1.0: "HH_020_0010_AC")
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    pl.save_scenes(eid, {
        "scanning": False, "interval_ms": 2000, "frames": [],
        "rule": {"delimiters": ["_"], "seq_tokens": [1], "scene_tokens": [2]},
        "segments_sequence": [
            {"label": "HH_010", "start_ms": 0, "end_ms": 4000},
            {"label": "HH_020", "start_ms": 4000, "end_ms": 8000},
            {"label": "HH_030", "start_ms": 8000, "end_ms": 12000},
        ],
        "segments_scene": [],
    })
    asyncio.run(pl.run_scene_refine(eid, "sequence"))
    st = pl.load_refine_status(eid)
    assert st is not None and st["refining"] is False, \
        "취소 후 refining이 켜진 채 남으면 프론트가 영원히 폴링한다"


def test_refine_first_segment_start(monkeypatch, tmp_path):
    """회귀(실기 010 1초 유실): 첫 세그먼트 시작(>0)도 정밀화 대상 — 앞쪽
    판독실패(타이틀카드) 구간과의 전환 프레임까지 이진탐색으로 좁혀야 한다."""
    import asyncio
    import math

    from uuid import uuid4

    from apps.server.domain.video_captions import pipeline as pl
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    eid = uuid4()
    frame = 1001 / 24
    t_cut = 120 * frame  # ≈5005ms — 010 실제 시작

    def frame_pts(t_ms: float) -> float:
        return math.ceil(t_ms / frame) * frame

    calls: dict[str, int] = {}

    def fake_extract(ffmpeg, src, t_ms, dst, proc_key=None, region=None):
        calls[str(dst)] = t_ms

    def fake_read(dst, delimiters, top_frac=1.0):
        return ("HH_010_0010_AC" if frame_pts(calls[str(dst)]) >= t_cut
                else "")  # 타이틀 카드 = 판독실패

    monkeypatch.setattr(pl, "extract_frame", fake_extract)
    monkeypatch.setattr(pl, "read_slate_line", fake_read)
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    pl.save_scenes(eid, {
        "scanning": False, "interval_ms": 2000, "frames": [],
        "rule": {"delimiters": ["_"], "seq_tokens": [1], "scene_tokens": [2]},
        "segments_sequence": [
            {"label": "HH_010", "start_ms": 6000, "end_ms": 20000},  # ~1s 늦음
            {"label": "HH_020", "start_ms": 20000, "end_ms": 30000},
        ],
        "segments_scene": [],
    })
    asyncio.run(pl.run_scene_refine(eid, "sequence"))
    s0 = pl.load_scenes(eid)["segments_sequence"][0]["start_ms"]
    assert t_cut - frame < s0 <= t_cut, f"첫 세그 시작 {s0}ms ≠ 전환 {t_cut:.1f}ms"


def test_label_matches_tolerates_merged_tokens():
    """회귀(실기): OCR이 언더스코어를 놓쳐 "HH0307_1200010"으로 붙어 읽혀도
    squash 접두 일치로 120 쪽으로 분류돼야 한다."""
    from apps.server.domain.video_captions.scene_split import label_matches
    delims = ["_", "-"]
    assert label_matches("HH0307_1200010", "HH0307_120", "HH0307_110", delims)
    assert not label_matches("HH0307_110_0310", "HH0307_120", "HH0307_110", delims)
    assert not label_matches("", "HH0307_120", "HH0307_110", delims)
    # 중복 라벨 접미사(한 라벨이 다른 라벨의 접두) — 반대쪽이 더 구체적으로
    # 일치하면 보수적으로 불일치 처리
    assert not label_matches("HH0307_120_02", "HH0307_120", "HH0307_120_02", delims)


def test_refine_recovers_boundary_outside_window_with_misreads(monkeypatch, tmp_path):
    """회귀(실기 110→120): 오독('HH_1200010')이 별개 키로 흡수돼 사전 경계가
    ~2초 지각하면 정밀화 창(±interval)이 전환을 못 담는다 — 창 시작이 이미
    next면 왼쪽으로 확장하고, 오독 프레임도 접두 일치로 next로 분류해
    프레임 단위까지 복구해야 한다."""
    import asyncio
    import math

    from uuid import uuid4

    from apps.server.domain.video_captions import pipeline as pl
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    eid = uuid4()
    frame = 1001 / 24
    t_cut = 28 * frame  # ≈1167.83ms — 실제 전환

    def frame_pts(t_ms: float) -> float:
        return math.ceil(t_ms / frame) * frame

    calls: dict[str, int] = {}

    def fake_extract(ffmpeg, src, t_ms, dst, proc_key=None, region=None):
        calls[str(dst)] = t_ms

    def fake_read(dst, delimiters, top_frac=1.0):
        pts = frame_pts(calls[str(dst)])
        if pts < t_cut:
            return "HH_110_0310_AC"
        if pts < t_cut + 400:  # 전환 직후 구간은 언더스코어 유실 오독
            return "HH_1200010_AC"
        return "HH_120_0010_AC"

    monkeypatch.setattr(pl, "extract_frame", fake_extract)
    monkeypatch.setattr(pl, "read_slate_line", fake_read)
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    pl.save_scenes(eid, {
        "scanning": False, "interval_ms": 2000, "frames": [],
        "rule": {"delimiters": ["_"], "seq_tokens": [1], "scene_tokens": [2]},
        "segments_sequence": [
            {"label": "HH_110", "start_ms": 0, "end_ms": 3500},   # 사전 경계
            {"label": "HH_120", "start_ms": 3500, "end_ms": 8000},  # ~2.3s 지각
        ],
        "segments_scene": [],
    })
    asyncio.run(pl.run_scene_refine(eid, "sequence"))
    b = pl.load_scenes(eid)["segments_sequence"][1]["start_ms"]
    assert t_cut - frame < b <= t_cut, f"경계 {b}ms가 전환 {t_cut:.1f}ms에서 벗어남"


def test_refine_boundary_is_frame_exact(monkeypatch, tmp_path):
    """회귀(실기): 이진탐색 종료 임계가 1프레임(23.976fps=41.7ms)보다 크면
    경계가 전환 프레임 '뒤'로 수렴해(실측 10/15 경계 지각) 새 시퀀스 첫 프레임이
    직전 클립으로 새 나간다. 종료 후 경계 b는 (전환pts-1프레임, 전환pts] 안,
    즉 -ss b 컷이 정확히 전환 프레임에서 시작해야 한다."""
    import asyncio
    import math

    from uuid import uuid4

    from apps.server.domain.video_captions import pipeline as pl
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    eid = uuid4()
    frame = 1001 / 24  # ms
    t_cut = 28 * frame  # ≈1167.83ms — 150ms 임계에선 b=1250(지각)이 되는 지점

    def frame_pts(t_ms: float) -> float:
        return math.ceil(t_ms / frame) * frame

    calls: dict[str, int] = {}

    def fake_extract(ffmpeg, src, t_ms, dst, proc_key=None, region=None):
        calls[str(dst)] = t_ms

    def fake_read(dst, delimiters, top_frac=1.0):
        return ("HH_new_x" if frame_pts(calls[str(dst)]) >= t_cut
                else "HH_old_x")

    monkeypatch.setattr(pl, "extract_frame", fake_extract)
    monkeypatch.setattr(pl, "read_slate_line", fake_read)
    monkeypatch.setattr(pl, "locate_ffmpeg", lambda: "ffmpeg")
    pl.save_scenes(eid, {
        "scanning": False, "interval_ms": 2000, "frames": [],
        "rule": {"delimiters": ["_"], "seq_tokens": [1], "scene_tokens": [2]},
        "segments_sequence": [
            {"label": "HH_old", "start_ms": 0, "end_ms": 2000},
            {"label": "HH_new", "start_ms": 2000, "end_ms": 6000},
        ],
        "segments_scene": [],
    })
    asyncio.run(pl.run_scene_refine(eid, "sequence"))
    b = pl.load_scenes(eid)["segments_sequence"][1]["start_ms"]
    assert t_cut - frame < b <= t_cut, f"경계 {b}ms가 전환 {t_cut:.1f}ms에서 벗어남"


def test_save_and_load_scenes_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    ext = uuid4()
    (tmp_path / "video_jobs" / str(ext)).mkdir(parents=True)
    assert pl.load_scenes(ext) is None
    pl.save_scenes(ext, {"rule": {"seq_tokens": [1]}, "segments_scene": []})
    loaded = pl.load_scenes(ext)
    assert loaded["rule"]["seq_tokens"] == [1]


def test_dedupe_labels_suffixes_collisions():
    from apps.server.domain.video_captions.scene_split import dedupe_labels
    assert dedupe_labels(["HH0307_020", "HH0307_021", "HH0307_020"]) == \
        ["HH0307_020", "HH0307_021", "HH0307_020_02"]
    assert dedupe_labels(["a", "a", "a"]) == ["a", "a_02", "a_03"]
    assert dedupe_labels([]) == []


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


def test_grouping_key_normalizes_internal_whitespace():
    from apps.server.domain.video_captions.scene_split import grouping_key, build_label
    # OCR이 같은 필드를 "Seq01B"/"Seq 01B"로 들쭉날쭉 읽어도 같은 키/라벨.
    assert grouping_key(["Seq 01B", "S19"], [0]) == grouping_key(["Seq01B", "S19"], [0])
    assert build_label(["Seq 01B", "S19"], 0) == "Seq01B"


def test_boundaries_absorb_ocr_space_blips():
    from apps.server.domain.video_captions.scene_split import (
        FrameSample, SlateRule, compute_boundaries, hold_keys)
    rule = SlateRule(delimiters=["_", "-"], seq_tokens=[0], scene_tokens=[1])
    samples = [
        FrameSample(0, 0, "Seq01B_S19"),
        FrameSample(1, 2000, "Seq 01B_S19"),   # 공백 블립 — 같은 시퀀스여야
        FrameSample(2, 4000, "Seq01B_S19"),
    ]
    segs = compute_boundaries(hold_keys(samples, rule, "sequence"), 6000, min_ms=0)
    assert [s.label for s in segs] == ["Seq01B"]  # 하나로 병합


def test_compute_boundaries_centers_cut_between_samples():
    from apps.server.domain.video_captions.scene_split import compute_boundaries
    # A A B B, 2초 간격. 실제 전환은 두 샘플(2000,4000) 사이 → 컷을 중간 3000으로.
    keyed = [(0, "A", "A"), (2000, "A", "A"), (4000, "B", "B"), (6000, "B", "B")]
    segs = compute_boundaries(keyed, 8000, min_ms=0, interval_ms=2000)
    assert segs[0].start_ms == 0 and segs[0].end_ms == 3000
    assert segs[1].start_ms == 3000 and segs[1].end_ms == 8000


def test_compute_boundaries_absorbs_internal_single_sample_in_sequence():
    from apps.server.domain.video_captions.scene_split import compute_boundaries
    # A A VAL B B — VAL은 내부 1샘플 고립 오독.
    keyed = [(0, "A", "A"), (2000, "A", "A"), (4000, "VAL", "VAL"),
             (6000, "B", "B"), (8000, "B", "B")]
    seq = compute_boundaries(keyed, 10000, min_ms=0, interval_ms=2000, absorb_single=True)
    assert [s.label for s in seq] == ["A", "B"]  # VAL 흡수됨
    scene = compute_boundaries(keyed, 10000, min_ms=0, interval_ms=2000, absorb_single=False)
    assert [s.label for s in scene] == ["A", "VAL", "B"]  # 씬 모드는 유지


def test_compute_boundaries_keeps_single_sample_at_edges():
    from apps.server.domain.video_captions.scene_split import compute_boundaries
    # 마지막 1샘플(B)은 흡수하지 않는다(끝단은 확신 낮음) — 실기 Seq80 같은 끝 시퀀스 보호.
    keyed = [(0, "A", "A"), (2000, "A", "A"), (4000, "B", "B")]
    seq = compute_boundaries(keyed, 6000, min_ms=0, interval_ms=2000, absorb_single=True)
    assert [s.label for s in seq] == ["A", "B"]


# ── runs_to_segments (지문 컷 감지) ──────────────────────────────────────────
# 런 = 지문 컷 사이 구간 + 그 안에서 읽은 슬레이트. 경계는 이미 프레임 정확한
# 컷이므로 min_ms·중앙정렬·정밀화가 없고, 같은 키의 연속 런 병합(가짜 컷 흡수)과
# 판독실패 홀드만 한다.

RULE = SlateRule(delimiters=["_", "-"], seq_tokens=[1], scene_tokens=[2])


def _run(start_ms, end_ms, text):
    from apps.server.domain.video_captions.scene_split import SceneRun
    return SceneRun(start_ms=start_ms, end_ms=end_ms, text=text)


def test_runs_merge_consecutive_same_key():
    # 씬 내부 가짜 컷(반투명 바 뒤 애니 움직임)으로 런이 갈라져도 같은 키면 병합.
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _run(0, 100, "HH0307_010_0010_AC_v01"),
        _run(100, 250, "HH0307_010_0010_AC_v01"),
        _run(250, 500, "HH0307_010_0020_AC_v01"),
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_010_0010", 0, 250), ("HH0307_010_0020", 250, 500)]


def test_runs_drop_leading_unreadable():
    # 선두 판독실패(타이틀카드) 런은 버린다 — 첫 유효 런의 시작이 곧 실제 컷.
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _run(0, 5000, ""),
        _run(5000, 8000, "HH0307_010_0010_AC_v01"),
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_010_0010", 5000, 8000)]


def test_runs_hold_mid_unreadable_to_previous():
    # 중간 판독실패 런은 직전 세그먼트의 연속으로 본다(hold_keys와 같은 홀드).
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _run(0, 100, "HH0307_010_0010_AC_v01"),
        _run(100, 200, ""),
        _run(200, 300, "HH0307_010_0020_AC_v01"),
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_010_0010", 0, 200), ("HH0307_010_0020", 200, 300)]


def test_runs_unreadable_then_same_key_still_merges():
    # A | 실패 | A — 실패 런이 직전에 붙은 뒤 다음 A도 같은 키라 한 세그로.
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _run(0, 100, "HH0307_010_0010_AC_v01"),
        _run(100, 200, ""),
        _run(200, 300, "HH0307_010_0010_AC_v01"),
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_010_0010", 0, 300)]


def test_runs_sequence_mode_groups_by_seq_tokens_only():
    # 시퀀스 모드: 씬 토큰이 달라도 seq 토큰이 같으면 한 세그먼트.
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _run(0, 100, "HH0307_010_0010_AC_v01"),
        _run(100, 200, "HH0307_010_0020_AC_v01"),
        _run(200, 300, "HH0307_020_0010_AC_v01"),
    ]
    segs = runs_to_segments(runs, RULE, "sequence")
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_010", 0, 200), ("HH0307_020", 200, 300)]


def test_runs_contiguous_no_gaps():
    # 어떤 흡수·병합 후에도 시간축은 연속이어야 한다(빈틈=프레임 유실).
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _run(0, 700, ""),
        _run(700, 1000, "HH0307_010_0010_AC_v01"),
        _run(1000, 1400, ""),
        _run(1400, 2000, "HH0307_010_0020_AC_v01"),
        _run(2000, 2600, "HH0307_010_0020_AC_v01"),
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert segs[0].start_ms == 700 and segs[-1].end_ms == 2600
    for a, b in zip(segs, segs[1:]):
        assert a.end_ms == b.start_ms


# ── 런 텍스트 canonical화 (지문 오독 원천 차단) ─────────────────────────────
# 지문 방식은 런 중간(가짜 컷을 만든 흐릿한 프레임 근처)을 읽어 구분자 유실
# 오독률이 간격 스캔의 10배(실기 11.5%)다. 그룹핑 전에 데이터 자신의 최빈
# 토큰 모양(템플릿)으로 오독을 재분해해 키가 갈라지는 것을 원천 차단한다
# (프론트 sceneSplitLogic.ts tokenShape/labelTemplate/reparse의 서버 이식 —
# 경계 계산의 단일 출처는 서버다).

def test_token_shape_runs():
    from apps.server.domain.video_captions.scene_split import token_shape
    assert token_shape("HH0307") == "U2D4"
    assert token_shape("v01") == "L1D2"
    assert token_shape("Seq 11B") == "U1L2D2U1"  # 내부 공백은 squash


def test_label_template_modal_shapes():
    from apps.server.domain.video_captions.scene_split import label_template
    texts = ["HH0307_010_0010_AC_v01"] * 5 + ["HH0307010_0020_AC_v01", "VAL"]
    assert label_template(texts, ["_", "-"]) == ["U2D4", "D3", "D4", "U2", "L1D2"]


def test_canonicalize_fixes_delimiter_loss():
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    texts = (["HH0307_010_0010_AC_v01"] * 5
             + ["HH0307010_0020_AC_v01",    # 쇼+시퀀스 붙음(실기)
                "HH0307_0100030_ACv01"])    # 시퀀스+씬·AC+v01 붙음(실기)
    out = canonicalize_texts(texts, ["_", "-"])
    assert out[:5] == texts[:5]  # 정상은 그대로
    assert out[5] == "HH0307_010_0020_AC_v01"
    assert out[6] == "HH0307_010_0030_AC_v01"


def test_canonicalize_unifies_lookalike_letter_heads():
    """EASA05 실기: 'Seq'의 q가 g로 읽히는 글자↔글자 오독은 모양(U1L2D2U1)이
    동일해 템플릿·닮은꼴(글자↔숫자) 교정을 전부 통과하고, 같은 씬이 Seq/Seg
    두 세그먼트로 갈라진다(혼재 121/200씬 실측). 같은 토큰 자리의 머리글자가
    닮은꼴 소문자쌍(q↔g) 한 글자 차이로 갈리면 전역 다수결로 통일한다."""
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    texts = (["Seq01A_S02-Panel4"] * 6
             + ["Seg01A_S02-Panel5",     # 다수(Seq)와 q↔g 한 글자 차이
                "Seg12B_S03-Panel1"])    # 다른 씬이어도 같은 자리 오독이면 통일
    out = canonicalize_texts(texts, ["_", "-"])
    assert out[:6] == texts[:6]          # 다수형은 그대로
    assert out[6].startswith("Seq01A_S02")
    assert out[7].startswith("Seq12B_S03")


def test_canonicalize_declared_example_beats_corrupt_majority():
    """예시 슬레이트를 선언하면 머리글자 통일이 다수결이 아니라 선언을 따른다.
    오독(Seg)이 다수인 코퍼스에서 다수결은 정답(Seq)을 오독으로 뒤집는데,
    선언된 구조가 있으면 그 방향으로만 교정한다 — 사용자 제안 기능의 핵심."""
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    texts = ["Seg01A_S01-Panel1"] * 5 + ["Seq01A_S01-Panel2"] * 2  # 오독이 다수
    out = canonicalize_texts(texts, ["_", "-"],
                             example="Seq 01A_S01 - Panel 1")
    assert all(t.startswith("Seq01A") for t in out)
    # 선언이 없으면 기존 다수결 그대로(호환) — Seq 소수가 Seg로 통일된다.
    plain = canonicalize_texts(texts, ["_", "-"])
    assert all(t.startswith("Seg01A") for t in plain)


def test_fingerprint_segments_honor_declared_example():
    """rule_dict.example이 지문 세그먼트 빌드까지 배선되는지 — 경계 계산에서
    예시를 넘기면 Seg 다수 코퍼스도 Seq 라벨로 병합된다."""
    from apps.server.domain.video_captions import pipeline as pl
    runs = ([{"start_ms": i * 1000, "end_ms": (i + 1) * 1000,
              "text": "Seg01A_S01-Panel1"} for i in range(5)]
            + [{"start_ms": 5000, "end_ms": 6000, "text": "Seq01A_S01-Panel2"}])
    out = pl.build_fingerprint_segments(
        runs, {"delimiters": ["_", "-"], "seq_tokens": [0], "scene_tokens": [1],
               "example": "Seq 01A_S01 - Panel 1"})
    labels = [s["label"] for s in out["segments_scene"]]
    assert labels == ["Seq01A_S01"]  # 한 씬으로 병합 + 선언된 머리


def test_canonicalize_head_unify_noop_for_stable_shows():
    """머리글자가 안 갈리는 쇼는 한 글자도 안 바뀐다(타 쇼 무회귀 보증).
    다수결 동수(어느 쪽이 진짜인지 근거 없음)도 손대지 않는다."""
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    stable = ["HH0307_010_0010_AC_v01"] * 5 + ["HH0307_010_0020_AC_v01"]
    assert canonicalize_texts(stable, ["_", "-"]) == stable
    tie = ["Seq01A_S01"] * 3 + ["Seg01A_S01"] * 3
    assert canonicalize_texts(tie, ["_", "-"]) == tie


def test_canonicalize_leaves_ambiguous_and_corrupt():
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    texts = (["HH0307_010_0010_AC_v01"] * 5
             + ["HH0307_010_00305_AC_v01",  # 자릿수 남음 — 억지 교정 금지
                "HH030Z_140_0010_AC_v01",   # 문자 오독 — 템플릿 불일치
                ""])                         # 판독 실패
    out = canonicalize_texts(texts, ["_", "-"])
    assert out[5:] == texts[5:]


# ── 클러스터 갈라짐 흡수 (runs_to_segments absorb_flanked_ms) ────────────────
# 같은 키 두 세그 사이에 낀 '연속' 오독 블록(총 길이 ≤ cap)을 통째로 흡수한다.
# 단일 낀 것만 잡는 프론트 정리로는 연속 오독(A|X|Y|A)이 남는다(실기 시퀀스
# 322→104 잔존). 캡이 진짜 비단조(A|B|A에서 B가 긴 경우)를 보존한다.

def test_runs_absorb_flanked_cluster():
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _run(0, 1000, "HH0307_010_0010_AC_v01"),
        _run(1000, 1100, "HH0307_010_0010AC_v01"),   # 오독 X
        _run(1100, 1200, "HH03070100010_AC_v01"),    # 오독 Y (연속 클러스터)
        _run(1200, 2000, "HH0307_010_0010_AC_v01"),
    ]
    segs = runs_to_segments(runs, RULE, "scene", absorb_flanked_ms=5000)
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_010_0010", 0, 2000)]


def test_runs_absorb_flanked_respects_cap():
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _run(0, 1000, "HH0307_010_0010_AC_v01"),
        _run(1000, 7000, "HH0307_010_0020_AC_v01"),  # 6초 — 진짜 씬(비단조) 보존
        _run(7000, 8000, "HH0307_010_0010_AC_v01"),
    ]
    segs = runs_to_segments(runs, RULE, "scene", absorb_flanked_ms=5000)
    assert [s.label for s in segs] == [
        "HH0307_010_0010", "HH0307_010_0020", "HH0307_010_0010"]


def test_runs_absorb_flanked_default_off():
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _run(0, 1000, "HH0307_010_0010_AC_v01"),
        _run(1000, 1100, "HH0307_010_0010AC_v01"),
        _run(1100, 2000, "HH0307_010_0010_AC_v01"),
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert len(segs) == 3  # absorb_flanked_ms 미지정=기존 동작


def test_runs_absorb_flanked_keeps_edges():
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _run(0, 100, "HH0307_010_0010AC_v01"),       # 선두 오독 — 흡수 짝 없음
        _run(100, 1000, "HH0307_010_0010_AC_v01"),
    ]
    segs = runs_to_segments(runs, RULE, "scene", absorb_flanked_ms=5000)
    assert [s.label for s in segs] == ["HH0307_010_0010AC", "HH0307_010_0010"]


# ── 판독불가 전환 블록 귀속 (cut_diff 휴리스틱) ──────────────────────────────
# 씬 전환부에 OCR이 전혀 안 되는 구간(디졸브·슬레이트 페이드인)이 끼면 어느
# 씬 소속인지 OCR로는 판별 불가 — 유일한 신호는 지문 컷 세기다. 블록 '들어가는
# 컷'이 '나가는 컷'보다 훨씬 세면(≥3배) 시각 컷이 블록 앞에 있다는 뜻이라
# 블록은 다음 씬의 머리다(실기 0040→0050: 4248 vs 47, 90배 — 0050 내용이
# 0040 꼬리에 붙어 보이던 케이스). 애매하면 기존대로 앞 씬에 붙인다.

def _runc(start_ms, end_ms, text, cut_diff=0):
    from apps.server.domain.video_captions.scene_split import SceneRun
    return SceneRun(start_ms=start_ms, end_ms=end_ms, text=text,
                    cut_diff=cut_diff)


def test_unreadable_block_moves_to_next_on_strong_entry_cut():
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _runc(0, 1000, "HH0307_030_0040_AC_v01", 500),
        _runc(1000, 1900, "", 4248),   # 판독불가 블록 — 들어컷 강함
        _runc(1900, 3000, "HH0307_030_0050_AC_v01", 47),  # 나가컷 약함
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_030_0040", 0, 1000), ("HH0307_030_0050", 1000, 3000)]


def test_unreadable_block_stays_with_prev_when_ambiguous():
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _runc(0, 1000, "HH0307_030_0210_AC_v01", 500),
        _runc(1000, 1900, "", 4473),
        _runc(1900, 3000, "HH0307_030_0230_AC_v01", 1794),  # 2.5배 — 애매
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_030_0210", 0, 1900), ("HH0307_030_0230", 1900, 3000)]


def test_unreadable_block_without_cut_diff_keeps_old_behavior():
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    # cut_diff 미기록(구 데이터) — 0 vs 0은 신호 없음 → 기존(앞에 붙임) 유지.
    runs = [
        _runc(0, 1000, "HH0307_030_0040_AC_v01"),
        _runc(1000, 1900, ""),
        _runc(1900, 3000, "HH0307_030_0050_AC_v01"),
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert segs[0].end_ms == 1900


def test_unreadable_block_between_same_key_unaffected():
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    runs = [
        _runc(0, 1000, "HH0307_030_0040_AC_v01", 500),
        _runc(1000, 1900, "", 9999),
        _runc(1900, 3000, "HH0307_030_0040_AC_v01", 10),
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_030_0040", 0, 3000)]


# ── 전환 블록 텍스트 근거 우선 (읽히는 런은 OCR이 결정) ─────────────────────
# 다음 씬 첫 런이 읽히긴 했지만 구분자 유실이 canonical화 한도를 넘어 파싱
# 불가면 판독불가 블록으로 취급됐고, 픽셀 비율이 앞 씬에 붙여 꼬리 오염이
# 생겼다(실기 0180 꼬리에 0190 첫 런 3프레임, 0170 꼬리에 0180 3프레임).
# squash 접두 일치(label_matches)로 어느 쪽 라벨인지 먼저 판정하고, 픽셀
# 비율은 텍스트가 전혀 없을 때만 쓴다.

def test_readable_unparseable_block_goes_to_matching_next():
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    # 실기 090_0180→0190: 들어컷 4646 vs 나가컷 2271(2배)이라 픽셀 비율로는
    # 앞 씬에 붙지만, 텍스트가 0190이므로 그 런부터 다음 씬이다.
    runs = [
        _runc(0, 5839, "HH0307_090_0180_AC_v01", 500),
        _runc(5839, 5964, "HH0307_0900190AC V01", 4646),
        _runc(5964, 6715, "HH0307_090_0190_AC_v01", 2271),
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_090_0180", 0, 5839), ("HH0307_090_0190", 5839, 6715)]


def test_readable_unparseable_block_matching_prev_stays():
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    # 블록 텍스트가 앞 라벨과 일치(앞 씬 꼬리 오독) — 들어컷이 세도 앞 씬 소속.
    runs = [
        _runc(0, 1000, "HH0307_030_0040_AC_v01", 500),
        _runc(1000, 1900, "HH0307_0300040AC", 4248),
        _runc(1900, 3000, "HH0307_030_0050_AC_v01", 47),
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_030_0040", 0, 1900), ("HH0307_030_0050", 1900, 3000)]


def test_mixed_block_cut_at_first_next_matching_text():
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    # 진짜 판독불가(페이드) 뒤에 파싱불가 오독이 오는 혼합 블록 — 컷은 다음
    # 라벨과 일치하는 첫 런의 시작이고, 그 앞 판독불가는 앞 씬에 남는다.
    runs = [
        _runc(0, 1000, "HH0307_030_0040_AC_v01", 500),
        _runc(1000, 1400, "", 300),
        _runc(1400, 1900, "HH0307_0300050ACv01", 4248),
        _runc(1900, 3000, "HH0307_030_0050_AC_v01", 47),
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_030_0040", 0, 1400), ("HH0307_030_0050", 1400, 3000)]


def test_leading_unparseable_head_joins_first_scene():
    from apps.server.domain.video_captions.scene_split import runs_to_segments
    # 선두 블록도 같은 원칙: 타이틀카드는 버리되, 첫 라벨과 일치하는 오독
    # 런부터는 첫 씬의 머리다.
    runs = [
        _runc(0, 400, "TITLECARD", 0),
        _runc(400, 525, "HH0307_0100010ACv01", 3000),
        _runc(525, 2000, "HH0307_010_0010_AC_v01", 100),
    ]
    segs = runs_to_segments(runs, RULE, "scene")
    assert [(s.label, s.start_ms, s.end_ms) for s in segs] == [
        ("HH0307_010_0010", 400, 2000)]


def test_tokenize_slash_misread_as_delimiter():
    # OCR이 "_"를 "/"로 어긋 읽는 상수적 오독(실기 HH0307_075/0080·120/0010) —
    # "/"를 구분자로 취급하면 오독 텍스트도 정상과 같은 토큰·키로 쪼개진다.
    assert tokenize("HH0307_120/0010_AC_v01", ["_", "-", "/"]) == [
        "HH0307", "120", "0010", "AC", "v01"]


def test_canonicalize_fixes_single_inserted_char():
    # OCR이 '_'를 숫자 '1'로 읽는 환각 삽입(실기 HH0307_07510040_AC → 075_0040
    # 오독) — 한 글자를 삭제해 템플릿에 '유일하게' 들어맞으면 확신 교정한다.
    # 진짜 다른 씬(0040 vs 0050)은 삭제로는 못 맞추므로 안전하다.
    # 같은 씬의 깨끗한 판독이 코퍼스에 존재하는 게 교정 근거다.
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    texts = (["HH0307_075_0030_AC_v01"] * 4
             + ["HH0307_075_0040_AC_v01"]
             + ["HH0307_07510040_AC_v01"])
    out = canonicalize_texts(texts, ["_", "-"])
    assert out[5] == "HH0307_075_0040_AC_v01"


def test_canonicalize_fixes_lookalike_digit_for_letter():
    """실기 FL102: 시퀀스 글자가 'O001'인데 숫자 '0001'로, 'I016'인데 '1016'으로
    읽힌다(299씬 중 60개). 코퍼스 다수 모양이 U1D3이면 그 자리는 '글자'라는
    뜻이므로 닮은꼴 숫자를 글자로 되돌린다 — 작품 포맷 하드코딩 없이 데이터
    자신의 템플릿이 근거다."""
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    texts = (["FL102 A001", "FL102 B010", "FL102 C013", "FL102 D021"] * 3
             + ["FL102 0001", "FL102 1016"])
    out = canonicalize_texts(texts, ["_", " ", "-", "/"])
    assert out[-2] == "FL102_O001"
    assert out[-1] == "FL102_I016"


def test_canonicalize_lookalike_merges_split_scene():
    """같은 씬이 'I016'과 '1016' 두 갈래로 읽히면 지금은 세그먼트가 둘로
    쪼개진다(실기 7건). 교정이 그룹핑 '전에' 돌아 같은 키로 합쳐져야 한다."""
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    delims = ["_", " ", "-", "/"]
    texts = ["FL102 A001", "FL102 B010", "FL102 C013",
             "FL102 I016", "FL102 1016"]
    out = canonicalize_texts(texts, delims)
    # 정상 판독은 원문 그대로, 오독은 교정 — 둘이 같은 토큰(=같은 키)이어야
    # 그룹핑에서 한 씬으로 합쳐진다.
    assert tokenize(out[3], delims) == tokenize(out[4], delims) \
        == ["FL102", "I016"]


def test_canonicalize_lookalike_never_invents_value_in_fixed_field():
    """고정 필드(코퍼스 값이 하나뿐)에는 새 값을 지어내지 않는다 — 'HH030Z'를
    닮은꼴로 밀면 'HH0302'가 되지만 이 코퍼스의 쇼 번호는 언제나 HH0307이다.
    변하는 씬 ID 필드와 달리 여기선 닮은꼴 교체가 근거가 되지 못한다."""
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    texts = ["HH0307_010_0010_AC_v01"] * 5 + ["HH030Z_140_0010_AC_v01"]
    assert canonicalize_texts(texts, ["_", "-"])[5] == texts[5]


def test_canonicalize_lookalike_restores_known_constant():
    """반대로 닮은꼴 교정 결과가 그 고정 필드의 '아는 값'과 일치하면 교정한다
    — 'FLI02'는 코퍼스에 실재하는 'FL102'로 되돌아가므로 지어내기가 아니다."""
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    texts = (["FL102 A001", "FL102 B010", "FL102 C013"] * 2
             + ["FLI02 D021"])
    assert canonicalize_texts(texts, ["_", " ", "-", "/"])[-1] == "FL102_D021"


def test_canonicalize_keeps_digits_when_corpus_says_digits():
    """진짜 숫자 필드인 작품은 건드리지 않는다 — 다수 모양이 D4면 그 자리는
    숫자다. 템플릿이 작품마다 데이터에서 나오므로 자동으로 안전하다."""
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    texts = ["FL102 0001", "FL102 0002", "FL102 0003", "FL102 0004"]
    assert canonicalize_texts(texts, ["_", " ", "-", "/"]) == texts


def test_canonicalize_skips_non_lookalike_mismatch():
    """닮은꼴 쌍이 아닌 문자는 억지로 바꾸지 않는다 — '4'는 어떤 글자로도
    읽히지 않으므로 원문 그대로 둔다(억지 교정 금지 원칙)."""
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    texts = (["FL102 A001", "FL102 B010", "FL102 C013"] * 2
             + ["FL102 4001"])
    out = canonicalize_texts(texts, ["_", " ", "-", "/"])
    assert out[-1] == "FL102 4001"


def test_canonicalize_rejects_ambiguous_deletion():
    # 삭제 위치에 따라 서로 다른 결과가 나오면(모호) 교정하지 않는다.
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    texts = (["HH0307_075_0030_AC_v01"] * 5
             + ["HH0307_07512340_AC_v01"])  # 8자리: 어느 숫자를 지워도 7자리
    out = canonicalize_texts(texts, ["_", "-"])   # 가 되지만 결과가 제각각 → 유지
    assert out[5] == "HH0307_07512340_AC_v01"


def test_canonicalize_disambiguates_insertion_by_neighbor_context():
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    # '_'→'1' 삽입 오독 'HH03041130_0040': 한 글자 삭제 후보가 코퍼스의
    # 130_0040('1' 삭제)·110_0040('3' 삭제) 둘 다와 일치해 유일성 판정이
    # 죽는다(실기 22:28 블록이 별개 씬으로 남은 원인). 이웃 런이 130_0040을
    # 깨끗하게 읽었으면 그쪽으로 판별한다(슬레이트 지속성 — 오독의 정답은
    # 대개 바로 이웃 런에 있다).
    texts = [
        "HH0304_110_0040_AC_v01",   # 먼 곳의 정상 판독(코퍼스 오염원)
        "HH0304_090_0010_AC_v01",
        "HH0304_090_0020_AC_v01",
        "HH0304_090_0030_AC_v01",
        "HH0304_090_0040_AC_v01",
        "HH0304_130_0030_AC_v01",
        "HH0304_130_0040_AC_v01",   # 이웃 정상 판독
        "HH03041130_0040_AC_v01",   # 삽입 오독
        "HH0304_130_0050_AC_v01",
    ]
    out = canonicalize_texts(texts, ["_", "-", "/"])
    assert out[7] == "HH0304_130_0040_AC_v01"
    assert out[0] == "HH0304_110_0040_AC_v01"  # 정상 판독은 그대로


def test_canonicalize_insertion_stays_ambiguous_without_neighbor():
    from apps.server.domain.video_captions.scene_split import canonicalize_texts
    # 이웃 창 안에 판별 근거가 없으면 교정하지 않는다 — 억지 교정 금지.
    texts = (["HH0304_110_0040_AC_v01"] + ["HH0304_090_0010_AC_v01"] * 8
             + ["HH0304_130_0040_AC_v01"] + ["HH0304_090_0020_AC_v01"] * 8
             + ["HH03041130_0040_AC_v01"] + ["HH0304_090_0030_AC_v01"] * 8)
    out = canonicalize_texts(texts, ["_", "-", "/"])
    assert out[18] == "HH03041130_0040_AC_v01"


def test_read_slate_line_rescaled_recovers_native_fail(monkeypatch, tmp_path):
    """축소 재판독 — 검출기가 원본 해상도의 흐릿한 경계 프레임에서 통째로
    실패하는 케이스(실기 040_0200 전환 17프레임: 원본 0/17, 0.6× 축소 17/17).
    가짜 엔진이 '작은 이미지에서만 읽히는' 상황을 재현한다."""
    from PIL import Image

    from apps.server.domain.video_captions import slate_ocr
    png = tmp_path / "b.png"
    Image.new("RGB", (800, 200)).save(png)

    def fake_engine(path):
        with Image.open(path) as im:
            if im.width >= 500:
                return None, 0.0
        return ([[[[5, 5], [300, 5], [300, 30], [5, 30]],
                  "HH0304_040_0210_AC_v01", 0.9]], 0.0)

    monkeypatch.setattr(slate_ocr, "_get_engine", lambda: fake_engine)
    assert slate_ocr.read_slate_line(png, ["_", "-"], top_frac=1.0) == ""
    assert slate_ocr.read_slate_line_rescaled(png, ["_", "-"], top_frac=1.0) \
        == "HH0304_040_0210_AC_v01"
    # 축소 임시 파일은 남지 않는다.
    assert list(tmp_path.glob("*_rs*")) == []


def test_read_slate_line_rescaled_tries_upscale(monkeypatch, tmp_path):
    """확대 재판독 — 작은 크롭에서는 필드 구분자가 뭉개져 두 필드가 붙어 읽히고
    (실기 FL102 720s 원본 'FL102J002'), 확대하면 갈라진다('FL102 J002').
    축소(0.6×)로도 안 갈라지는 프레임을 확대가 받아낸다."""
    from PIL import Image

    from apps.server.domain.video_captions import slate_ocr
    png = tmp_path / "s.png"
    Image.new("RGB", (232, 62)).save(png)

    def fake_engine(path):
        with Image.open(path) as im:
            text = "FL102 J002" if im.width >= 400 else "FL102J002"
        return ([[[[5, 5], [200, 5], [200, 40], [5, 40]], text, 0.9]], 0.0)

    monkeypatch.setattr(slate_ocr, "_get_engine", lambda: fake_engine)
    assert slate_ocr.read_slate_line(png, ["_", "-"], top_frac=1.0) == ""
    assert slate_ocr.read_slate_line_rescaled(png, ["_", "-"], top_frac=1.0) \
        == "FL102 J002"
    assert list(tmp_path.glob("*_rs*")) == []


def test_read_slate_line_rescaled_skips_pointless_upscale(monkeypatch, tmp_path):
    """검출 상한(960)을 넘겨 확대하면 검출기가 도로 줄여 결과는 같고 시간만 든다
    — 큰 이미지는 확대 시도를 건너뛴다(수천 프레임 스캔의 낭비 방지)."""
    from PIL import Image

    from apps.server.domain.video_captions import slate_ocr
    png = tmp_path / "big.png"
    Image.new("RGB", (900, 200)).save(png)
    widths = []

    def fake_engine(path):
        with Image.open(path) as im:
            widths.append(im.width)
        return None, 0.0

    monkeypatch.setattr(slate_ocr, "_get_engine", lambda: fake_engine)
    assert slate_ocr.read_slate_line_rescaled(png, ["_", "-"], top_frac=1.0) == ""
    assert widths == [540]  # 0.6배만 — 1800px 확대는 시도하지 않는다
