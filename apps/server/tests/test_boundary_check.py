"""경계 오류(혼입) 검사 순수 로직 단위 테스트 — ffmpeg/OCR 불필요.

프레임 인덱스 수식이 익스포트 컷(cut_segment)과 정확히 일치해야 OCR이 실제로
잘리는 프레임을 읽는다. 분류 로직은 이웃 슬레이트 혼입만 오류로 잡고, 자기
라벨·판독불가('')는 오류로 표시하지 않는다."""
from __future__ import annotations

import math

from apps.server.domain.video_captions.fingerprint import frame_boundary_ms
from apps.server.domain.video_captions.pipeline import (_boundary_head_tail_ms,
                                                        _classify_boundary)

# 실기 _0070 클립 수치(측정 fps) — 문서화된 기대 프레임 인덱스와 대조한다.
_FPS = 23.976215594130576
_SEG = {"label": "HH0304_0070", "start_ms": 28924, "end_ms": 36849}


def test_head_idx_and_frame_count_match_export():
    # 익스포트가 쓰는 것과 동일한 수식 — head_idx 694, N 190, last_idx 883.
    head_idx = math.ceil(_SEG["start_ms"] * _FPS / 1000.0 - 1e-6)
    n = round((_SEG["end_ms"] - _SEG["start_ms"]) * _FPS / 1000.0)
    last_idx = head_idx + n - 1
    assert head_idx == 694
    assert n == 190
    assert last_idx == 883


def test_boundary_head_tail_ms_lands_on_last_frame():
    head_ms, tail_ms = _boundary_head_tail_ms(_SEG, _FPS)
    # 머리는 start_ms 그대로(=첫 프레임의 frame_boundary_ms).
    assert head_ms == 28924
    # 꼬리는 마지막 프레임(883)의 -ss 경계 시각과 정확히 일치해야 한다.
    assert tail_ms == frame_boundary_ms(883, _FPS)


def test_head_tail_ms_zero_length_segment():
    # end==start면 N=round(0)=0 → last_idx = head_idx - 1(머리 직전). 크래시 없이
    # 유한한 값이면 충분하다(경계 검사 자체는 판독으로 필터된다).
    head_ms, tail_ms = _boundary_head_tail_ms(
        {"label": "X", "start_ms": 1000, "end_ms": 1000}, _FPS)
    assert head_ms == 1000
    assert isinstance(tail_ms, int)


# 실제 라벨(이웃 관계): prev=0220, own=0230, next=0240.
_PREV = "HH0304_020_0220"
_OWN = "HH0304_020_0230"
_NEXT = "HH0304_020_0240"


def test_classify_flags_dissolve_wipe_overlap_both_slates():
    # 디졸브/와이프: 경계 프레임에 두 슬레이트가 '함께' 보인다. 머리에 이전(0220),
    # 꼬리에 다음(0240)이 자기(0230)와 겹쳐 보이면 양쪽 혼입으로 잡아야 한다
    # (사용자 요구: 오버랩 씬을 오류 필터에 띄운다).
    head_text = "HH0304_020_0230_AC_v01 HH0304_020_0220_AC_v01"  # own + prev
    tail_text = "HH0304_020_0240_AC_v01 HH0304_020_0230_AC_v01"  # next + own
    head_bad, tail_bad = _classify_boundary(head_text, tail_text, _OWN, _PREV, _NEXT)
    assert head_bad is True
    assert tail_bad is True


def test_classify_robust_to_underscore_read_as_space():
    # OCR이 밑줄을 공백으로 읽어도(HH0304 020 0220) squash로 같은 키가 되어 잡힌다.
    head_bad, tail_bad = _classify_boundary(
        "HH0304 020 0230 AC v01 HH0304 020 0220 AC v01", "", _OWN, _PREV, _NEXT)
    assert head_bad is True
    assert tail_bad is False


def test_classify_flags_clear_misplacement_only_neighbor():
    # 경계가 크게 어긋나 머리 프레임에 이전 라벨만 보이는 오배치도 잡는다.
    head_bad, _ = _classify_boundary(
        "HH0304_020_0220_AC_v01", "", _OWN, _PREV, _NEXT)
    assert head_bad is True


def test_classify_no_flag_on_own_only():
    # 하드컷: 경계 프레임에 자기 슬레이트만 보이면 정상(이웃 슬레이트 없음).
    own = "HH0304_020_0230_AC_v01"
    head_bad, tail_bad = _classify_boundary(own, own, _OWN, _PREV, _NEXT)
    assert head_bad is False
    assert tail_bad is False


def test_classify_ignores_truncated_neighbor_inside_own_slate():
    # 실기 EASA05: 이웃 라벨이 접두 유실 오독("18A_S01"·"S12_Panel10")이면 그
    # 문자열은 내 경계 프레임의 '내 슬레이트' 판독("Seq18A_S01-Panel5") 안에
    # 항상 들어 있다 — 이웃 슬레이트가 보인 게 아니라 이웃 이름이 내 이름의
    # 조각일 뿐이다. 내 라벨을 걷어낸 나머지에서 찾아야 한다.
    _, tail_bad = _classify_boundary(
        "", "Seq18A_S01 - Panel 5", "Seq18A_S01", None, "18A_S01")
    assert tail_bad is False
    _, tail_bad2 = _classify_boundary(
        "", "Seq07B_S12 - Panel 10", "Seq07B_S12", None, "S12_Panel10")
    assert tail_bad2 is False


def test_classify_ignores_neighbor_that_squashes_to_nothing():
    # 완전 깨진 이웃 라벨("一·_,")은 squash하면 빈 문자열 — 빈 문자열은 모든
    # 텍스트에 '포함'되므로 그 이웃을 가진 씬이 무조건 혼입 판정됐다(실기 EASA05).
    head_bad, tail_bad = _classify_boundary(
        "Seq10A_S08 - Panel 3", "Seq10A_S08 - Panel 9",
        "Seq10A_S08", "一·_,", "一·_,")
    assert head_bad is False
    assert tail_bad is False


def test_classify_no_flag_on_empty():
    # 판독불가('')는 미지 — 오류로 표시하지 않는다.
    head_bad, tail_bad = _classify_boundary("", "", _OWN, _PREV, _NEXT)
    assert head_bad is False
    assert tail_bad is False


def test_classify_no_flag_when_no_neighbor():
    # 끝 세그먼트는 next=None — 꼬리 혼입 판정 불가.
    head_text = "HH0304_020_0230_AC_v01"  # 자기만
    head_bad, tail_bad = _classify_boundary(head_text, head_text, _OWN, _PREV, None)
    assert head_bad is False
    assert tail_bad is False
