"""씬 지문(전 프레임 컷 감지) 스캔 러너 — 컷 검출·런 OCR·경계 정렬.

pipeline.py에서 분리(2026-07-29 리팩토링, 동작 변경 0). 간격 스캔은 scene_scan,
규칙 확정 시의 세그먼트 계산(build_fingerprint_segments)도 여기 산다.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

from .ffmpeg import (
    build_scan_source, extract_fingerprint_frames, extract_frame,
    extract_frames_at, extract_thumbnails, locate_ffmpeg, video_fps,
)
from .fingerprint import (
    FADE_WINDOW, detect_cuts_with_fades, diff_series, frame_boundary_ms,
    frame_runs, load_fingerprint, stable_frame,
)
from .job_store import _TOP_BAND_DEFAULT, job_dir, load_ocr_region, save_scenes
from .job_tasks import (
    _BURN_SEMAPHORE, _bump_generation, _current_generation, _refine_workers,
)
from .scene_scan import _DEFAULT_DELIMS
from .scene_split import (
    SceneRun, SlateRule, canonicalize_texts, runs_to_segments, tokenize,
)
from .slate_ocr import read_slate_line, read_slate_line_rescaled
from .transcribe import StaleRunCancelled

# 로거 이름은 분리 전과 동일하게 유지한다(job_tasks 참조).
logger = logging.getLogger("yeson.video.pipeline")


# 판독 카운터(ocr_done)가 아직 없는 앞 구간의 단계 이름 — 프론트가 그대로
# 표시한다("프레임 추출 중…"보다 어디쯤인지 알 수 있게).
STAGE_CROP = "스캔용 크롭본 만드는 중"
STAGE_FRAMES = "프레임 추출 중"
STAGE_THUMBS = "썸네일 만드는 중"
STAGE_CUTS = "컷 감지 중"

# 살아있음 신호를 갱신하는 주기(초). 프론트 정체 판정이 200초라 넉넉히 짧게.
_EXTRACT_TICK_S = 3.0


def _extract_tick(scan_src: Path, frames_dir: Path, thumbs_dir: Path) -> int:
    """추출 구간의 '살아있음' 신호 — 산출물이 실제로 늘어야 값이 오른다.

    단순 시계였다면 진짜로 멎은 ffmpeg도 살아있는 것처럼 보여 정체 감지가
    죽는다. 크롭본 크기(KB)와 추출된 파일 수를 더해, 일이 진행될 때만 값이
    바뀌게 한다(수가 아니라 '변했는가'만 쓴다).
    """
    tick = 0
    try:
        if scan_src.exists():
            tick += scan_src.stat().st_size // 1024
    except OSError:
        pass
    for d, pat in ((frames_dir, "f_*.png"), (thumbs_dir, "thumb_*.jpg")):
        try:
            if d.exists():
                tick += sum(1 for _ in d.glob(pat))
        except OSError:
            pass
    return tick


# 지문 클러스터 흡수 캡 — 프론트 '오독 갈라짐 정리'(FLANK_MAX_MS)와 동일 5초.
# 이보다 긴 블록은 진짜 비단조 씬일 수 있어 보존한다.
_FP_FLANK_MAX_MS = 5000


def build_fingerprint_segments(runs_raw: list[dict], rule_dict: dict) -> dict:
    """지문 런 + 규칙 → 양 모드 세그먼트(순수 함수, build_scene_data의 지문판).
    경계는 이미 프레임 정확한 컷이라 min_ms 흡수·중앙정렬·정밀화가 없다 —
    규칙은 런들을 같은 키로 병합하는 데만 쓴다.

    그룹핑 전에 런 텍스트를 canonical화하고(구분자 유실 오독 → 같은 키로 병합),
    교정 못 한 오독은 클러스터 흡수(≤5s)로 걷어낸다 — 지문은 런 중간(흐릿한
    프레임 근처)을 읽어 오독률이 높아(실기 11.5%) 이 두 단계가 없으면 오독
    하나가 세그먼트 하나로 굳는다(실기 씬 806→481·시퀀스 322→19)."""
    rule = SlateRule(
        delimiters=rule_dict.get("delimiters", ["_", " ", "-"]),
        seq_tokens=rule_dict["seq_tokens"],
        scene_tokens=rule_dict.get("scene_tokens", []),
    )
    texts = canonicalize_texts([r.get("text", "") for r in runs_raw],
                               rule.delimiters,
                               example=rule_dict.get("example"))
    runs = [SceneRun(start_ms=r["start_ms"], end_ms=r["end_ms"], text=t,
                     cut_diff=r.get("cut_diff", 0))
            for r, t in zip(runs_raw, texts)]
    return {
        "segments_scene": [
            {"label": s.label, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in runs_to_segments(runs, rule, "scene",
                                      absorb_flanked_ms=_FP_FLANK_MAX_MS)],
        "segments_sequence": [
            {"label": s.label, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in runs_to_segments(runs, rule, "sequence",
                                      absorb_flanked_ms=_FP_FLANK_MAX_MS)],
    }


# 지문 스캔에서 구역 미지정 시 상단 밴드를 크롭으로 쓴다(_TOP_BAND_DEFAULT와
# 같은 비율) — 지문은 크롭이 필수라(전체 프레임이면 애니 전체의 변화가 다 컷으로
# 잡힌다) 기존 '상단 밴드 가정'을 크롭으로 실체화한 것. 썸네일 간격은 간격
# 스캔의 상한과 동일한 2초 고정(지문에는 샘플 간격 개념이 없다).
_FP_FALLBACK_REGION = (0.0, 0.0, 1.0, _TOP_BAND_DEFAULT)
_FP_THUMB_INTERVAL_S = 2.0


def _text_side(text: str | None, prev_text: str, next_text: str,
               delimiters: list[str]) -> str | None:
    """판독 텍스트가 이전/다음 어느 쪽 슬레이트인지 — squash 접두 상호 일치
    (오독·꼬리 잘림 내성). 판독불가·양쪽 다 일치(공통 접두만 읽힘)면 None."""
    def sq(s: str) -> str:
        # 소문자화 — OCR이 v01/V01을 오가며 읽어(실기) 대소문자 구분 비교는
        # '어느 쪽도 아님'을 만들고 OCR 권위를 무력화한다.
        return "".join("".join(t.split()) for t in tokenize(s, delimiters)).lower()

    x = sq(text or "")
    if not x:
        return None
    prev_sq, next_sq = sq(prev_text), sq(next_text)
    match_prev = x.startswith(prev_sq) or prev_sq.startswith(x)
    match_next = x.startswith(next_sq) or next_sq.startswith(x)
    if match_prev == match_next:
        return None
    return "next" if match_next else "prev"


def _clamp_fp_move(ocr_side, cur: int, target: int) -> int:
    """지문 유사도 이동을 OCR 가독성으로 캡 — 읽히는 프레임의 소속은 OCR이 권위.

    유사도 정렬은 판독불가 페이드에는 옳지만, 새 슬레이트가 옛 그림 위에 일찍
    떠오르는 반대 극성 디졸브에서는 OCR로 이미 '다음'이 읽히는 프레임까지 이전
    쪽으로 밀어버린다(실기 090_0180 꼬리에 0190). 오른쪽 이동은 prev로 재배정될
    구간에서 next로 읽히는 첫 프레임에서 멈추고, 왼쪽 이동은 next로 재배정될
    구간에서 prev로 읽히는 프레임 뒤로 물린다."""
    if target > cur:
        for frame in range(cur, target):
            if ocr_side(frame) == "next":
                return frame
        return target
    if target < cur:
        best = target
        for frame in range(target, cur):
            if ocr_side(frame) == "prev":
                best = frame + 1
        return best
    return cur


def _align_cut(read_at, cut: int, prev_text: str, next_text: str,
               lo: int, hi: int, delimiters: list[str],
               max_probe: int = 24) -> int:
    """지문 컷을 '다음 슬레이트가 읽히는 첫 프레임'으로 정렬한다.

    지문 컷(픽셀 전환 지점)은 디졸브에서 슬레이트 '가독' 전환과 어긋난다
    (실기: 130→140 컷 6프레임 지각 — 클립 꼬리가 다음 시퀀스로 읽힘,
    030→040은 1프레임 조기). read_at(frame)->text로 컷 주변을 읽어, 컷 직전
    프레임이 이미 다음으로 읽히면 왼쪽으로, 컷 프레임이 아직 이전으로 읽히면
    오른쪽으로 걷는다. 판정은 squash 접두 상호 일치(오독·꼬리 잘림 내성) —
    양쪽 다 일치(공통 접두만 읽힘)하거나 판독불가면 근거가 없으므로 멈춘다
    (보수적 — 원래 컷 유지가 기본). lo/hi는 이웃 런 침범 방지 경계(exclusive)."""
    def side(frame: int) -> str | None:
        return _text_side(read_at(frame), prev_text, next_text, delimiters)

    before = side(cut - 1)
    if before == "next" or (before is None and side(cut) == "next"):
        # 컷 지각 — 다음 슬레이트가 읽히는 가장 이른 프레임까지 왼쪽으로.
        # 컷 직전 프레임이 판독불가여도 컷 프레임이 '다음'으로 읽히면 더
        # 왼쪽을 살핀다 — 슬레이트만 바뀌고 그림이 이어지는 무컷 전환에서
        # 경계 프레임 판독 깜박임 하나가 걷기 시작을 막아 꼬리 혼입
        # ~22프레임이 남았다(실기 040_0200). 이동은 '다음'으로 확인된 가장
        # 깊은 프레임까지만: 판독불가는 건너뛰되 이동 근거가 되지 않고(그
        # 구간의 귀속은 원래 컷 쪽 유지), '이전'이 읽히면 멈춘다.
        new = cut - 1 if before == "next" else cut
        frame, probes = cut - 2, 0
        while frame > lo and probes < max_probe:
            s = side(frame)
            if s == "prev":
                break
            if s == "next":
                new = frame
            frame -= 1
            probes += 1
        return new
    if side(cut) == "prev":
        # 컷 조기 — 이전 슬레이트가 끝나는 지점(다음이 읽히는 첫 프레임)까지.
        # 직전 프레임(before)이 판독불가여도 컷 프레임이 '이전'으로 읽히면
        # 걷는다 — before까지 요구하던 가드가 디졸브 경계의 ±1프레임 잔존
        # 4건을 남겼다(실기 468클립 검사). 컷 프레임이 판독불가면 걷지 않는다
        # — 무판독 구간의 귀속은 지문(①_fp_align·블록 귀속)의 몫이라, 여기서
        # 걷어 다음-읽힘 프레임까지 밀면 지문이 next로 귀속한 구간을 도로
        # 빼앗는다(통합 테스트로 잠금).
        frame, probes = cut + 1, 0
        while frame < hi and probes < max_probe:
            s = side(frame)
            if s == "next":
                return frame
            # 판독불가/양쪽공통 프레임은 근거가 없을 뿐 — 걷기를 끊지 않고
            # 건너뛴다(디졸브 블러 1프레임이 걷기를 끊어 다음이 읽히는데도
            # 컷이 안 옮겨지던 실기 머리 혼입 090_0060·020_0250). '다음'을
            # 못 찾고 끝나면 컷 유지라 건너뛴 프레임은 이전 쪽에 남는다.
            frame += 1
            probes += 1
    return cut


def _fp_align(fp_at, cut: int, ref_prev, ref_next, lo: int, hi: int,
              window: int = 8) -> int | None:
    """지문 유사도 플립 지점으로 컷을 정렬 — 판독불가 페이드 프레임의 귀속.

    디졸브의 페이드 프레임은 OCR로 못 읽지만 픽셀은 아직 이전 슬레이트의
    잔상이다(실기 030_0190→0200: 페이드 2프레임의 지문 거리 4823 vs 8044로
    이전 쪽, 다음 첫 프레임은 7951 vs 127로 다음 쪽 — 사람 눈의 경계와 일치).
    컷 주변 창에서 프레임 지문이 이전/다음 런 대표 지문(안정 프레임) 중 어느
    쪽에 가까운지를 훑어 '다음 쪽에 처음 가까워지는 프레임'을 경계로 삼는다.
    창 안에 플립이 없으면 None(유지). OCR 정렬과 달리 판독 불가 프레임에서도
    동작하고, 이미 추출된 지문 PNG를 재사용해 ffmpeg·OCR 호출이 없다.
    lo/hi는 이웃 런 침범 방지 경계(lo exclusive, hi exclusive)."""
    import numpy as np

    def is_next(frame: int) -> bool:
        fp = fp_at(frame)
        return int(np.sum(fp != ref_prev)) >= int(np.sum(fp != ref_next))

    start = max(lo + 1, cut - window)
    end = min(hi, cut + window + 1)
    prior: bool | None = None
    for frame in range(start, end):
        cur = is_next(frame)
        if cur and prior is not True:
            return frame
        prior = cur
    return None


# 패딩 재판독 배율 — 경계 프레임 판독은 검출기 여백·저대비에 민감해, 같은
# 프레임이 구역을 넓히면 읽히는 경우가 실측으로 확인됐다(HH0304: 130_0160
# 디졸브 블러·020_0250 잔존 프레임 모두 타이트 구역 ''→패딩 구역 정상 판독).
# 1차는 스캔과 동일 구역(판독 조건 일관), 실패 시에만 패딩으로 근거를 회수한다.
_READ_PAD_FRAC = 0.3


def _pad_region(region) -> tuple[float, float, float, float]:
    """경계 재판독용 패딩 구역 — 사방으로 w·h의 _READ_PAD_FRAC만큼 넓힌다
    (0..1 클램프). 판독에만 쓰며 지문·경계 계산 구역은 그대로다."""
    x, y, w, h = region
    nx = max(0.0, x - w * _READ_PAD_FRAC)
    ny = max(0.0, y - h * _READ_PAD_FRAC)
    nw = min(1.0 - nx, x + w * (1.0 + _READ_PAD_FRAC) - nx)
    nh = min(1.0 - ny, y + h * (1.0 + _READ_PAD_FRAC) - ny)
    return (nx, ny, nw, nh)


def _relative_region(inner, outer) -> tuple[float, float, float, float]:
    """outer 크롭본 좌표계에서 본 inner 구역(비율, 0..1 클램프).

    스캔 중간본(build_scan_source가 만든 패딩 크롭 영상) 위에서 타이트 구역
    판독을 계속하기 위한 변환 — 중간본 전체 프레임(0,0,1,1)이 곧 패딩 구역이고,
    타이트 구역은 그 안의 부분 사각형이 된다."""
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    if ow <= 0 or oh <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    x = min(max((ix - ox) / ow, 0.0), 1.0)
    y = min(max((iy - oy) / oh, 0.0), 1.0)
    w = min(max(iw / ow, 0.0), 1.0 - x)
    h = min(max(ih / oh, 0.0), 1.0 - y)
    return (x, y, w, h)


def _resolve_unreadable_blocks(
    runs_f: list[tuple[int, int]], texts: list[str], picks: list[int],
    delimiters: list[str], fp_at, read_frame,
) -> tuple[list[tuple[int, int]], list[str]]:
    """서로 다른 라벨 사이에 낀 판독불가('') 런 블록을 프레임 단위로 귀속한다.

    텍스트 근거가 전혀 없는 블록은 runs_to_segments의 컷 세기 비율만으로는
    판정이 안 된다(실기 HH0304 2026-07-23: 문제 경계 전부가 애매 밴드
    1.1~2.4배 → 블록이 통째 앞 씬에 붙어 시퀀스 3·씬 48클립에 이웃 프레임
    혼입). 여기서 ①블록 각 프레임의 지문이 이전/다음 런 대표 지문(안정
    프레임) 중 어느 쪽에 가까운지로 플립 프레임을 찾고(_fp_align과 같은
    판정을 블록 전체 폭으로), ②블록 가장자리·플립 주변 프레임을 OCR해
    읽히는 프레임의 소속으로 플립을 캡한다(OCR 가독 > 픽셀 유사도 원칙).
    블록은 플립에서 갈라 양옆 런에 병합돼 사라진다 — 이후 경계 정렬(bounds)이
    읽히는 경계가 된 이 지점을 ±8프레임 OCR로 최종 다듬는다.

    같은 canonical 라벨 사이 블록(씬 내부 가짜컷·오독)은 연속 병합이 맞고,
    선두/꼬리 블록(한쪽 이웃 없음)은 기존 규칙(선두 드롭·꼬리 앞씬)이 맞으므로
    건드리지 않는다. OCR 제약이 모순(비단조 판독)이면 보수적으로 무변경."""
    import numpy as np

    def sq(s: str) -> str:
        return "".join("".join(t.split())
                       for t in tokenize(s, delimiters)).lower()

    blocks: list[tuple[int, int]] = []
    i = 0
    while i < len(texts):
        if texts[i].strip():
            i += 1
            continue
        j = i
        while j + 1 < len(texts) and not texts[j + 1].strip():
            j += 1
        blocks.append((i, j))
        i = j + 1

    resolved: dict[int, tuple[int, int]] = {}  # bi -> (bj, flip)
    for bi, bj in blocks:
        a, b = bi - 1, bj + 1
        if a < 0 or b >= len(texts):
            continue
        if sq(texts[a]) == sq(texts[b]):
            continue
        s, e = runs_f[bi][0], runs_f[bj][1]
        ref_prev, ref_next = fp_at(picks[a]), fp_at(picks[b])

        def is_next(f: int) -> bool:
            # _fp_align의 >=와 달리 엄격 부등호 — 지문이 무정보(양쪽 동거리)면
            # 이동 근거가 없으므로 기존 귀속(앞 씬)에 남긴다. OCR 캡이 최종 권위.
            fp = fp_at(f)
            return int(np.sum(fp != ref_prev)) > int(np.sum(fp != ref_next))

        flip = e
        for f in range(s, e):
            if is_next(f):
                flip = f
                break
        lo, hi = s, e
        for f in sorted({s, e - 1, max(s, flip - 1), min(e - 1, flip)}):
            side = _text_side(read_frame(f), texts[a], texts[b], delimiters)
            if side == "prev":
                lo = max(lo, f + 1)
            elif side == "next":
                hi = min(hi, f)
        if lo > hi:
            continue
        resolved[bi] = (bj, min(max(flip, lo), hi))

    if not resolved:
        return list(runs_f), list(texts)

    out_runs: list[tuple[int, int]] = []
    out_texts: list[str] = []
    override_start: int | None = None
    i = 0
    while i < len(runs_f):
        if i in resolved:
            bj, flip = resolved[i]
            if out_runs and flip > runs_f[i][0]:
                out_runs[-1] = (out_runs[-1][0], flip)
            override_start = flip
            i = bj + 1
            continue
        s, e = runs_f[i]
        if override_start is not None:
            s = override_start
            override_start = None
        out_runs.append((s, e))
        out_texts.append(texts[i])
        i += 1
    return out_runs, out_texts


async def run_scene_scan_fingerprint(external_id: UUID) -> None:
    """burned.mp4 전 프레임의 텍스트 이진화 지문으로 컷을 찾고, 컷 사이 런마다
    슬레이트를 OCR해 scenes.json에 method="fingerprint"로 저장한다. 경계는 규칙
    확정(/scenes/rule) 때 runs_to_segments가 계산한다 — 간격 스캔과 같은 2단계
    UX이되, 경계가 이미 프레임 정확이라 정밀화 단계가 없다.

    진행률: 추출·지문 단계는 total_frames=0(프론트 '프레임 추출 중…' 표시),
    런 OCR 단계부터 ocr_done/total_frames(=런 수)로 증분 기록. 취소·실패·세마포어
    규약은 run_scene_scan과 동일하되 method를 함께 보존한다(방식 선택 유지)."""
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    region_out = None
    try:
        workdir = job_dir(external_id)
        burned = workdir / "burned.mp4"
        if not burned.exists():
            raise RuntimeError("굽기 완료본(burned.mp4)이 없습니다.")
        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")
        # 컷 프레임 인덱스 ↔ 시각(ms) 변환의 기준 — 반드시 측정 fps(showinfo).
        fps = video_fps(ffmpeg, burned)
        if not fps:
            raise RuntimeError("소스 프레임레이트를 측정하지 못했습니다.")

        frames_dir = workdir / "scene_fp_frames"
        thumbs_dir = workdir / "scene_thumbs"
        for d in (frames_dir, thumbs_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

        region = load_ocr_region(external_id)
        region_out = ({"x": region[0], "y": region[1],
                       "w": region[2], "h": region[3]} if region else None)
        eff_region = region or _FP_FALLBACK_REGION
        thumb_interval_ms = int(_FP_THUMB_INTERVAL_S * 1000)

        def _prog(extra: dict) -> dict:
            return {"scanning": True, "method": "fingerprint",
                    "thumb_interval_ms": thumb_interval_ms,
                    "ocr_region": region_out, **extra}

        def _check_cancel() -> None:
            if generation != _current_generation(external_id):
                raise StaleRunCancelled(external_id)

        # 스캔 전용 크롭 중간본 — 이후 지문·판독·정렬의 모든 디코드가 1080p
        # 원본 대신 이 초소형 영상을 대상으로 한다(build_scan_source 참조).
        # 중간본 전체 프레임=패딩 구역이므로, 타이트 구역은 상대 좌표로 변환해
        # 쓰고 패딩 판독은 크롭 없이(전체 프레임) 읽는다.
        scan_src = workdir / "fp_scan_src.mp4"
        pad_abs = _pad_region(eff_region)
        tight_rel = _relative_region(eff_region, pad_abs)
        _FULL_REL = (0.0, 0.0, 1.0, 1.0)

        def _work() -> tuple[list[SceneRun], int, int]:
            # 여기부터 diff_series까지는 판독 카운터가 없는 구간이다(실측 60초).
            # 아무것도 안 흘리면 프론트가 200초 무변화를 정체로 보고 멀쩡한
            # 스캔을 포기하므로, 단계 이름과 산출물 증가(_extract_tick)를
            # 감시 스레드가 알린다 — 이 구간에는 다른 기록자가 없어 경합 없음.
            stage = [STAGE_CROP]
            stop = threading.Event()
            lock = threading.Lock()

            def _mark(tick: int) -> None:
                with lock:
                    save_scenes(external_id, _prog(
                        {"total_frames": 0, "ocr_done": 0, "frames": [],
                         "stage": stage[0], "stage_tick": tick}))

            def _enter(name: str) -> None:
                """단계 진입 — 본 스레드가 쓰므로 순서가 보장된다."""
                stage[0] = name
                _mark(_extract_tick(scan_src, frames_dir, thumbs_dir))

            def _watch() -> None:
                """단계 '안'의 살아있음 — 산출물이 늘 때만 값을 올린다."""
                last = None
                while not stop.wait(_EXTRACT_TICK_S):
                    tick = _extract_tick(scan_src, frames_dir, thumbs_dir)
                    if tick != last:
                        last = tick
                        _mark(tick)

            _enter(STAGE_CROP)
            watcher = threading.Thread(target=_watch, daemon=True)
            watcher.start()
            try:
                build_scan_source(ffmpeg, burned, scan_src, pad_abs,
                                  proc_key=str(external_id))
                _enter(STAGE_FRAMES)
                extract_fingerprint_frames(ffmpeg, scan_src, frames_dir,
                                           tight_rel, proc_key=str(external_id))
                _enter(STAGE_THUMBS)
                # 썸네일은 전체 화면이 필요하다(필름스트립) — 원본 유지.
                extract_thumbnails(ffmpeg, burned, thumbs_dir,
                                   _FP_THUMB_INTERVAL_S,
                                   proc_key=str(external_id))
            finally:
                stop.set()
                watcher.join(timeout=_EXTRACT_TICK_S * 2)
            thumb_count = len(list(thumbs_dir.glob("thumb_*.jpg")))
            pngs = sorted(frames_dir.glob("f_*.png"))
            n_frames = len(pngs)
            if n_frames == 0:
                raise RuntimeError("프레임을 추출하지 못했습니다.")
            stage[0] = STAGE_CUTS
            # 인접+윈도우 diff 한 패스 — 윈도우가 느린 페이드(인접 diff가 임계를
            # 못 넘는 디졸브)의 컷 누락을 막는다(실기: 씬 통째 흡수). 3만 장을
            # 도는 통짜 루프라 여기서도 진행률을 흘린다(취소 확인과 같은 주기).
            diffs, wdiffs = diff_series(
                pngs, FADE_WINDOW, check_cancel=_check_cancel,
                on_progress=lambda i: save_scenes(external_id, _prog(
                    {"total_frames": 0, "ocr_done": 0, "frames": [],
                     "thumb_count": thumb_count,
                     "stage": STAGE_CUTS, "stage_tick": i})))
            runs_f = frame_runs(
                detect_cuts_with_fades(diffs, wdiffs, FADE_WINDOW), n_frames)
            total = len(runs_f)
            save_scenes(external_id, _prog(
                {"total_frames": total, "ocr_done": 0, "frames": [],
                 "thumb_count": thumb_count}))

            tmpdir = workdir / "fp_ocr_tmp"
            tmpdir.mkdir(parents=True, exist_ok=True)

            # 런마다 '정지' 프레임(인접 diff 최소)을 골라 한 번의 디코드 패스로
            # 일괄 추출한다 — 런마다 -ss 시킹하면 830ms×수천 런=수 분이 시킹에
            # 녹고(실측 총 9분), 흐릿한 중간 프레임을 읽어 오독도 는다.
            picks = [stable_frame(diffs, s, e) for s, e in runs_f]
            batch = extract_frames_at(ffmpeg, scan_src, picks, tmpdir,
                                      tight_rel, proc_key=str(external_id),
                                      workers=_refine_workers())

            def _read_run(item: tuple[int, tuple[int, int]]) -> str:
                # 배치 프레임만 읽는다 — 실패분의 재시도는 아래 패딩 배치와
                # 잔여 시킹 단계가 맡는다. 예전엔 여기서 런마다 개별 시킹
                # 폴백(0.25/0.75)을 했는데, '' 런이 많은 실기(HH0304 1011런)
                # 에서 시킹에만 ~10분이 녹았고 그 뒤의 패딩 배치가 어차피
                # 95%를 살렸다(순서 비효율).
                idx, _span = item
                _check_cancel()
                png = batch.get(picks[idx])
                return (read_slate_line(png, _DEFAULT_DELIMS, top_frac=1.0)
                        if png is not None else "")

            texts: list[str] = []
            done = 0
            try:
                # 런 판독은 서로 독립 — 정밀화·스캔과 같은 이유·설정으로 병렬화.
                with ThreadPoolExecutor(max_workers=_refine_workers()) as pool:
                    for text in pool.map(_read_run, enumerate(runs_f)):
                        texts.append(text)
                        done += 1
                        if done % 10 == 0 or done == total:
                            save_scenes(external_id, _prog(
                                {"total_frames": total, "ocr_done": done,
                                 "frames": [], "thumb_count": thumb_count}))

                # ── 패딩 재판독 배치 — 타이트 구역이 못 읽은 런의 안정 프레임을
                # 패딩 구역(_pad_region)으로 한 번 더 일괄 판독한다. 실기 HH0304:
                # '' 1011런의 상당수가 패딩에서 정상 판독(110_0330~0350은 씬
                # 통째가 이 경로로만 복구). 추가 비용은 미판독 런 수만큼의
                # select 배치 1회.
                # 재시도 단계도 진행률을 쓴다 — 카운터가 total에 닿은 채 몇 분이
                # 흐르면 프론트의 정체 판정(200초 무변화)이 멀쩡한 스캔을 실패로
                # 만든다("스캔이 진행되지 않습니다"). 실기: 타이트 판독이 전멸한
                # 소스에서 미판독 런이 곧 전체 런이라 두 재시도 단계가 스캔의
                # 대부분을 차지했고, 화면은 '판독 중… 2791/2791'에서 굳었다.
                # 정렬 단계와 같은 방식으로 total 뒤에 이어 센다.
                def _stage_prog(n: int, done_k: int) -> None:
                    save_scenes(external_id, _prog(
                        {"total_frames": total + n, "ocr_done": total + done_k,
                         "frames": [], "thumb_count": thumb_count}))

                miss = [i for i, t in enumerate(texts) if not t.strip()]
                if miss:
                    pad_batch = extract_frames_at(
                        ffmpeg, scan_src, [picks[i] for i in miss],
                        tmpdir / "pad", _FULL_REL,
                        proc_key=str(external_id), workers=_refine_workers())
                    for k, i in enumerate(miss):
                        _check_cancel()
                        png = pad_batch.get(picks[i])
                        if png is not None:
                            t = (read_slate_line(png, _DEFAULT_DELIMS,
                                                 top_frac=1.0)
                                 or read_slate_line_rescaled(
                                     png, _DEFAULT_DELIMS, top_frac=1.0))
                            if t:
                                texts[i] = t
                        if k % 10 == 0 or k == len(miss) - 1:
                            _stage_prog(len(miss), k + 1)

                # 패딩 배치도 못 살린 잔여 런만 개별 시킹 재시도(실기 1011→55).
                # 자리를 바꿔(0.25/0.75) 타이트→패딩 순으로 읽는다 — 런 내부는
                # 텍스트가 동일하다는 지문 방식의 전제 그대로.
                def _retry_run(i: int) -> tuple[int, str]:
                    _check_cancel()
                    start_f, end_f = runs_f[i]
                    span = end_f - start_f
                    for frac in (0.25, 0.75):
                        fi = min(end_f - 1, start_f + int(span * frac))
                        for region in (tight_rel, _FULL_REL):
                            dst = tmpdir / f"r_{threading.get_ident()}_{fi}.png"
                            extract_frame(ffmpeg, scan_src,
                                          frame_boundary_ms(fi, fps), dst,
                                          proc_key=str(external_id),
                                          region=region)
                            text = read_slate_line(dst, _DEFAULT_DELIMS,
                                                   top_frac=1.0)
                            if not text and region is _FULL_REL:
                                text = read_slate_line_rescaled(
                                    dst, _DEFAULT_DELIMS, top_frac=1.0)
                            try:
                                dst.unlink()
                            except OSError:
                                pass
                            if text:
                                return i, text
                    return i, ""

                still = [i for i, t in enumerate(texts) if not t.strip()]
                if still:
                    with ThreadPoolExecutor(
                            max_workers=_refine_workers()) as retry_pool:
                        for k, (i, text) in enumerate(
                                retry_pool.map(_retry_run, still)):
                            if text:
                                texts[i] = text
                            # 런마다 시킹 4회까지 도는 가장 느린 단계 — 여기서
                            # 진행률이 멈추면 프론트가 스캔을 포기한다.
                            if k % 10 == 0 or k == len(still) - 1:
                                _stage_prog(len(still), k + 1)

                # ── 판독불가 블록 프레임 단위 귀속 — 서로 다른 라벨 사이 ''
                # 블록이 통째 앞 씬에 붙는 혼입(실기 HH0304 씬 48클립)의 근본
                # 수정. 아래 bounds 정렬은 양쪽이 읽힌 경계만 보므로, 한쪽이
                # ''인 경계는 여기서 먼저 없앤다(블록을 플립에서 갈라 병합).
                fp_cache: dict[int, object] = {}

                def _fp_at(fi: int):
                    fp = fp_cache.get(fi)
                    if fp is None:
                        fp = load_fingerprint(pngs[fi])
                        fp_cache[fi] = fp
                    return fp

                seek_texts: dict[int, str] = {}

                def _read_seek(fi: int) -> str:
                    if fi not in seek_texts:
                        _check_cancel()
                        dst = tmpdir / f"rb_{fi}.png"
                        extract_frame(ffmpeg, scan_src,
                                      frame_boundary_ms(fi, fps), dst,
                                      proc_key=str(external_id),
                                      region=tight_rel)
                        text = read_slate_line(dst, _DEFAULT_DELIMS,
                                               top_frac=1.0)
                        if not text:
                            pdst = tmpdir / f"rbp_{fi}.png"
                            extract_frame(ffmpeg, scan_src,
                                          frame_boundary_ms(fi, fps), pdst,
                                          proc_key=str(external_id),
                                          region=_FULL_REL)
                            text = (read_slate_line(pdst, _DEFAULT_DELIMS,
                                                    top_frac=1.0)
                                    or read_slate_line_rescaled(
                                        pdst, _DEFAULT_DELIMS, top_frac=1.0))
                        seek_texts[fi] = text
                    return seek_texts[fi]

                runs_f, texts = _resolve_unreadable_blocks(
                    runs_f, texts, picks, _DEFAULT_DELIMS, _fp_at, _read_seek)
                picks = [stable_frame(diffs, s, e) for s, e in runs_f]

                # 디졸브 경계 정렬 — 텍스트가 달라지는 컷마다 전후 프레임을 읽어
                # 슬레이트 가독 전환 프레임으로 옮긴다(_align_cut 참조). 전후
                # 프레임은 배치로 미리 뜨고, 걷기(드묾)만 개별 시킹한다.
                texts_c = canonicalize_texts(texts, _DEFAULT_DELIMS)
                bounds = [i for i in range(1, len(runs_f))
                          if texts_c[i - 1] and texts_c[i]
                          and texts_c[i - 1] != texts_c[i]]
                align_dir = tmpdir / "align"
                prefetch = (extract_frames_at(
                    ffmpeg, scan_src,
                    sorted({f for i in bounds
                            for f in (runs_f[i][0] - 1, runs_f[i][0])}),
                    align_dir, tight_rel, proc_key=str(external_id),
                    workers=_refine_workers()) if bounds else {})
                read_cache: dict[int, str] = {}

                def _read_frame(fi: int) -> str:
                    if fi in read_cache:
                        return read_cache[fi]
                    png = prefetch.get(fi)
                    if png is None:
                        png = align_dir / f"nb_{fi}.png"
                        extract_frame(ffmpeg, scan_src,
                                      frame_boundary_ms(fi, fps), png,
                                      proc_key=str(external_id),
                                      region=tight_rel)
                    text = read_slate_line(png, _DEFAULT_DELIMS, top_frac=1.0)
                    if not text:
                        # 패딩 재판독 — 경계 프레임의 판독 깜박임이 걷기·정렬의
                        # 근거를 지우던 것의 회수. 중간본 전체 프레임=패딩 구역.
                        pdst = align_dir / f"nbp_{fi}.png"
                        extract_frame(ffmpeg, scan_src,
                                      frame_boundary_ms(fi, fps), pdst,
                                      proc_key=str(external_id),
                                      region=_FULL_REL)
                        text = (read_slate_line(pdst, _DEFAULT_DELIMS,
                                                top_frac=1.0)
                                or read_slate_line_rescaled(
                                    pdst, _DEFAULT_DELIMS, top_frac=1.0))
                    read_cache[fi] = text
                    return text

                starts = [s for s, _e in runs_f]
                # ① 지문 유사도 정렬 — OCR이 못 읽는 페이드 프레임의 귀속을
                # 픽셀 잔상으로 판정한다(_fp_align 참조). 이동은 OCR 가독성으로
                # 캡(_clamp_fp_move). _fp_at은 위 블록 귀속과 캐시를 공유한다.
                for i in bounds:
                    _check_cancel()
                    aligned = _fp_align(
                        _fp_at, starts[i], _fp_at(picks[i - 1]), _fp_at(picks[i]),
                        lo=starts[i - 1], hi=runs_f[i][1])
                    if aligned is not None and aligned != starts[i]:
                        prev_t, next_t = texts_c[i - 1], texts_c[i]

                        def _side(fi: int, p=prev_t, n=next_t) -> str | None:
                            return _text_side(_read_frame(fi), p, n,
                                              _DEFAULT_DELIMS)

                        starts[i] = _clamp_fp_move(_side, starts[i], aligned)

                # ② OCR 정렬을 '마지막'에 — 읽히는 프레임의 소속은 OCR이 최종
                # 권위다. 유사도가 어떤 이유로든(캡의 판정 불가 프레임 등) 경계를
                # 어긋내면 여기서 교정된다(실기: 하드컷·선명 슬레이트 잔존 오차).
                for bi, i in enumerate(bounds):
                    _check_cancel()
                    starts[i] = _align_cut(
                        _read_frame, starts[i], texts_c[i - 1], texts_c[i],
                        lo=runs_f[i - 1][0], hi=runs_f[i][1],
                        delimiters=_DEFAULT_DELIMS)
                    if bi % 20 == 0 or bi == len(bounds) - 1:
                        save_scenes(external_id, _prog(
                            {"total_frames": total + len(bounds),
                             "ocr_done": total + bi + 1, "frames": [],
                             "thumb_count": thumb_count}))

                # 정렬 결과로 런 재구성 — 연속성 유지(끝=다음 시작), 극단적으로
                # 이웃 경계가 서로를 지나치면(짧은 런 양끝이 동시 이동) 단조 보정.
                for i in range(1, len(starts)):
                    starts[i] = max(starts[i], starts[i - 1] + 1)
                runs_f = [(starts[i],
                           starts[i + 1] if i + 1 < len(starts) else n_frames)
                          for i in range(len(runs_f))]
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

            # cut_diff=각 런을 연 컷의 지문 세기(정렬 후 최종 시작 프레임 기준) —
            # 판독불가 블록 귀속(runs_to_segments)의 유일한 판정 신호다.
            runs = [SceneRun(start_ms=frame_boundary_ms(s, fps),
                             end_ms=frame_boundary_ms(e, fps), text=t,
                             cut_diff=(diffs[s - 1]
                                       if 0 < s <= len(diffs) else 0))
                    for (s, e), t in zip(runs_f, texts)]
            return runs, thumb_count, n_frames

        try:
            runs, thumb_count, n_frames = await asyncio.to_thread(_work)
        finally:
            # 지문용 프레임은 수만 장이라 크고, 스캔 중간본도 수백 MB다 —
            # 실패해도 제거한다.
            shutil.rmtree(frames_dir, ignore_errors=True)
            try:
                scan_src.unlink()
            except OSError:
                pass

        save_scenes(external_id, {
            "scanning": False,
            "method": "fingerprint",
            "video_fps": fps,
            "total_ms": frame_boundary_ms(n_frames, fps),
            "thumb_interval_ms": thumb_interval_ms,
            "thumb_count": thumb_count,
            "frame_count": len(runs),
            "runs": [{"start_ms": r.start_ms, "end_ms": r.end_ms,
                      "text": r.text, "cut_diff": r.cut_diff} for r in runs],
            # frames는 토큰 선택 UI 호환용 — 런 시작 시각을 샘플로 노출한다.
            "frames": [{"t_ms": r.start_ms, "text": r.text} for r in runs],
            "ocr_region": region_out,
        })
    except StaleRunCancelled:
        logger.info("scene fp scan %s cancelled (gen %d)", external_id, generation)
        # 멈추는 쪽이 자기 플래그를 내린다(run_scene_scan과 동일 경합 방지).
        # 부분 판독은 남기지 않되 구역·방식 선택은 보존한다.
        save_scenes(external_id, {"scanning": False, "method": "fingerprint",
                                  "ocr_region": region_out})
    except Exception:  # noqa: BLE001
        if generation != _current_generation(external_id):
            # kill_active로 죽은 ffmpeg가 FfmpegError로 표면화된 경우 —
            # 세대가 넘어갔으면 실패가 아니라 취소이므로 조용히 정리한다.
            logger.info("scene fp scan %s cancelled mid-ffmpeg (gen %d)",
                        external_id, generation)
            return
        logger.exception("scene fp scan %s failed", external_id)
        try:
            save_scenes(external_id, {"scanning": False, "method": "fingerprint",
                                      "frames": [], "ocr_region": region_out,
                                      "error": "스캔에 실패했습니다. 서버 로그를 확인하세요."})
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        _BURN_SEMAPHORE.release()
