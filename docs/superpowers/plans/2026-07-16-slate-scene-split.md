# 슬레이트 OCR 씬별 분할 익스포트 — 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 자막메이커 결과보기의 완료된 잡(`burned.mp4`)에서 슬레이트 텍스트를 OCR로 읽어, 사용자가 지정한 토큰 규칙에 따라 시퀀스별/씬별로 재인코딩 분할해 폴더로 내보낸다.

**Architecture:** 백엔드는 `apps/server/domain/video_captions/`에 순수 파싱 코어(`scene_split.py`) + OCR 래퍼(`slate_ocr.py`)를 추가하고, ffmpeg 래퍼에 프레임 추출·세그먼트 컷을 더한다. 스캔·익스포트는 기존 굽기(`run_burn_job`)와 동일한 asyncio 태스크 + 세대(generation) + 세마포어 패턴으로 돌린다. 상태는 새 DB 없이 잡 디렉토리 `scenes.json`에 저장한다. 프론트는 `videoApi.ts`에 클라이언트 함수를, 데스크톱에 필름스트립 화면을 추가한다.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy(async) / RapidOCR(onnxruntime) / ffmpeg CLI / React + TypeScript / Tauri(plugin-dialog, plugin-fs) / pytest / vitest.

## Global Constraints

- 최소 패치 원칙: 요청된 파일만 수정, 파일 전체 재작성 금지, 임포트 구조 임의 변경 금지 (CLAUDE.md 규칙 1·2·3·7).
- 대상 파일은 `burned.mp4` — 잡 상태가 `done`일 때만 진입. `job_dir(external_id)/burned.mp4` 존재가 전제.
- OCR 엔진은 **RapidOCR (rapidocr-onnxruntime)** 고정. onnxruntime은 이미 faster-whisper 전이의존 + build-server.sh `--collect-all onnxruntime`.
- 자르기는 **재인코딩(정확)** — `-c copy` 무손실 복사 금지. 인코더 인자는 기존 굽기와 동일(`libx264 -preset veryfast -crf 23`, 오디오 `aac`).
- ffprobe 사용 금지 (번들에 없음 — ffmpeg.lock.json 명시). 프레임 시각·경계는 프레임 인덱스×간격으로 계산.
- 서버 subprocess는 항상 `text=True, encoding="utf-8", errors="replace"` + `**ff._SUBPROCESS_FLAGS`(Windows 콘솔 창 억제) 사용 (Windows cp949 교훈).
- CPU 집약 작업(OCR·인코딩)은 `asyncio.to_thread`로. 스캔·익스포트 태스크는 세대 카운터로 취소·재실행 스테일 쓰기를 무효화.
- 새 DB 테이블/컬럼 추가 금지 — 규칙·경계는 `job_dir/scenes.json`에만 저장.
- 파이썬 테스트: `python -m pytest apps/server/tests/<file> -v` (root pyproject testpaths는 server만 가리킴 — sidecar/scripts는 명시 경로 필요, 하지만 이 플랜은 server 내부라 무관).

## 파일 구조

- Create `apps/server/domain/video_captions/scene_split.py` — 순수 파싱 코어: `SlateRule`, `FrameSample`, `Segment`, `tokenize`, `grouping_key`, `build_label`, `hold_keys`, `compute_boundaries`. I/O 없음.
- Create `apps/server/domain/video_captions/slate_ocr.py` — RapidOCR 지연 싱글턴 + `read_slate_line(image_path) -> str`.
- Modify `apps/server/domain/video_captions/ffmpeg.py` — `extract_frames`, `extract_thumbnails`, `cut_segment` 추가.
- Modify `apps/server/domain/video_captions/pipeline.py` — `run_scene_scan`, `run_scene_export`, `scenes_json_path`, `load_scenes`, `save_scenes` 추가 + 태스크 레지스트리 재사용.
- Modify `apps/server/api/v1/video_jobs.py` — scan/rule/segments/export/thumb 엔드포인트 + Pydantic 모델 + 테스트 시임.
- Create `apps/server/tests/test_video_scene_split.py` — 코어 순수 함수 회귀 테스트.
- Create `apps/server/tests/test_video_scene_ffmpeg.py` — 프레임 추출·컷 명령 구성 테스트.
- Modify `apps/server/tests/test_api_video_jobs.py` — 씬 분할 엔드포인트 테스트 (또는 신규 파일).
- Modify `apps/server/pyproject.toml` — `rapidocr-onnxruntime` 의존성.
- Modify `apps/server_desktop/scripts/build-server.sh` + `apps/server_desktop/scripts/build-server.ps1` — `--collect-all rapidocr_onnxruntime`.
- Modify `apps/desktop/src/console/videoApi.ts` — 씬 분할 클라이언트 함수·타입.
- Create `apps/desktop/src/console/sceneSplitLogic.ts` — 프론트 순수 로직(토큰 미리보기·라벨·구간 렌더 매핑).
- Create `apps/desktop/src/console/sceneSplitLogic.test.ts` — vitest.
- Create `apps/desktop/src/console/SceneSplitView.tsx` — 규칙 지정 + 필름스트립 + 익스포트 화면.
- Create `apps/desktop/src/console/SceneFilmstrip.tsx` — 썸네일 트랙 + 컷 라인 + 라벨.
- Modify `apps/desktop/src/console/VideoReviewView.tsx` — "씬별 분할" 진입 버튼.

---

### Task 1: 슬레이트 파싱 코어 (순수 함수)

가장 중요한 TDD 대상. ffmpeg·OCR·DB 없이 "슬레이트 텍스트 배열 → 규칙 → 경계"만 계산한다. 실제 두 슬레이트 예시로 회귀를 잠근다.

**Files:**
- Create: `apps/server/domain/video_captions/scene_split.py`
- Test: `apps/server/tests/test_video_scene_split.py`

**Interfaces:**
- Produces:
  - `SlateRule(delimiters: list[str], seq_tokens: list[int], scene_tokens: list[int])` (dataclass)
  - `FrameSample(index: int, t_ms: int, text: str)` (dataclass)
  - `Segment(label: str, start_ms: int, end_ms: int)` (dataclass)
  - `tokenize(text: str, delimiters: list[str]) -> list[str]`
  - `grouping_key(tokens: list[str], indices: list[int]) -> str | None`
  - `build_label(tokens: list[str], upto_index: int) -> str`
  - `hold_keys(samples: list[FrameSample], rule: SlateRule, mode: str) -> list[tuple[int, str | None, str | None]]` — 각 프레임을 `(t_ms, grouping_key, label)`로 매핑, 빈/판독불가 프레임은 직전 유효값으로 홀드.
  - `compute_boundaries(keyed: list[tuple[int, str | None, str | None]], total_ms: int, min_ms: int = 2000) -> list[Segment]`

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_video_scene_split.py
from __future__ import annotations

from apps.server.domain.video_captions.scene_split import (
    FrameSample, Segment, SlateRule, build_label, compute_boundaries,
    grouping_key, hold_keys, tokenize,
)

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/server/tests/test_video_scene_split.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.server.domain.video_captions.scene_split'`

- [ ] **Step 3: Write minimal implementation**

