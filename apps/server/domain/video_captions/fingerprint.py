"""텍스트 이진화 지문 컷 감지 (순수 계산 + 프레임 PNG 로드).

원리: 슬레이트 텍스트는 한 씬 내내 동일하므로, 슬레이트 구역의 어두운 픽셀
패턴(지문)은 씬 안에서 거의 불변이고 컷에서만 크게 변한다. 인접 프레임 지문의
해밍거리(다른 픽셀 수)로 컷을 찾으면 경계가 프레임 정확하다 — 간격 OCR 샘플링
+이진탐색 정밀화 2단계를 컷 감지 1단계로 대체한다. 실측(2026-07-21, 90초/2158
프레임): 지문+diff 0.4ms/프레임(OCR의 100배), 실제 씬 전부 감지.

가짜 컷(반투명 바 뒤 애니 움직임 등)은 허용한다 — 컷 사이 런마다 OCR해 같은
라벨의 연속 런을 병합하면 자연히 흡수된다(scene_split.runs_to_segments).
numpy/PIL은 RapidOCR 전이의존이라 이미 서버 번들에 있다(새 의존성 아님) —
번들 무관 경로 보호를 위해 지연 import한다(slate_ocr와 같은 규약).
"""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import Callable

# 전부 실측 검증값(2026-07-21, HZBN307). BIN_THRESHOLD: 그레이 < 이 값 =
# 텍스트 픽셀. MIN_CUT_DIFF: 씬 내 인접 diff는 대부분 0(중앙값 0)이라 절대
# 하한이 주 방어선. MED_MULT: 노이즈 큰 소스는 중앙값 배수가 임계를 올린다.
# MERGE_GAP: 디졸브가 여러 프레임에 걸쳐 임계를 넘으면 첫 프레임만 컷으로.
BIN_THRESHOLD = 90
MIN_CUT_DIFF = 15
MED_MULT = 3
MERGE_GAP = 2


def load_fingerprint(path: str | Path):
    """프레임 1장 → 이진 지문(어두운 픽셀=1). numpy uint8 2차원 배열(H, W)."""
    import numpy as np
    from PIL import Image
    with Image.open(path) as im:
        arr = np.asarray(im.convert("L"), dtype=np.uint8)
    return (arr < BIN_THRESHOLD).astype(np.uint8)


def adjacent_diffs(paths: list[Path],
                   check_cancel: Callable[[], None] | None = None) -> list[int]:
    """인접 프레임 지문의 해밍거리 목록 — diffs[i] = frame i+1 vs frame i.

    직전 지문 하나만 유지한다(스트리밍) — 25분 영상 3.6만 프레임도 O(1) 메모리.
    check_cancel은 주기적으로 호출된다 — 취소 시 콜백이 예외를 던져 중단한다
    (지문 패스는 수십 초 걸릴 수 있어 취소 신호를 물어야 한다). 크기가 다른
    프레임(추출 이상)은 전체 픽셀 수를 diff로 쳐서 컷으로 보이게 한다.
    """
    import numpy as np
    diffs: list[int] = []
    prev = None
    for i, path in enumerate(paths):
        if check_cancel is not None and i % 256 == 0:
            check_cancel()
        cur = load_fingerprint(path)
        if prev is not None:
            diffs.append(int(np.sum(cur != prev)) if cur.shape == prev.shape
                         else int(cur.size))
        prev = cur
    return diffs


# 느린 페이드 감지 윈도우(프레임). 시퀀스 전환 디졸브는 4~12프레임에 걸쳐
# 서서히 바뀌어 인접 diff가 임계를 한 번도 못 넘는다(실기: 030_0330 씬이 통째로
# 다음 런에 흡수, 130→140 혼입). i vs i-6 누적 diff는 텍스트가 통째로 바뀌므로
# 크게 튄다 — 길수록 민감하지만 플래토가 넓어져 위치 정밀도가 떨어진다.
FADE_WINDOW = 6


def diff_series(paths: list[Path], window: int,
                check_cancel: Callable[[], None] | None = None,
                ) -> tuple[list[int], list[int]]:
    """인접 해밍거리와 윈도우 해밍거리(frame k+window vs frame k)를 한 패스로.

    adjacent_diffs와 같은 스트리밍(최근 window개 링버퍼, O(window) 메모리).
    반환: (adj, win) — adj[i]=frame i+1 vs i, win[k]=frame k+window vs k."""
    import numpy as np
    adj: list[int] = []
    win: list[int] = []
    ring: list = []
    prev = None
    for i, path in enumerate(paths):
        if check_cancel is not None and i % 256 == 0:
            check_cancel()
        cur = load_fingerprint(path)
        if prev is not None:
            adj.append(int(np.sum(cur != prev)) if cur.shape == prev.shape
                       else int(cur.size))
        if len(ring) == window:
            old = ring[0]
            win.append(int(np.sum(cur != old)) if cur.shape == old.shape
                       else int(cur.size))
        ring.append(cur)
        if len(ring) > window:
            ring.pop(0)
        prev = cur
    return adj, win


