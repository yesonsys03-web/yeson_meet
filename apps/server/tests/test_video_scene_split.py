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