```python
# apps/server/domain/video_captions/scene_split.py
"""슬레이트 텍스트 → 씬/시퀀스 경계 계산 (순수 함수, I/O 없음).

작품마다 슬레이트 포맷이 달라(예: "HH0307_020_0150_AC_v01" vs
"Seq 07_S08 - Panel 3") 파서를 하드코딩하지 않는다. OCR이 읽은 텍스트를
구분자로 토큰화하고, 사용자가 지정한 토큰 인덱스(SlateRule)로 그룹 키와
파일명 라벨을 만든다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# grouping_key 내부 결합자 — 슬레이트 토큰에 등장하지 않는 제어문자(US)라
# "020"+"0150"과 "0200"+"150" 같은 충돌을 막는다.
_KEY_SEP = "\x1f"


@dataclass
class SlateRule:
    delimiters: list[str]
    seq_tokens: list[int]
    scene_tokens: list[int]


@dataclass
class FrameSample:
    index: int
    t_ms: int
    text: str


@dataclass
class Segment:
    label: str
    start_ms: int
    end_ms: int


def tokenize(text: str, delimiters: list[str]) -> list[str]:
    """구분자(기본 _, 공백, -)로 분해. 빈 토큰은 버린다(하이픈 양옆 공백 등)."""
    if not delimiters:
        parts = [text]
    else:
        pattern = "|".join(re.escape(d) for d in delimiters)
        parts = re.split(pattern, text)
    return [p for p in (s.strip() for s in parts) if p]


def grouping_key(tokens: list[str], indices: list[int]) -> str | None:
    """선택된 토큰들을 결합해 그룹 키를 만든다. 인덱스가 범위를 벗어나면
    (판독 실패로 토큰이 모자란 경우) None."""
    if not indices or any(i < 0 or i >= len(tokens) for i in indices):
        return None
    return _KEY_SEP.join(tokens[i] for i in indices)


def build_label(tokens: list[str], upto_index: int) -> str:
    """파일명 라벨 = tokens[0..upto_index]를 "_"로 결합. 선택 토큰 앞의 고정
    접두(쇼넘버 등)가 자연히 포함된다."""
    if upto_index < 0 or upto_index >= len(tokens):
        return ""
    return "_".join(tokens[: upto_index + 1])


def _mode_indices(rule: SlateRule, mode: str) -> list[int]:
    if mode == "sequence":
        return rule.seq_tokens
    return rule.seq_tokens + rule.scene_tokens


def hold_keys(
    samples: list[FrameSample], rule: SlateRule, mode: str,
) -> list[tuple[int, str | None, str | None]]:
    """각 프레임을 (t_ms, grouping_key, label)로 매핑. 판독 실패(빈 텍스트·
    토큰 부족) 프레임은 직전 유효값으로 홀드한다."""
    indices = _mode_indices(rule, mode)
    upto = max(indices) if indices else -1
    out: list[tuple[int, str | None, str | None]] = []
    last_key: str | None = None
    last_label: str | None = None
    for s in samples:
        toks = tokenize(s.text, rule.delimiters) if s.text else []
        key = grouping_key(toks, indices)
        if key is not None:
            last_key = key
            last_label = build_label(toks, upto)
        out.append((s.t_ms, last_key, last_label))
    return out


def compute_boundaries(
    keyed: list[tuple[int, str | None, str | None]],
    total_ms: int,
    min_ms: int = 2000,
) -> list[Segment]:
    """연속된 동일 키 구간을 세그먼트로 묶는다. 각 구간은
    [start_ms, 다음 구간 start_ms) (마지막은 total_ms). min_ms 미만 구간은
    직전 구간에 흡수해 오독 1프레임 튐을 제거한다."""
    runs: list[tuple[str, str, int]] = []  # (key, label, start_ms)
    for t_ms, key, label in keyed:
        if key is None:
            continue
        if not runs or runs[-1][0] != key:
            runs.append((key, label or "", t_ms))
    if not runs:
        return []

    # (start, end) 부여
    spans: list[list] = []
    for i, (key, label, start) in enumerate(runs):
        end = runs[i + 1][2] if i + 1 < len(runs) else total_ms
        spans.append([key, label, start, end])

    # min_ms 미만 흡수 — 직전 구간에 합치고, 없으면 다음 구간 시작을 앞당긴다.
    merged: list[list] = []
    for span in spans:
        key, label, start, end = span
        if end - start < min_ms and merged:
            merged[-1][3] = end  # 직전 구간 끝을 연장(흡수)
            continue
        merged.append(span)
    # 첫 구간이 짧고 흡수할 직전이 없으면 다음 구간과 병합
    if len(merged) >= 2 and merged[0][3] - merged[0][2] < min_ms:
        merged[1][2] = merged[0][2]
        merged.pop(0)

    return [Segment(label=lbl, start_ms=st, end_ms=en) for _, lbl, st, en in merged]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest apps/server/tests/test_video_scene_split.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add apps/server/domain/video_captions/scene_split.py apps/server/tests/test_video_scene_split.py
git commit -m "feat(video): 슬레이트 씬/시퀀스 경계 계산 순수 코어"
```

---

### Task 2: RapidOCR 슬레이트 판독 래퍼

프레임 이미지 한 장에서 슬레이트 라인(토큰화 가능한 후보)을 뽑는다. RapidOCR 인스턴스는 초기화가 비싸므로 프로세스당 1회 지연 생성한다.

**Files:**
- Create: `apps/server/domain/video_captions/slate_ocr.py`
- Modify: `apps/server/pyproject.toml`
- Test: `apps/server/tests/test_video_scene_split.py` (파일에 OCR 픽처 테스트 추가)

**Interfaces:**
- Consumes: `tokenize` (Task 1)
- Produces:
  - `read_slate_line(image_path: str | Path, delimiters: list[str], min_tokens: int = 2) -> str` — 이미지에서 OCR한 라인 중 "토큰 수가 가장 많고 신뢰도 높은" 후보 텍스트. 없으면 `""`.
  - `pick_slate_line(lines: list[tuple[str, float]], delimiters: list[str], min_tokens: int) -> str` — 순수 선택 함수(테스트용): `(text, score)` 목록에서 슬레이트 후보 1개.
  - `_get_engine()` — 지연 싱글턴(테스트에서 monkeypatch 대상).

- [ ] **Step 1: Write the failing test** (기존 test 파일에 append)

```python
# apps/server/tests/test_video_scene_split.py 에 추가
from apps.server.domain.video_captions.slate_ocr import pick_slate_line


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/server/tests/test_video_scene_split.py -k pick_slate_line -v`
Expected: FAIL — `ModuleNotFoundError: ... slate_ocr`

- [ ] **Step 3: Add dependency + write implementation**

`apps/server/pyproject.toml` 의 `dependencies` 리스트 끝(`"faster-whisper>=1.1",` 다음 줄)에 추가:

```toml
  "faster-whisper>=1.1",
  "rapidocr-onnxruntime>=1.3",
```

```python
# apps/server/domain/video_captions/slate_ocr.py
"""RapidOCR(onnxruntime) 슬레이트 판독 래퍼.

onnxruntime은 이미 faster-whisper 전이의존 + 번들 --collect-all 대상이라
새 시스템 바이너리가 없다. RapidOCR 초기화(모델 로드)는 비싸므로 프로세스당
1회 지연 생성한다. 슬레이트는 배경 대비 뚜렷한 산세리프라 판독이 쉽다.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .scene_split import tokenize

logger = logging.getLogger("yeson.video.slate_ocr")

_engine = None


def _get_engine():
    """RapidOCR 지연 싱글턴. import·초기화 실패는 호출자에게 전파한다."""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR  # 지연 import (번들 무관 경로 보호)
        _engine = RapidOCR()
    return _engine


def pick_slate_line(
    lines: list[tuple[str, float]], delimiters: list[str], min_tokens: int,
) -> str:
    """OCR 라인 후보 중 슬레이트 1줄 선택 — 토큰 수(내림차순)·신뢰도(내림차순)
    우선. min_tokens 미만으로 쪼개지는 라인은 후보에서 제외한다."""
    scored = []
    for text, score in lines:
        n = len(tokenize(text, delimiters))
        if n >= min_tokens:
            scored.append((n, score, text))
    if not scored:
        return ""
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored[0][2]


def read_slate_line(
    image_path: str | Path, delimiters: list[str], min_tokens: int = 2,
) -> str:
    """이미지 한 장 OCR → 슬레이트 라인. 판독 실패/후보 없음은 "" 반환."""
    try:
        result, _elapse = _get_engine()(str(image_path))
    except Exception:  # noqa: BLE001 — 한 프레임 판독 실패가 전체 스캔을 막지 않게
        logger.exception("OCR failed for %s", image_path)
        return ""
    if not result:
        return ""
    # RapidOCR 반환: [[box, text, score], ...]
    lines = [(item[1], float(item[2])) for item in result]
    return pick_slate_line(lines, delimiters, min_tokens)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest apps/server/tests/test_video_scene_split.py -k pick_slate_line -v`
Expected: PASS (2 passed) — `pick_slate_line`은 RapidOCR import 없이 순수 함수라 의존성 미설치 상태에서도 통과.

- [ ] **Step 5: Install dependency locally + smoke import**

```bash
cd apps/server && uv pip install "rapidocr-onnxruntime>=1.3"
python -c "from apps.server.domain.video_captions.slate_ocr import read_slate_line; print('ok')"
```
Expected: `ok` (import 성공 — 실제 OCR은 Task 3 통합에서 검증)

- [ ] **Step 6: Commit**

```bash
git add apps/server/domain/video_captions/slate_ocr.py apps/server/pyproject.toml apps/server/tests/test_video_scene_split.py
git commit -m "feat(video): RapidOCR 슬레이트 판독 래퍼 + 의존성"
```

---

### Task 3: ffmpeg 프레임 추출 · 썸네일 · 세그먼트 컷

ffmpeg 래퍼(`ffmpeg.py`)에 세 함수를 추가한다. 기존 `_run`/`FfmpegError`/proc 레지스트리 패턴을 그대로 재사용한다.

