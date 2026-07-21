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


def frame_mid_ms(idx: int, fps: float) -> int:
    """프레임 idx의 표시구간 '중앙' 시각(ms) — 경계·OCR 추출 시각 규약.

    프레임 PTS(idx/fps) 정확값을 쓰면 입력측 -ss 스냅다운("그 시각 이하 가장
    가까운 프레임")이 반올림 오차 1ms로도 직전 프레임으로 미끄러진다. 중앙이면
    ±0.5ms 오차에도 항상 frame idx에 안착하고, 세그 프레임 수
    N=round((end-start)×fps/1000)도 정확히 (end_idx-start_idx)로 떨어진다
    (cut_segment의 -frames:v 규약과 일치 — NTSC 장구간 누적 오차 없음)."""
    return round((idx + 0.5) * 1000.0 / fps)