def detect_cuts_with_fades(adjacent: list[int], windowed: list[int],
                           window: int) -> list[int]:
    """하드컷(인접 diff) + 느린 페이드 컷(윈도우 diff 플래토) 통합 감지.

    윈도우 diff가 임계를 넘는 연속 구간(플래토)은 그 안 어딘가의 전환을 뜻한다.
    하드컷도 자기 주변에 플래토를 만들므로 이미 잡힌 컷 근처 플래토는 버리고,
    남은 플래토(=인접 diff만으로는 안 보이는 느린 페이드)에 컷 1개를 삽입한다 —
    위치는 플래토 안 인접 diff 최대 지점(가장 빠른 변화). 가짜 컷 추가는
    무해하다(동일 라벨 병합이 흡수) — 컷 누락만이 씬을 통째로 잃게 한다."""
    cuts = detect_cuts(adjacent)
    if window <= 1 or not windowed:
        return cuts
    threshold = max(MIN_CUT_DIFF, statistics.median(windowed) * MED_MULT)
    fade: list[int] = []
    k = 0
    while k < len(windowed):
        if windowed[k] <= threshold:
            k += 1
            continue
        j = k
        while j + 1 < len(windowed) and windowed[j + 1] > threshold:
            j += 1
        lo_f, hi_f = k, j + window  # 플래토가 걸친 프레임 범위
        if not any(lo_f - MERGE_GAP <= c <= hi_f + MERGE_GAP for c in cuts):
            lo_i = max(0, lo_f)
            hi_i = min(len(adjacent) - 1, hi_f - 1)
            if lo_i <= hi_i:
                best = max(range(lo_i, hi_i + 1),
                           key=lambda i: (adjacent[i], -i))
                fade.append(best + 1)
        k = j + 1
    return sorted(set(cuts + fade))


def detect_cuts(diffs: list[int]) -> list[int]:
    """컷 프레임 인덱스(새 런의 첫 프레임, 0-based) 목록.

    임계 = max(MIN_CUT_DIFF, 중앙값×MED_MULT). MERGE_GAP 프레임 이내로 붙은
    후보는 첫 것만 남긴다. 가짜 컷은 하류(동일 라벨 병합)가 흡수하므로 여기서는
    놓침(미검출)이 없는 쪽으로 보수적이어야 한다.
    """
    if not diffs:
        return []
    threshold = max(MIN_CUT_DIFF, statistics.median(diffs) * MED_MULT)
    cuts: list[int] = []
    for i, diff in enumerate(diffs):
        if diff > threshold and (not cuts or (i + 1) - cuts[-1] > MERGE_GAP):
            cuts.append(i + 1)
    return cuts


def frame_runs(cuts: list[int], n_frames: int) -> list[tuple[int, int]]:
    """컷 목록 → [start, end) 프레임 인덱스 런. 선두 런(0~첫 컷) 포함,
    범위 밖(≤0, ≥n_frames) 컷은 무시한다."""
    if n_frames <= 0:
        return []
    starts = [0] + [c for c in cuts if 0 < c < n_frames]
    return list(zip(starts, starts[1:] + [n_frames]))


def stable_frame(diffs: list[int], start: int, end: int) -> int:
    """런 [start, end)에서 판독할 프레임 — 인접 diff 합이 가장 작은 '정지' 프레임.

    런 중간을 맹목적으로 읽으면 가짜 컷을 만든 흐릿한 프레임(디졸브·모션 잔상)
    을 읽게 돼 오독률이 치솟는다(실기 11.5%). 앞뒤 diff가 작은 프레임은 화면이
    정지해 있어 텍스트가 선명하다. 컷 프레임(런 양끝)은 컷 스파이크를 지므로
    자연히 피해진다. 동점이면 런 중앙에 가까운(그다음 낮은 인덱스) 프레임."""
    center2 = start + end - 1  # 중앙×2 (정수 비교용)

    def score(i: int) -> tuple:
        motion_in = diffs[i - 1] if 0 <= i - 1 < len(diffs) else 0
        motion_out = diffs[i] if i < len(diffs) else 0
        return (motion_in + motion_out, abs(2 * i - center2), i)

    return min(range(start, end), key=score)


def frame_boundary_ms(idx: int, fps: float) -> int:
    """frame idx가 -ss로 정확히 잡히는 경계 시각(ms) — 직전 프레임과의 갭 중앙.

    실측(번들 8.1.2, select 절대 프레임번호 대조): 입력측 -ss X는 "X 시각에
    보이는 프레임"이 아니라 **PTS ≥ X인 첫 프레임**을 내보낸다(스냅업). 그래서
    프레임 표시구간 '중앙'((idx+0.5)/fps)을 경계로 쓰면 -ss가 다음 프레임부터
    시작해 모든 클립이 1프레임 늦고, 개수는 정확하니 꼬리에 다음 씬 1프레임이
    섞인다(실기 시퀀스 16클립 전수 재현 — 머리 2프레임째 시작+꼬리 혼입).
    갭 중앙((idx-0.5)/fps)은 PTS(idx-1) < X < PTS(idx)라 ±0.5ms 반올림에도
    정확히 frame idx에 안착하고(여유 ~20ms@24fps), 세그 프레임 수
    N=round((end-start)×fps/1000)도 정확히 (end_idx-start_idx)로 떨어진다
    (cut_segment의 -frames:v 규약과 일치 — NTSC 장구간 누적 오차 없음).
    idx=0은 음수가 되므로 0으로 클램프한다(-ss 0 → 첫 프레임)."""
    return max(0, round((idx - 0.5) * 1000.0 / fps))