**Files:**
- Modify: `apps/server/domain/video_captions/ffmpeg.py` (파일 끝에 함수 추가)
- Test: `apps/server/tests/test_video_scene_ffmpeg.py`

**Interfaces:**
- Consumes: `_run`, `_SUBPROCESS_FLAGS`, `FfmpegError` (기존 ffmpeg.py)
- Produces:
  - `extract_frames(ffmpeg: str, src: Path, out_dir: Path, interval_s: float = 1.0, proc_key: str | None = None) -> None` — `out_dir/frame_%05d.png` 생성.
  - `extract_thumbnails(ffmpeg: str, src: Path, out_dir: Path, interval_s: float = 1.0, height: int = 90, proc_key: str | None = None) -> None` — `out_dir/thumb_%05d.jpg`.
  - `cut_segment(ffmpeg: str, src: Path, dst: Path, start_ms: int, end_ms: int, proc_key: str | None = None) -> None` — 재인코딩 컷.

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_video_scene_ffmpeg.py
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


def test_cut_segment_reencodes_with_ss_to(monkeypatch, tmp_path: Path):
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
    assert cmd[cmd.index("-to") + 1] == "7.500"
    # -ss가 -i 앞이면 입력 시킹(빠름)이지만 재인코딩이라 프레임 정확
    assert cmd.index("-ss") < cmd.index("-i")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/server/tests/test_video_scene_ffmpeg.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'extract_frames'`

- [ ] **Step 3: Write implementation** (ffmpeg.py `ensure_preview` 함수 뒤, 파일 끝에 추가)

```python
def extract_frames(ffmpeg: str, src: Path, out_dir: Path,
                   interval_s: float = 1.0, proc_key: str | None = None) -> None:
    """OCR용 프레임을 interval_s 간격으로 out_dir/frame_%05d.png에 추출.

    frame_00001.png ≈ t=0, frame_00002.png ≈ t=interval_s … (fps 필터 기준).
    호출자는 인덱스(1-based)로 t_ms = (index-1)*interval_ms를 부여한다.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg, "-y", "-i", str(src), "-vf", f"fps=1/{interval_s}",
          str(out_dir / "frame_%05d.png")], proc_key=proc_key)


def extract_thumbnails(ffmpeg: str, src: Path, out_dir: Path,
                       interval_s: float = 1.0, height: int = 90,
                       proc_key: str | None = None) -> None:
    """필름스트립용 저해상도 썸네일. scale=-2:height (너비는 짝수 자동)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg, "-y", "-i", str(src), "-vf",
          f"fps=1/{interval_s},scale=-2:{height}",
          str(out_dir / "thumb_%05d.jpg")], proc_key=proc_key)


def cut_segment(ffmpeg: str, src: Path, dst: Path, start_ms: int, end_ms: int,
                proc_key: str | None = None) -> None:
    """[start_ms, end_ms) 구간을 재인코딩(정확)해 dst로 저장. -c copy 금지 —
    슬레이트 편집본은 컷 경계가 명확해야 하므로 프레임 정확도를 우선한다.
    -ss를 -i 앞에 둬 입력 시킹으로 빠르게 접근하되, 재인코딩이라 컷은 정확하다."""
    ss = f"{start_ms / 1000:.3f}"
    to = f"{end_ms / 1000:.3f}"
    _run([ffmpeg, "-y", "-ss", ss, "-to", to, "-i", str(src),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
          "-c:a", "aac", "-movflags", "+faststart", str(dst)],
         proc_key=proc_key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest apps/server/tests/test_video_scene_ffmpeg.py -v`
Expected: PASS (3 passed)

> 주의: `-ss`를 `-i` 앞에 두면 입력 시킹이다. 재인코딩 경로에서는 프레임 정확하지만, 만약 실기 검증에서 컷 시작이 어긋나면 `-ss`를 `-i` **뒤**(출력 시킹)로 옮긴다(느리지만 항상 정확). Task 5 실기 검증에서 확인.

- [ ] **Step 5: Commit**

```bash
git add apps/server/domain/video_captions/ffmpeg.py apps/server/tests/test_video_scene_ffmpeg.py
git commit -m "feat(video): ffmpeg 프레임 추출·썸네일·세그먼트 컷"
```

---

### Task 4: scenes.json 저장 헬퍼

규칙·프레임·경계를 잡 디렉토리 `scenes.json`으로 직렬화한다. DB 없음.

**Files:**
- Modify: `apps/server/domain/video_captions/pipeline.py` (헬퍼 추가)
- Test: `apps/server/tests/test_video_scene_split.py` (append)

**Interfaces:**
- Consumes: `job_dir` (기존 pipeline.py)
- Produces:
  - `scenes_json_path(external_id: UUID | str) -> Path`
  - `save_scenes(external_id: UUID | str, data: dict) -> None`
  - `load_scenes(external_id: UUID | str) -> dict | None`

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_video_scene_split.py 에 추가
from uuid import uuid4

from apps.server.domain.video_captions import pipeline as pl


def test_save_and_load_scenes_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    ext = uuid4()
    (tmp_path / "video_jobs" / str(ext)).mkdir(parents=True)
    assert pl.load_scenes(ext) is None
    pl.save_scenes(ext, {"rule": {"seq_tokens": [1]}, "segments_scene": []})
    loaded = pl.load_scenes(ext)
    assert loaded["rule"]["seq_tokens"] == [1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/server/tests/test_video_scene_split.py -k scenes_roundtrip -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'save_scenes'`

- [ ] **Step 3: Write implementation** (pipeline.py `job_dir` 함수 뒤에 추가; 파일 상단 import에 `import json` 추가 — 이미 있으면 생략)

```python
def scenes_json_path(external_id: UUID | str) -> Path:
    return job_dir(external_id) / "scenes.json"


def save_scenes(external_id: UUID | str, data: dict) -> None:
    path = scenes_json_path(external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_scenes(external_id: UUID | str) -> dict | None:
    path = scenes_json_path(external_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest apps/server/tests/test_video_scene_split.py -k scenes_roundtrip -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/domain/video_captions/pipeline.py apps/server/tests/test_video_scene_split.py
git commit -m "feat(video): scenes.json 저장/로드 헬퍼"
```

---

### Task 5: 스캔·익스포트 파이프라인 오케스트레이션

`run_burn_job`을 본떠 두 asyncio 태스크를 추가한다. 스캔은 프레임 추출→OCR→경계 계산→scenes.json 저장까지, 익스포트는 확정 경계로 세그먼트를 재인코딩한다. 세대(generation) + `_BURN_SEMAPHORE` 재사용으로 취소·직렬화를 기존과 동일하게 처리한다.

**Files:**
- Modify: `apps/server/domain/video_captions/pipeline.py`
- Test: `apps/server/tests/test_video_scene_split.py` (append — OCR/ffmpeg를 monkeypatch)

**Interfaces:**
- Consumes: `extract_frames`, `extract_thumbnails`, `cut_segment`, `locate_ffmpeg` (Task 3); `read_slate_line` (Task 2); `scene_split.*` (Task 1); `save_scenes`/`load_scenes` (Task 4); 기존 `_bump_generation`, `_current_generation`, `_BURN_SEMAPHORE`, `_set_status`, `_set_progress`, `job_dir`.
- Produces:
  - `run_scene_scan(external_id: UUID, interval_s: float = 1.0) -> None`
  - `run_scene_export(external_id: UUID, mode: str, rule: dict) -> None`
  - `build_scene_data(external_id, samples, rule_dict) -> dict` — 순수 조립(테스트용): frames + segments_scene + segments_sequence.

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_video_scene_split.py 에 추가
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/server/tests/test_video_scene_split.py -k build_scene_data -v`
Expected: FAIL — `AttributeError: ... build_scene_data`

- [ ] **Step 3: Write implementation** (pipeline.py 에 추가)

pipeline.py 상단 import 블록에 추가:

```python
from .scene_split import (
    FrameSample, SlateRule, compute_boundaries, hold_keys,
)
from .slate_ocr import read_slate_line
from .ffmpeg import cut_segment, extract_frames, extract_thumbnails
```

구현 (파일 하단, `run_burn_job` 뒤):

```python
def build_scene_data(samples: list[FrameSample], rule_dict: dict,
                     total_ms: int, min_ms: int = 2000) -> dict:
    """프레임 샘플 + 규칙 → scenes.json 본문(양 모드 경계 포함). 순수 함수."""
    rule = SlateRule(
        delimiters=rule_dict.get("delimiters", ["_", " ", "-"]),
        seq_tokens=rule_dict["seq_tokens"],
        scene_tokens=rule_dict.get("scene_tokens", []),
    )
    scene_keyed = hold_keys(samples, rule, "scene")
    seq_keyed = hold_keys(samples, rule, "sequence")
    seg_scene = compute_boundaries(scene_keyed, total_ms, min_ms)
    seg_seq = compute_boundaries(seq_keyed, total_ms, min_ms)
    return {
        "rule": rule_dict,
        "frames": [{"t_ms": s.t_ms, "text": s.text} for s in samples],
        "segments_scene": [
            {"label": s.label, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in seg_scene],
        "segments_sequence": [
            {"label": s.label, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in seg_seq],
    }


# 슬레이트 스캔 기본 규칙 — 사용자가 규칙 지정 전, 첫 스캔은 규칙 없이 프레임
# 텍스트만 수집한다(경계는 규칙 확정 시 계산). 구분자는 관측된 두 포맷 커버.
_DEFAULT_DELIMS = ["_", " ", "-"]


async def run_scene_scan(external_id: UUID, interval_s: float = 1.0) -> None:
    """burned.mp4에서 프레임을 추출·OCR해 프레임별 슬레이트 텍스트를 모아
    scenes.json(frames만)에 저장한다. 경계는 규칙 확정(run_scene_export 전
    /scenes/rule) 때 계산한다. 진행률은 burning 채널을 재사용하지 않고
    별도 상태 없이 scenes.json 존재로 완료를 판단한다(스캔은 굽기와 배타)."""
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    try:
        workdir = job_dir(external_id)
        burned = workdir / "burned.mp4"
        if not burned.exists():
            raise RuntimeError("굽기 완료본(burned.mp4)이 없습니다.")
        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")

        frames_dir = workdir / "scene_frames"
        thumbs_dir = workdir / "scene_thumbs"
        # 이전 스캔 잔여 제거
        for d in (frames_dir, thumbs_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

        def _work() -> list[FrameSample]:
            extract_frames(ffmpeg, burned, frames_dir, interval_s,
                           proc_key=str(external_id))
            extract_thumbnails(ffmpeg, burned, thumbs_dir, interval_s,
                               proc_key=str(external_id))
            samples: list[FrameSample] = []
            interval_ms = int(interval_s * 1000)
            for i, png in enumerate(sorted(frames_dir.glob("frame_*.png"))):
                if generation != _current_generation(external_id):
                    raise StaleRunCancelled(external_id)
                text = read_slate_line(png, _DEFAULT_DELIMS)
                samples.append(FrameSample(index=i, t_ms=i * interval_ms, text=text))
            return samples

        samples = await asyncio.to_thread(_work)
        # OCR용 원본 프레임은 크므로 제거(썸네일만 남긴다)
        shutil.rmtree(frames_dir, ignore_errors=True)

        save_scenes(external_id, {
            "interval_ms": int(interval_s * 1000),
            "frame_count": len(samples),
            "frames": [{"t_ms": s.t_ms, "text": s.text} for s in samples],
        })
    except StaleRunCancelled:
        logger.info("scene scan %s cancelled (gen %d)", external_id, generation)
    except Exception:  # noqa: BLE001
        logger.exception("scene scan %s failed", external_id)
        raise
    finally:
        _BURN_SEMAPHORE.release()


async def run_scene_export(external_id: UUID, mode: str,
                           out_dir: str | None = None) -> list[str]:
    """확정된 scenes.json 경계로 세그먼트를 재인코딩해 out_dir(미지정 시 잡
    디렉토리 scene_out/)에 슬레이트 라벨 파일명으로 저장한다. 저장 경로 목록 반환."""
    await _BURN_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    try:
        data = load_scenes(external_id)
        if not data:
            raise RuntimeError("먼저 씬 스캔을 실행하세요.")
        key = "segments_sequence" if mode == "sequence" else "segments_scene"
        segments = data.get(key) or []
        if not segments:
            raise RuntimeError("자를 세그먼트가 없습니다 — 규칙을 확정하세요.")

        workdir = job_dir(external_id)
        burned = workdir / "burned.mp4"
        ffmpeg = locate_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")
        dest = Path(out_dir) if out_dir else (workdir / "scene_out")
        dest.mkdir(parents=True, exist_ok=True)

        def _work() -> list[str]:
            written: list[str] = []
            for seg in segments:
                if generation != _current_generation(external_id):
                    raise StaleRunCancelled(external_id)
                safe = _sanitize_label(seg["label"])
                out_path = dest / f"{safe}.mp4"
                cut_segment(ffmpeg, burned, out_path,
                            seg["start_ms"], seg["end_ms"],
                            proc_key=str(external_id))
                written.append(str(out_path))
            return written

        return await asyncio.to_thread(_work)
    except StaleRunCancelled:
        logger.info("scene export %s cancelled (gen %d)", external_id, generation)
        return []
    finally:
        _BURN_SEMAPHORE.release()


def _sanitize_label(label: str) -> str:
    """파일명 안전화 — 경로 구분자·제어문자 제거. 공백은 유지(슬레이트 원문 존중),
    빈 라벨은 'segment'로 폴백."""
    bad = '/\\:*?"<>|\n\r\t'
    cleaned = "".join("_" if c in bad else c for c in label).strip()
    return cleaned or "segment"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest apps/server/tests/test_video_scene_split.py -k build_scene_data -v`
Expected: PASS

- [ ] **Step 5: Full pure-core suite green**

Run: `python -m pytest apps/server/tests/test_video_scene_split.py apps/server/tests/test_video_scene_ffmpeg.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add apps/server/domain/video_captions/pipeline.py apps/server/tests/test_video_scene_split.py
git commit -m "feat(video): 씬 스캔·익스포트 파이프라인 오케스트레이션"
```

---

### Task 6: API 엔드포인트

`video_jobs.py`에 스캔·규칙·경계수정·익스포트·썸네일 엔드포인트를 추가한다. 기존 무인증·`_get_job_or_404`·테스트 시임 패턴을 따른다.

**Files:**
- Modify: `apps/server/api/v1/video_jobs.py`
- Test: `apps/server/tests/test_api_video_jobs.py` (Task 7)

**Interfaces:**
- Consumes: `run_scene_scan`, `run_scene_export`, `build_scene_data`, `load_scenes`, `save_scenes`, `job_dir`, `start_job_task` (pipeline); `FrameSample` (scene_split).
- Produces (HTTP):
  - `POST /video-jobs/{id}/scenes/scan` → `{"status": "scanning"}` (202)
  - `GET  /video-jobs/{id}/scenes` → `{"scanned": bool, "frames": [...], "segments_scene": [...], "segments_sequence": [...], "rule": {...}|null}`
  - `POST /video-jobs/{id}/scenes/rule` (body: `SlateRuleIn`) → 경계 재계산 결과
  - `PATCH /video-jobs/{id}/scenes/segments` (body: `SegmentsOverrideIn`) → `{"updated": true}`
  - `POST /video-jobs/{id}/scenes/export` (body: `SceneExportIn`) → `{"files": [...], "count": n}`
  - `GET  /video-jobs/{id}/scenes/thumb/{index}` → 썸네일 JPG (capability URL)

- [ ] **Step 1: Write implementation** (video_jobs.py)

import 블록(29–34행 pipeline import)에 심볼 추가:

```python
from apps.server.domain.video_captions.pipeline import (RETENTION_KEEP,
                                                         build_scene_data,
                                                         cancel_job_task, job_dir,
                                                         load_scenes,
                                                         prune_old_video_jobs,
                                                         run_burn_job, run_scene_export,
                                                         run_scene_scan, run_video_job,
                                                         save_scenes, start_job_task,
                                                         start_task, video_jobs_root)
from apps.server.domain.video_captions.scene_split import FrameSample
```

`_start_burn` 테스트 시임 뒤에 추가:

```python
def _start_scene_scan(external_id: UUID) -> None:  # test seam
    start_job_task(external_id, run_scene_scan(external_id))


def _start_scene_export(external_id: UUID, mode: str, out_dir: str | None) -> None:  # test seam
    start_job_task(external_id, run_scene_export(external_id, mode, out_dir))
```

Pydantic 모델 (`BurnIn` 클래스 뒤):

```python
class SlateRuleIn(BaseModel):
    delimiters: list[str] = Field(default_factory=lambda: ["_", " ", "-"])
    seq_tokens: list[int]
    scene_tokens: list[int] = Field(default_factory=list)
    min_ms: int = Field(default=2000, ge=0, le=60000)


class SegmentOverride(BaseModel):
    label: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


class SegmentsOverrideIn(BaseModel):
    mode: str = Field(pattern="^(scene|sequence)$")
    segments: list[SegmentOverride]


class SceneExportIn(BaseModel):
    mode: str = Field(pattern="^(scene|sequence)$")
    out_dir: str | None = None
```

엔드포인트 (`download_video_job` 앞, 또는 `/media` 뒤):

```python
@router.post("/{external_id}/scenes/scan", status_code=status.HTTP_202_ACCEPTED)
async def scan_scenes(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await _get_job_or_404(db, external_id)
    if job.status != "done" or not job.burned_path or not Path(job.burned_path).exists():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "씬 분할은 굽기 완료(done)된 작업에서만 가능합니다.")
    _start_scene_scan(external_id)
    return {"status": "scanning"}


@router.get("/{external_id}/scenes")
async def get_scenes(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _get_job_or_404(db, external_id)
    data = load_scenes(external_id)
    if not data:
        return {"scanned": False, "frames": [], "segments_scene": [],
                "segments_sequence": [], "rule": None}
    return {
        "scanned": True,
        "frames": data.get("frames", []),
        "segments_scene": data.get("segments_scene", []),
        "segments_sequence": data.get("segments_sequence", []),
        "rule": data.get("rule"),
        "interval_ms": data.get("interval_ms", 1000),
    }


@router.post("/{external_id}/scenes/rule")
async def set_scene_rule(
    external_id: UUID,
    body: SlateRuleIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _get_job_or_404(db, external_id)
    data = load_scenes(external_id)
    if not data or not data.get("frames"):
        raise HTTPException(status.HTTP_409_CONFLICT, "먼저 씬 스캔을 실행하세요.")
    interval_ms = data.get("interval_ms", 1000)
    samples = [FrameSample(index=i, t_ms=f["t_ms"], text=f.get("text", ""))
               for i, f in enumerate(data["frames"])]
    total_ms = (samples[-1].t_ms + interval_ms) if samples else 0
    rule_dict = body.model_dump()
    scene_data = build_scene_data(samples, rule_dict, total_ms, body.min_ms)
    scene_data["interval_ms"] = interval_ms
    save_scenes(external_id, scene_data)
    return {"segments_scene": scene_data["segments_scene"],
            "segments_sequence": scene_data["segments_sequence"],
            "rule": rule_dict}


@router.patch("/{external_id}/scenes/segments")
async def override_scene_segments(
    external_id: UUID,
    body: SegmentsOverrideIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    await _get_job_or_404(db, external_id)
    data = load_scenes(external_id)
    if not data:
        raise HTTPException(status.HTTP_409_CONFLICT, "먼저 씬 스캔을 실행하세요.")
    key = "segments_sequence" if body.mode == "sequence" else "segments_scene"
    data[key] = [s.model_dump() for s in body.segments]
    save_scenes(external_id, data)
    return {"updated": True}


@router.post("/{external_id}/scenes/export", status_code=status.HTTP_202_ACCEPTED)
async def export_scenes(
    external_id: UUID,
    body: SceneExportIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await _get_job_or_404(db, external_id)
    if job.status != "done":
        raise HTTPException(status.HTTP_409_CONFLICT, "완료된 작업만 익스포트할 수 있습니다.")
    data = load_scenes(external_id)
    key = "segments_sequence" if body.mode == "sequence" else "segments_scene"
    if not data or not (data.get(key) or []):
        raise HTTPException(status.HTTP_409_CONFLICT, "자를 세그먼트가 없습니다 — 규칙을 확정하세요.")
    _start_scene_export(external_id, body.mode, body.out_dir)
    return {"status": "exporting", "count": len(data[key])}


@router.get("/{external_id}/scenes/thumb/{index}")
async def scene_thumbnail(
    external_id: UUID,
    index: int,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    await _get_job_or_404(db, external_id)
    # 썸네일은 1-based(thumb_00001.jpg) — index는 0-based 프레임 인덱스
    path = job_dir(external_id) / "scene_thumbs" / f"thumb_{index + 1:05d}.jpg"
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thumbnail not found")
    return FileResponse(path, media_type="image/jpeg")
```

> 참고: 익스포트는 비동기 태스크라 202로 즉시 반환한다. 완료 확인은 클라가 `GET /scenes/export/status` 대신 파일 저장을 서버 측에서 하므로, MVP에서는 `scene_out/` 폴더에 저장 후 데스크톱이 그 폴더를 사용자가 고른 위치로 복사한다(Task 9·11에서 배선). 서버가 사용자 지정 절대경로(`out_dir`)로 바로 쓰는 경로는 데스크톱(로컬 서버) 전제에서만 유효.

- [ ] **Step 2: Import smoke check**

Run: `python -c "from apps.server.api.v1 import video_jobs; print('ok')"`
Expected: `ok` (문법·import 오류 없음)

- [ ] **Step 3: Commit**

```bash
git add apps/server/api/v1/video_jobs.py
git commit -m "feat(video): 씬 스캔·규칙·익스포트 API 엔드포인트"
```

---

### Task 7: API 엔드포인트 테스트

기존 `test_api_video_jobs.py`의 httpx + 테스트 시임 패턴으로 엔드포인트를 검증한다. 실제 ffmpeg/OCR은 시임 monkeypatch로 대체.

**Files:**
- Modify: `apps/server/tests/test_api_video_jobs.py`

**Interfaces:**
- Consumes: 기존 테스트의 app/client 픽스처, `_start_scene_scan`/`_start_scene_export` 시임.

- [ ] **Step 1: Read existing test fixtures**

Run: `python -m pytest apps/server/tests/test_api_video_jobs.py -v --collect-only`
Expected: 기존 테스트 목록 출력 — 픽스처(`client`, done 잡 생성 헬퍼) 이름 확인.

- [ ] **Step 2: Write tests** (파일 기존 스타일에 맞춰 추가 — 아래는 골격; 실제 픽스처명은 Step 1 확인값 사용)

```python
def test_scan_scenes_requires_done_status(client, make_job):
    job = make_job(status="review")  # 기존 헬퍼 사용
    r = client.post(f"/api/v1/video-jobs/{job}/scenes/scan")
    assert r.status_code == 409


def test_get_scenes_empty_before_scan(client, make_job):
    job = make_job(status="done")
    r = client.get(f"/api/v1/video-jobs/{job}/scenes")
    assert r.status_code == 200
    assert r.json()["scanned"] is False


def test_set_rule_computes_boundaries(client, make_job, monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    job = make_job(status="done")
    from apps.server.domain.video_captions import pipeline as pl
    (tmp_path / "video_jobs" / job).mkdir(parents=True, exist_ok=True)
    pl.save_scenes(job, {"interval_ms": 1000, "frames": [
        {"t_ms": 0, "text": "HH0307_020_0150_AC_v01"},
        {"t_ms": 1000, "text": "HH0307_020_0170_AC_v01"},
        {"t_ms": 2000, "text": "HH0307_021_0010_AC_v01"},
    ]})
    r = client.post(f"/api/v1/video-jobs/{job}/scenes/rule",
                    json={"seq_tokens": [1], "scene_tokens": [2], "min_ms": 0})
    assert r.status_code == 200
    labels = [s["label"] for s in r.json()["segments_scene"]]
    assert labels == ["HH0307_020_0150", "HH0307_020_0170", "HH0307_021_0010"]


def test_export_starts_task(client, make_job, monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    job = make_job(status="done")
    calls = []
    from apps.server.api.v1 import video_jobs
    monkeypatch.setattr(video_jobs, "_start_scene_export",
                        lambda ext, mode, out: calls.append((str(ext), mode, out)))
    from apps.server.domain.video_captions import pipeline as pl
    (tmp_path / "video_jobs" / job).mkdir(parents=True, exist_ok=True)
    pl.save_scenes(job, {"segments_scene": [
        {"label": "HH0307_020_0150", "start_ms": 0, "end_ms": 3000}]})
    r = client.post(f"/api/v1/video-jobs/{job}/scenes/export",
                    json={"mode": "scene"})
    assert r.status_code == 202
    assert calls and calls[0][1] == "scene"
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest apps/server/tests/test_api_video_jobs.py -k scene -v`
Expected: PASS (기존 픽스처명이 다르면 Step 1 확인값으로 수정 후 재실행)

- [ ] **Step 4: Full server test suite**

Run: `python -m pytest apps/server/tests/ -q`
Expected: 기존 + 신규 테스트 모두 PASS (회귀 없음)

- [ ] **Step 5: Commit**

```bash
git add apps/server/tests/test_api_video_jobs.py
git commit -m "test(video): 씬 분할 API 엔드포인트 테스트"
```

---

### Task 8: 번들 배선 (RapidOCR + 네이티브 의존성)

PyInstaller freeze에 RapidOCR + 모델 파일 + **네이티브 의존성**을 포함한다.

> ⚠️ **RapidOCR은 순수 파이썬이 아니다.** rapidocr-onnxruntime 1.4.x 의존성:
> `onnxruntime`(이미 번들 ✓), `opencv-python`(네이티브 DLL/so), `Shapely`(GEOS),
> `pyclipper`(C++), `Pillow`(네이티브), `PyYAML`, `numpy`(이미 번들), `six`, `tqdm`.
> `--collect-all rapidocr_onnxruntime` **한 줄로는 부족하다** — 네이티브 의존성의
> 바이너리와 RapidOCR의 `.onnx` 모델·`config.yaml`(패키지 상대경로 로드 — 프로즌에서
> 어긋나기 쉬움, MLX metallib 함정과 동류)을 모두 수집·검증해야 한다.
>
> **Linux 서버 함정:** `opencv-python`(비-headless)은 `import cv2` 시 `libGL.so.1`을
> 요구해 GUI 없는 서버에서 즉사한다. **빌드 venv에서 `opencv-python`을
> `opencv-python-headless`로 교체**한다(같은 `cv2` 모듈을 libGL 없이 제공 — RapidOCR은
> `import cv2`만 하므로 무손실). 이는 전 플랫폼에 안전하다.

**Files:**
- Modify: `apps/server_desktop/scripts/build-server.sh`
- Modify: `apps/server_desktop/scripts/build-server.ps1`

**Interfaces:** 없음 (빌드 스크립트)

- [ ] **Step 1: 빌드 venv 의존성 설치에 headless opencv 교체 추가 (build-server.sh)**

build-server.sh의 `uv pip install ... ./apps/server "pyinstaller>=6.21"` 단계 **뒤**에, opencv를 headless로 교체하는 라인을 추가한다(rapidocr가 opencv-python을 끌어온 뒤 덮어쓴다):

```bash
# RapidOCR가 끌어온 opencv-python(비-headless)은 Linux에서 libGL.so.1을 요구해
# 서버 번들에서 import cv2가 즉사한다. 같은 cv2 모듈을 제공하는 headless로 교체.
VIRTUAL_ENV="${BUILD_VENV}" uv pip install --python "${BUILD_VENV}/bin/python" \
    opencv-python-headless
VIRTUAL_ENV="${BUILD_VENV}" uv pip uninstall --python "${BUILD_VENV}/bin/python" \
    opencv-python || true
```

- [ ] **Step 2: pyinstaller 인자에 collect-all 추가 (build-server.sh)**

`--collect-all onnxruntime \` 다음 줄들에 추가(네이티브 의존성 명시 수집):

```bash
    --collect-all onnxruntime \
    --collect-all rapidocr_onnxruntime \
    --collect-all shapely \
    --collect-all pyclipper \
    --collect-all cv2 \
    --collect-all PIL \
```

- [ ] **Step 3: build-server.ps1 미러링**

`build-server.ps1`에서 (a) 빌드 venv 설치 단계 뒤에 opencv headless 교체, (b) `--collect-all onnxruntime` 뒤에 동일한 5개 collect-all 라인을 추가한다.

> Windows는 `libGL` 문제가 없어 opencv 교체가 필수는 아니지만, **전 플랫폼 동일 의존성 그래프**를 유지하려면 headless로 통일하는 게 안전하다(headless는 Windows에서도 정상 동작).

Run: `grep -n "collect-all onnxruntime" apps/server_desktop/scripts/build-server.ps1`
Expected: 라인 위치 확인 후 동일 패턴으로 5개 라인 삽입.

- [ ] **Step 4: 로컬 import 스모크 (비-프로즌)**

Run:
```bash
cd apps/server && .venv/bin/python -c "import cv2, shapely, pyclipper, PIL, rapidocr_onnxruntime; print('deps ok')"
.venv/bin/python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('engine init ok')"
```
Expected: `deps ok` / `engine init ok` (모델 로드 성공 — 경로 해석 정상)

- [ ] **Step 5: Commit**

```bash
git add apps/server_desktop/scripts/build-server.sh apps/server_desktop/scripts/build-server.ps1
git commit -m "build(video): RapidOCR + 네이티브 의존성 번들(headless opencv/shapely/pyclipper)"
```

- [ ] **Step 6: 프로즌 스모크 (플랫폼별 — 릴리스 검증 항목)**

각 플랫폼에서 번들 빌드 후, 번들 파이썬으로 RapidOCR가 실제 초기화·판독되는지 확인한다. 이것이 "모델 경로가 프로즌에서 풀리는가"를 잡는 유일한 관문이다:

- **Mac/Linux:** `build-server.sh`로 빌드 → `dist/yeson-server/`에서 서버 기동 후 실제 씬 스캔 1회(Task 12에서 통합). 최소한 번들 내 `_internal/rapidocr_onnxruntime/` 에 `.onnx` 모델이 있는지 확인.
- **Windows:** `build-server.ps1`로 빌드 → 번들에서 `import cv2` + RapidOCR 초기화 성공 + 테스트 이미지 1장 OCR 확인.

> 프로즌 빌드 검증은 릴리스 절차(release 스킬)와 Task 12 실기 검증에서 수행 — 이 태스크는 스크립트 배선 + 로컬 import 스모크까지. **모델 경로가 프로즌에서 안 풀리면**(로그에 `.onnx not found`/`config.yaml not found`), `--add-data`로 `rapidocr_onnxruntime/models`·`config.yaml`을 명시 경로에 복사하는 폴백이 필요하다.

---

### Task 9: 프론트 API 클라이언트

`videoApi.ts`에 씬 분할 호출 함수와 타입을 추가한다.

**Files:**
- Modify: `apps/desktop/src/console/videoApi.ts`

**Interfaces:**
- Produces:
  - 타입 `SceneSegment`, `ScenesData`, `SlateRuleInput`
  - `scanScenes(jobId)`, `getScenes(jobId)`, `setSceneRule(jobId, rule)`, `overrideSceneSegments(jobId, mode, segments)`, `exportScenes(jobId, mode, outDir?)`
  - `sceneThumbUrl(jobId, index)`

- [ ] **Step 1: Write implementation** (videoApi.ts 파일 끝 `videoDownloadUrl` 뒤에 추가)

```typescript
export type SceneSegment = { label: string; start_ms: number; end_ms: number };

export type ScenesData = {
  scanned: boolean;
  frames: Array<{ t_ms: number; text: string }>;
  segments_scene: SceneSegment[];
  segments_sequence: SceneSegment[];
  rule: SlateRuleInput | null;
  interval_ms?: number;
};

export type SlateRuleInput = {
  delimiters?: string[];
  seq_tokens: number[];
  scene_tokens?: number[];
  min_ms?: number;
};

export async function scanScenes(jobId: string): Promise<void> {
  await request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/scan`,
    { method: "POST" });
}

export async function getScenes(jobId: string): Promise<ScenesData> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes`, {});
}

export async function setSceneRule(
  jobId: string, rule: SlateRuleInput,
): Promise<{ segments_scene: SceneSegment[]; segments_sequence: SceneSegment[] }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/rule`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rule),
  });
}

export async function overrideSceneSegments(
  jobId: string, mode: "scene" | "sequence", segments: SceneSegment[],
): Promise<void> {
  await request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/segments`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, segments }),
  });
}

export async function exportScenes(
  jobId: string, mode: "scene" | "sequence", outDir?: string,
): Promise<{ status: string; count: number }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, out_dir: outDir ?? null }),
  });
}

export function sceneThumbUrl(jobId: string, index: number): string {
  return `${apiBase()}/api/v1/video-jobs/${jobId}/scenes/thumb/${index}`;
}
```

- [ ] **Step 2: Typecheck**

Run: `cd apps/desktop && pnpm exec tsc --noEmit`
Expected: 오류 없음 (신규 심볼 타입 정합)

- [ ] **Step 3: Commit**

```bash
git add apps/desktop/src/console/videoApi.ts
git commit -m "feat(video): 씬 분할 프론트 API 클라이언트"
```

---

### Task 10: 프론트 순수 로직 (토큰 미리보기 · 라벨)

규칙 지정 UI가 쓰는 순수 계산을 별도 모듈로 빼 vitest로 잠근다.

**Files:**
- Create: `apps/desktop/src/console/sceneSplitLogic.ts`
- Create: `apps/desktop/src/console/sceneSplitLogic.test.ts`

**Interfaces:**
- Produces:
  - `tokenizeSlate(text: string, delimiters?: string[]): string[]` — 백엔드 `tokenize`와 동일 규칙(미리보기용).
  - `previewLabel(tokens: string[], uptoIndex: number): string`
  - `formatMs(ms: number): string` — `0:23` / `1:52` 표기.

- [ ] **Step 1: Write the failing test**

```typescript
// apps/desktop/src/console/sceneSplitLogic.test.ts
import { describe, expect, it } from "vitest";
import { formatMs, previewLabel, tokenizeSlate } from "./sceneSplitLogic";

describe("tokenizeSlate", () => {
  it("splits underscore slate", () => {
    expect(tokenizeSlate("HH0307_020_0150_AC_v01")).toEqual(
      ["HH0307", "020", "0150", "AC", "v01"]);
  });
  it("splits mixed delimiters without empty tokens", () => {
    expect(tokenizeSlate("Seq 07_S08 - Panel 3")).toEqual(
      ["Seq", "07", "S08", "Panel", "3"]);
  });
});

describe("previewLabel", () => {
  it("joins prefix through upto index with underscore", () => {
    const t = ["HH0307", "020", "0150", "AC", "v01"];
    expect(previewLabel(t, 1)).toBe("HH0307_020");
    expect(previewLabel(t, 2)).toBe("HH0307_020_0150");
  });
});

describe("formatMs", () => {
  it("formats mm:ss", () => {
    expect(formatMs(23000)).toBe("0:23");
    expect(formatMs(112000)).toBe("1:52");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/desktop && pnpm exec vitest run src/console/sceneSplitLogic.test.ts`
Expected: FAIL — 모듈 없음

- [ ] **Step 3: Write implementation**

```typescript
// apps/desktop/src/console/sceneSplitLogic.ts
// 슬레이트 씬 분할 규칙 UI의 순수 계산. 백엔드 scene_split.tokenize와 동일 규칙
// (미리보기 일치 — 실제 경계 계산은 서버가 단일 출처).
const DEFAULT_DELIMITERS = ["_", " ", "-"];

export function tokenizeSlate(
  text: string, delimiters: string[] = DEFAULT_DELIMITERS,
): string[] {
  const escaped = delimiters.map((d) => d.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const parts = escaped.length ? text.split(new RegExp(escaped.join("|"))) : [text];
  return parts.map((s) => s.trim()).filter((s) => s.length > 0);
}

export function previewLabel(tokens: string[], uptoIndex: number): string {
  if (uptoIndex < 0 || uptoIndex >= tokens.length) return "";
  return tokens.slice(0, uptoIndex + 1).join("_");
}

export function formatMs(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/desktop && pnpm exec vitest run src/console/sceneSplitLogic.test.ts`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/console/sceneSplitLogic.ts apps/desktop/src/console/sceneSplitLogic.test.ts
git commit -m "feat(video): 씬 분할 프론트 순수 로직 + 테스트"
```

---

### Task 11: 필름스트립 UI + 결과보기 진입

규칙 지정 → 필름스트립 검증 → 익스포트 화면과 진입 버튼. React 컴포넌트라 단위 테스트 대신 타입체크 + 수동 검증.

**Files:**
- Create: `apps/desktop/src/console/SceneFilmstrip.tsx`
- Create: `apps/desktop/src/console/SceneSplitView.tsx`
- Modify: `apps/desktop/src/console/VideoReviewView.tsx`

**Interfaces:**
- Consumes: Task 9 API 함수, Task 10 순수 로직, 기존 `consoleStyles`, `hasTauriRuntime`.
- Produces: `SceneSplitView({ jobId, onBack })`, `SceneFilmstrip({ segments, thumbUrl, thumbCount, mode, onSegmentsChange })`.

- [ ] **Step 1: SceneFilmstrip.tsx** — 썸네일 트랙 + 컷 라인 + 라벨

```tsx
// apps/desktop/src/console/SceneFilmstrip.tsx
import { consoleStyles } from "./consoleStyles";
import { formatMs } from "./sceneSplitLogic";
import { sceneThumbUrl, type SceneSegment } from "./videoApi";

type Props = {
  jobId: string;
  segments: SceneSegment[];
  thumbCount: number;
  intervalMs: number;
  totalMs: number;
};

// 다빈치 리졸브식 필름스트립: 썸네일을 시간축에 깔고 세그먼트 경계를 세로선으로
// 얹는다. MVP는 읽기 전용(확인용) — 드래그 조정은 후속(Task 밖, 스펙 8절).
export function SceneFilmstrip({ jobId, segments, thumbCount, intervalMs, totalMs }: Props) {
  const thumbs = Array.from({ length: thumbCount }, (_, i) => i);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", overflowX: "auto", gap: 1,
                    background: "#000", borderRadius: 6, padding: 2 }}>
        {thumbs.map((i) => (
          <img key={i} src={sceneThumbUrl(jobId, i)} alt=""
               style={{ height: 64, flexShrink: 0 }} />
        ))}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {segments.map((s, i) => (
          <div key={i} style={{ display: "flex", gap: 8, fontSize: 13,
                                justifyContent: "space-between",
                                padding: "3px 8px", borderRadius: 4,
                                background: "rgba(255,255,255,0.05)" }}>
            <span style={{ fontFamily: "monospace", overflowWrap: "anywhere" }}>{s.label}</span>
            <span style={{ opacity: 0.7, flexShrink: 0 }}>
              {formatMs(s.start_ms)}–{formatMs(s.end_ms)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: SceneSplitView.tsx** — 스캔·규칙·모드·익스포트

```tsx
// apps/desktop/src/console/SceneSplitView.tsx
import { useEffect, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import { hasTauriRuntime } from "./sessionApi";
import { previewLabel, tokenizeSlate } from "./sceneSplitLogic";
import { SceneFilmstrip } from "./SceneFilmstrip";
import {
  exportScenes, getScenes, scanScenes, setSceneRule,
  type ScenesData, type SceneSegment,
} from "./videoApi";

type Mode = "scene" | "sequence";

export function SceneSplitView({ jobId, onBack }: { jobId: string; onBack: () => void }) {
  const [data, setData] = useState<ScenesData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("scene");
  const [seqIdx, setSeqIdx] = useState<number[]>([]);
  const [sceneIdx, setSceneIdx] = useState<number[]>([]);

  const refresh = async () => setData(await getScenes(jobId));
  useEffect(() => { void refresh(); }, [jobId]);

  // 대표 프레임 = 첫 비어있지 않은 OCR 텍스트
  const sample = data?.frames.find((f) => f.text)?.text ?? "";
  const tokens = tokenizeSlate(sample);

  const runScan = async () => {
    setBusy(true); setError(null); setNotice("프레임 스캔·OCR 중…");
    try {
      await scanScenes(jobId);
      // 스캔은 비동기 — 폴링으로 완료 대기
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        const d = await getScenes(jobId);
        if (d.scanned) { setData(d); setNotice("스캔 완료 — 토큰을 지정하세요."); return; }
      }
      setError("스캔이 시간 내 끝나지 않았습니다.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const applyRule = async () => {
    setBusy(true); setError(null);
    try {
      const res = await setSceneRule(jobId, {
        seq_tokens: seqIdx, scene_tokens: sceneIdx,
      });
      setData({ ...(data as ScenesData), scanned: true,
                segments_scene: res.segments_scene,
                segments_sequence: res.segments_sequence });
      setNotice("경계를 계산했습니다.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const doExport = async () => {
    setBusy(true); setError(null); setNotice(null);
    try {
      let outDir: string | undefined;
      if (hasTauriRuntime()) {
        const { open } = await import("@tauri-apps/plugin-dialog");
        const picked = await open({ directory: true });
        if (!picked || Array.isArray(picked)) { setNotice("저장 폴더 선택이 취소되었습니다."); return; }
        outDir = picked;
      }
      const res = await exportScenes(jobId, mode, outDir);
      setNotice(`${res.count}개 클립을 내보내는 중… (${outDir ?? "서버 폴더"})`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const segments: SceneSegment[] = data
    ? (mode === "sequence" ? data.segments_sequence : data.segments_scene) : [];
  const toggle = (arr: number[], i: number, set: (v: number[]) => void) =>
    set(arr.includes(i) ? arr.filter((x) => x !== i) : [...arr, i].sort((a, b) => a - b));

  return (
    <div style={{ ...consoleStyles.panel, display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button type="button" style={{ ...consoleStyles.mutedAction, flexShrink: 0 }}
          onClick={onBack}>← 결과보기로</button>
        <h2 style={{ ...consoleStyles.title, margin: 0 }}>씬별 분할</h2>
      </div>
      {error ? <p style={{ color: "#e5484d", margin: 0 }}>{error}</p> : null}
      {notice ? <p style={consoleStyles.statusInfo}>{notice}</p> : null}

      {!data?.scanned ? (
        <button type="button" style={consoleStyles.action} disabled={busy}
          onClick={() => void runScan()}>
          {busy ? "스캔 중…" : "슬레이트 스캔 시작"}
        </button>
      ) : (
        <>
          {/* 규칙 지정: 토큰 칩 */}
          <div>
            <p style={{ fontSize: 13, opacity: 0.75, margin: "0 0 6px" }}>
              대표 슬레이트: <code>{sample || "(판독 실패)"}</code> — 시퀀스/씬 토큰을 고르세요.
            </p>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {tokens.map((tok, i) => (
                <span key={i} style={{
                  padding: "3px 8px", borderRadius: 6, fontFamily: "monospace",
                  border: "1px solid rgba(255,255,255,0.15)",
                  background: sceneIdx.includes(i) ? "#2b6cb0"
                    : seqIdx.includes(i) ? "#2f855a" : "transparent",
                }}>
                  {tok}
                  <button type="button" style={{ marginLeft: 6, fontSize: 11 }}
                    onClick={() => toggle(seqIdx, i, setSeqIdx)}>SEQ</button>
                  <button type="button" style={{ marginLeft: 4, fontSize: 11 }}
                    onClick={() => toggle(sceneIdx, i, setSceneIdx)}>SCENE</button>
                </span>
              ))}
            </div>
            <p style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>
              시퀀스 라벨 미리보기: <code>{previewLabel(tokens, Math.max(-1, ...seqIdx))}</code>
              {"  ·  "}씬 라벨: <code>{previewLabel(tokens, Math.max(-1, ...seqIdx, ...sceneIdx))}</code>
            </p>
            <button type="button" style={{ ...consoleStyles.mutedAction, marginTop: 8 }}
              disabled={busy || seqIdx.length === 0}
              onClick={() => void applyRule()}>경계 계산</button>
          </div>

          {/* 모드 토글 + 필름스트립 */}
          <div style={{ display: "flex", gap: 16, alignItems: "center", fontSize: 13 }}>
            <label><input type="radio" checked={mode === "scene"}
              onChange={() => setMode("scene")} /> 씬별</label>
            <label><input type="radio" checked={mode === "sequence"}
              onChange={() => setMode("sequence")} /> 시퀀스별</label>
            <span style={{ opacity: 0.7 }}>{segments.length}개 구간</span>
          </div>
          <SceneFilmstrip jobId={jobId} segments={segments}
            thumbCount={data.frames.length}
            intervalMs={data.interval_ms ?? 1000}
            totalMs={(data.frames.at(-1)?.t_ms ?? 0) + (data.interval_ms ?? 1000)} />
          <button type="button" style={consoleStyles.action}
            disabled={busy || segments.length === 0}
            onClick={() => void doExport()}>
            {segments.length}개 클립 익스포트
          </button>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Wire entry button in VideoReviewView.tsx**

`VideoReviewView.tsx` 상단 import에 추가:

```tsx
import { SceneSplitView } from "./SceneSplitView";
```

컴포넌트 함수 본문 상단(다른 `useState` 근처)에 상태 추가:

```tsx
const [sceneSplit, setSceneSplit] = useState(false);
```

`return (` 직후, 최상위 렌더 앞에 분기 추가:

```tsx
if (sceneSplit) {
  return <SceneSplitView jobId={jobId} onBack={() => setSceneSplit(false)} />;
}
```

`job.status === "done"` 다운로드 버튼 블록(현재 362–377행, `SRT 다운로드` 버튼 뒤)에 버튼 추가:

```tsx
<button type="button" style={consoleStyles.mutedAction}
  onClick={() => setSceneSplit(true)}>
  씬별 분할
</button>
```

- [ ] **Step 4: Typecheck**

Run: `cd apps/desktop && pnpm exec tsc --noEmit`
Expected: 오류 없음

> 참고: `consoleStyles`·`hasTauriRuntime`·`statusInfo`의 정확한 export 경로는 `VideoReviewView.tsx` 기존 import를 참조해 맞춘다(예: `hasTauriRuntime`이 `sessionApi` 아닌 다른 모듈이면 그 경로 사용). `@tauri-apps/plugin-dialog`의 `open`은 이미 프로젝트에 설치돼 있다(다른 폴더 선택 흐름에서 사용 중).

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/console/SceneFilmstrip.tsx apps/desktop/src/console/SceneSplitView.tsx apps/desktop/src/console/VideoReviewView.tsx
git commit -m "feat(video): 씬별 분할 필름스트립 UI + 결과보기 진입"
```

---

### Task 12: 통합 실기 검증 (수동)

전 계층을 실제 영상으로 관통 검증한다. verify 스킬 흐름.

**Files:** 없음 (검증)

- [ ] **Step 1: 서버 재동결 + 데스크톱 실행**

`apps/server` 소스를 바꿨으므로 데스크톱이 번들 바이너리를 쓰면 재동결이 필요하다. 개발 검증은 `pnpm tauri:dev`로도 되지만 번들 사이드카 경로 확인이 필요하면 build-server 후 실행.

Run: `cd apps/server_desktop && pnpm tauri:dev`

- [ ] **Step 2: 슬레이트 있는 실제 영상으로 관통**

1. 자막메이커로 슬레이트 있는 영상을 업로드 → 전사·번역·굽기(done)까지.
2. 결과보기에서 "씬별 분할" → "슬레이트 스캔 시작". OCR이 프레임별 슬레이트를 읽는지 확인(대표 슬레이트 텍스트 표시).
3. 토큰 칩에서 시퀀스/씬 지정 → "경계 계산" → 필름스트립에 구간·라벨·시간이 맞는지.
4. 씬별/시퀀스별 토글 시 구간 수가 바뀌는지.
5. "익스포트" → 폴더 선택 → 슬레이트 이름 파일들이 생성되는지, 각 클립에 슬레이트+한글자막이 남아있는지, 컷 경계가 프레임 정확한지.

- [ ] **Step 3: 컷 정확도 확인**

익스포트된 클립의 시작/끝이 의도한 샷 경계와 맞는지 육안 확인. 어긋나면 Task 3의 `cut_segment`에서 `-ss`를 `-i` 뒤(출력 시킹)로 옮겨 재검증.

- [ ] **Step 4: 두 슬레이트 포맷 모두 확인**

`HH0307_020_0150_AC_v01` 형식과 `Seq 07_S08 - Panel 3` 형식 각각에서 토큰 지정 → 경계·파일명이 맞는지.

- [ ] **Step 5: Commit (필요 시 조정 반영)**

검증 중 발견한 미세 조정을 반영하고 커밋. 문제 없으면 이 태스크는 검증 기록만 남긴다.

---

## Self-Review

**Spec coverage:**
- OCR 판독(RapidOCR) → Task 2 ✓
- 사용자 규칙 지정(토큰) → Task 1(코어)·10(미리보기)·11(UI) ✓
- 필름스트립 검증 UX → Task 11 ✓
- 시퀀스별/씬별 두 모드 → Task 1·5·11 ✓
- 재인코딩 정확 컷 → Task 3(`cut_segment`) ✓
- scenes.json 저장(새 DB 없음) → Task 4 ✓
- API → Task 6·7 ✓
- 번들 collect-all → Task 8 ✓
- 두 실제 슬레이트 예시 회귀 잠금 → Task 1·10 테스트 ✓
- 에러/엣지(판독실패 홀드·1프레임 튐 흡수) → Task 1 ✓
- 슬레이트 없는 영상 안내 → Task 11(대표 슬레이트 "판독 실패" 표시) + Task 6(빈 frames 시 409) ✓
- 취소·직렬화 → Task 5(세대+세마포어) ✓

**미세 조정 메모(구현 중 확인):**
- Task 11의 `hasTauriRuntime`/`consoleStyles`/`statusInfo` import 경로는 `VideoReviewView.tsx` 기존 import에 맞춘다(추정 경로 표기).
- Task 7의 픽스처명(`client`/`make_job`)은 기존 `test_api_video_jobs.py` 실제 이름으로 교체.
- 익스포트 진행률/완료 통지는 MVP에서 notice 문자열로 갈음 — 정밀 진행바는 후속(스펙 8절 YAGNI).

**Type consistency:** `SceneSegment`(label/start_ms/end_ms)는 백엔드 `Segment`·`scenes.json`·API·프론트에서 동일 필드명 유지 ✓. `mode ∈ {"scene","sequence"}` 문자열이 전 계층 일관 ✓.
