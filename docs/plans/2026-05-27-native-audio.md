# Native Audio (Phase 0 Baseline + Phase 1 macOS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Execution Status (as of 2026-05-27)

| Tasks | Status | Notes |
|---|---|---|
| 1 — scenarios.md | ✅ done | commit `b3c2f9b` |
| 2-3 — baseline_collect.py + tests | ✅ done | commit `1d69686`; flat + `--schema v1` paths, `--allow-empty` covers silent |
| 4-5 — baseline_compare.py + tests | ✅ done | commit `c1d50ba`; flat + schema-v1 dotted-key paths |
| 6 — subtitleTiming.ts + wiring | ✅ done | commit `83a74f3`; wired into `useLiveSubtitleStream.applyMessage`; `window.__yesonTimingExport()` for DevTools |
| 7 — baseline measurements | ⚠ **manual** | Step 5 (`--allow-empty`) folded into Task 2-3 |
| 8 — Swift package skeleton | ✅ done | commit `45d0cd4`; **deviation**: split into `YesonMacAudioHelperKit` (lib) + `YesonMacAudioHelper` (thin exec). Plan's single-target setup triggered SIGILL in `xctest` on Swift 5.9 / macOS 14. All Swift sources for Tasks 9-14 live in `Sources/YesonMacAudioHelperKit/`, the executable shell in `Sources/YesonMacAudioHelper/`. |
| 9-10 — PCMConverter | ✅ done | commit `4d8a0a2`; `process()` loops to drain converter, streaming test confirms convergence |
| 11-12 — IPC framer | ✅ done | commit `a6b489c` |
| 13 — AudioCapture protocol | ✅ done | commit `2493034` |
| 14 — ScreenCaptureKitProvider | ✅ done | commit `df352c6` |
| 15 — Helper main entrypoint | ✅ done | commit `a5e7f33`; `main.swift` → `App.swift` (`@main async`) since top-level `await` isn't allowed in `main.swift`. Step 3 smoke skipped (needs Screen Recording grant — falls under manual Task 24). |
| 16 — build-release.sh | ✅ done | commit `636c6b3`; release build verified, 126 KB binary at `target/native-helper-mac/yeson-mac-audio-helper` |
| 17 — AudioSource ABC | ✅ done | commit `27f6a73` |
| 18 — SoundDeviceSource | ✅ done | commit `c306ff7` |
| 19-20 — NativePipeSource | ✅ done | commit `52c4ea9` |
| 21-22 — Provider factory + config | ✅ done | commit `dfb7fd5` |
| 23 — Wire factory into main.py | ✅ done | commit `6a8e449`; existing `test_audio_main_smoke.py` pins `YESON_AUDIO_PROVIDER=sounddevice` (auto would pick native when helper binary is built locally) |
| 24 — End-to-end smoke | ⚠ **manual** | requires dashboard dev + Screen Recording grant |
| 25 — Native re-measurements | ⚠ **manual** | requires Task 7 baselines + dashboard run |
| 26 — Comparison reports | ⚠ **manual** | depends on Task 25 outputs |

**Test totals (2026-05-27)**: Python 35 ✅ · Swift 8 ✅ · Vitest 3 ✅.

**Next decision**: run Task 7 (4 baseline scenarios) → native adoption / Task 24-25 GO/HOLD per Task 7 Step 7 exit-criteria table. Tasks 8-23 are already implemented, so this gates measurement/smoke continuation rather than Phase 1 coding start.

### Post-implementation review deltas (2026-05-28)

코드 리뷰에서 발견·수정된 사항. **본문 task 예제보다 커밋된 코드가 canonical.**

| # | 영역 | 변경 |
|---|---|---|
| F1 | Task 7 Step 1 / Step 7 | Phase 0 런타임을 `YESON_AUDIO_PROVIDER=sounddevice` 로 강제하고, 집계 명령을 `--schema v1`(+env 인자, `permission_state=not_applicable`)로 통일 → Step 7 표의 `ai.*`/`capture.*` 키와 정합. Step 7 표의 `chunks_per_sec_sustained` 의존 제거(도구가 `null`만 출력) → `audio_queue_drop_count` 분당 환산으로 판정. |
| F2 | `ScreenCaptureKitProvider` | SCStream 콜백 큐를 `.global()`(concurrent)→ 전용 serial `sampleQueue`. `pending` 데이터 레이스 제거. |
| F3 | `AudioCapture.start` / `App.swift` | `start(frameHandler:)`를 `throws`→`async throws`. `startCapture()`를 `await`해 실제 시작 후에만 `started` emit(early-started 레이스 제거). |
| F4 | `ScreenCaptureKitProvider` / Task 24 | 첫 버퍼에서 `audio_format_check: ... nonInterleaved=<bool> hasDataBuffer=<bool>` 1회 stderr 로그 추가. probe 를 `CMSampleBufferGetDataBuffer` guard **이전**으로 배치 → planar/buffer-nil 케이스도 항상 로깅(interleaved 가정은 여전히 Task 24 smoke 로 확정 필요). Task 24에 planar 검증·교체 절차 명시. |
| F5 | `baseline_collect.py` / Task 10 | flat `subtitle_full_*`가 실은 first-token latency임을 주석. Task 10 본문 PCMConverter는 단일-shot(stale), 커밋본은 drain 루프임을 명시. |
| F6 | Task 25 | native 재측정도 Task 7과 같은 `--schema v1` 출력으로 통일. flat native JSON을 만들면 `baseline_compare.py`가 schema mismatch로 비교를 거부한다. |

**Goal:** macOS 시스템 오디오를 BlackHole 없이 ScreenCaptureKit로 직접 캡처해 기존 sidecar 파이프라인에 흘리고, 도입 전·후 자막 latency 및 토큰 사용량을 정량 비교한다. Native 실패 시 BlackHole sounddevice 경로로 자동 fallback.

**Architecture:** Swift ScreenCaptureKit helper(별도 프로세스) → stdout 16 kHz mono PCM s16le 20 ms 청크 → Python sidecar의 `NativePipeSource`가 수신 → 기존 WebSocket 전송 그대로. Provider 선택은 `YESON_AUDIO_PROVIDER=native|sounddevice|auto` env로 제어. Phase 0에서 현 baseline 측정 후 Phase 1 완료 시 같은 시나리오로 재측정·비교.

**Tech Stack:** Swift 5.9+ (ScreenCaptureKit, AVAudioConverter), Python 3.12 (asyncio, subprocess), pytest + pytest-asyncio, sounddevice (fallback 유지), Tauri 2.x (resource bundling — release 단계만).

**References:**
- 상위 설계: `docs/INTEGRATION_DESIGN.md` §3, §4, §5
- 상위 방향: `docs/NATIVE_DESKTOP_HELPER_PLAN.md` Phase 0 / Phase 1
- **Baseline 출력 스키마 (v1, frozen)**: `docs/baselines/schema.md` — 모든 Phase 0/1 JSON 산출물의 source of truth
- 기존 코드 패턴: VibeLign 앵커(`# === ANCHOR: NAME_START === ... === ANCHOR: NAME_END ===`) 준수

---

## ⚠️ Schema Alignment (Phase 0 baseline 산출물)

본 plan 의 **Task 2~5 인라인 코드 예제** (`baseline_collect.py`, `baseline_compare.py`, 그리고 그 pytest fixture) 는 fast TDD 를 위해 **flat 키**(`subtitle_first_token_ms`, `subtitle_full_p50_ms`, ...)를 emit 한다. 하지만 **실제 측정 산출물(`docs/baselines/2026-MM-DD-<scenario>.json`)은 `docs/baselines/schema.md` v1 의 nested 구조**(`env.*` / `capture.*` / `ai.*` / `delivery.*` / `user_perceived.*`)를 따라야 한다.

구체 migration 책임:

| Task | 현재 인라인 예제 출력 | v1 schema 매핑 |
|---|---|---|
| 2/3 `baseline_collect.py` | `data["subtitle_first_token_ms"]` | `data["ai"]["gemini_connect_to_first_subtitle_ms_first"]` |
| 2/3 `baseline_collect.py` | `data["subtitle_full_p50_ms"]` / `p95_ms` | `data["ai"]["gemini_connect_to_first_subtitle_ms_p50"]` / `_p95` |
| 2/3 `baseline_collect.py` | `data["audio_queue_drop_count"]` | `data["capture"]["audio_queue_drop_count"]` |
| 2/3 `baseline_collect.py` | `data["gemini_segment_count"]` | `data["ai"]["gemini_segment_count"]` |
| 신규 | (없음) | `data["env"].*` — `--provider`, `--os`, `--os-version`, `--audio-route`, `--permission-state`, `--server-commit`, `--client-commit`, `--gemini-model`, `--gemini-modality` CLI 인자로 받기 |
| 신규 | (없음) | `data["user_perceived"].*` — 별도 wrap 단계: `--speech-onset-unix-ms` 인자와 첫 `Gemini Live first subtitle yielded` 의 wall-clock 차이를 계산해야 한다. **현재 커밋본은 raw onset 값을 그대로 넣으므로 이 인자는 사용하지 말고 `null` 유지**. PRD 지연 지표가 필요하면 먼저 true latency 계산을 구현한다. silent 시나리오는 `null`. |
| 신규 | (없음) | `data["delivery"]["client_timing_artifact"]` — `--client-timing` 인자로 viewer JSON 경로. 없으면 `null`. |
| 4/5 `baseline_compare.py` | flat `METRIC_KEYS` | dotted-path keys (`ai.gemini_connect_to_first_subtitle_ms_p50` 등). 비교 핵심 5 키는 schema §5 참조. |

**구현 순서 권고**:
1. Task 2/3 을 plan 그대로 TDD (flat keys) → green 확인
2. Task 3 끝에 **Step 4 추가**: parse_log 결과를 schema v1 nested 로 wrap 하는 `to_schema_v1(parsed, env_args, user_perceived_args)` 함수 + 해당 pytest 추가
3. Task 4/5 도 동일 패턴으로 nested 입력 가정
4. Task 7 (실측) 의 명령줄에 `--provider`, `--os`, `--os-version` 등 env 인자 채워서 호출

`schema_version: 1` 필드는 모든 산출물 JSON 의 첫 키여야 비교 스크립트가 mismatch 검출 가능.

---

## File Structure

### Phase 0 — Baseline

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `docs/baselines/scenarios.md` | 4개 시나리오 실측 프로토콜 |
| Create | `scripts/baseline_collect.py` | 서버 로그 파싱 → 시나리오별 JSON 지표 |
| Create | `scripts/baseline_compare.py` | baseline JSON vs native JSON → markdown 비교 리포트 |
| Create | `apps/desktop/src/timing/subtitleTiming.ts` | 클라이언트 자막 도착 timing 캡처 (performance.now()) |
| Modify | `apps/desktop/src/console/sessionApi.ts` 또는 자막 수신부 | timing hook 호출 |
| Create | `tests/scripts/test_baseline_collect.py` | 로그 파서 단위 테스트 |
| Create | `tests/scripts/test_baseline_compare.py` | 비교 리포트 단위 테스트 |
| Create | `docs/baselines/2026-MM-DD-<scenario>.json` | 실측 산출물 (수동 실행) |

### Phase 1 — macOS Native Helper

Swift 패키지 (`apps/native_helper_mac/`):
| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `apps/native_helper_mac/Package.swift` | Swift package 매니페스트 |
| Create | `apps/native_helper_mac/Sources/YesonMacAudioHelper/main.swift` | 진입점 — 시그널·인자·라이프사이클 |
| Create | `apps/native_helper_mac/Sources/YesonMacAudioHelper/AudioCapture.swift` | `AudioCapture` 프로토콜 + 공통 타입 |
| Create | `apps/native_helper_mac/Sources/YesonMacAudioHelper/IPC.swift` | stdout binary writer + stderr JSON lines |
| Create | `apps/native_helper_mac/Sources/YesonMacAudioHelper/PCMConverter.swift` | 임의 sample rate float32 → 16 kHz mono s16le |
| Create | `apps/native_helper_mac/Sources/YesonMacAudioHelper/ScreenCaptureKitProvider.swift` | 실제 캡처 구현체 |
| Create | `apps/native_helper_mac/Tests/YesonMacAudioHelperTests/IPCTests.swift` | IPC 단위 테스트 |
| Create | `apps/native_helper_mac/Tests/YesonMacAudioHelperTests/PCMConverterTests.swift` | 변환 단위 테스트 |
| Create | `apps/native_helper_mac/scripts/build-release.sh` | release 바이너리 빌드 |

Python sidecar (`apps/client_sidecar/audio/`):
| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `apps/client_sidecar/audio/source.py` | `AudioSource` 추상 베이스 |
| Create | `apps/client_sidecar/audio/sources/__init__.py` | 패키지 마커 |
| Create | `apps/client_sidecar/audio/sources/sounddevice_source.py` | 기존 capture/device 로직을 `AudioSource` 구현체로 wrapping |
| Create | `apps/client_sidecar/audio/sources/native_pipe_source.py` | helper 프로세스 spawn + stdout 읽기 + stderr 이벤트 |
| Create | `apps/client_sidecar/audio/sources/factory.py` | `YESON_AUDIO_PROVIDER` env 기반 선택 + auto fallback |
| Modify | `apps/client_sidecar/config/audio.py` (anchor `AUDIO_END` 직전) | `NATIVE_HELPER_BIN_PATH`, `YESON_AUDIO_PROVIDER` 상수 |
| Modify | `apps/client_sidecar/main.py:38-55` (anchor `MAIN_AUDIO_MAIN_*` 내부) | factory 호출로 source 선택 |
| Create | `apps/client_sidecar/tests/test_native_pipe_source.py` | mock subprocess 기반 단위 테스트 |
| Create | `apps/client_sidecar/tests/test_source_factory.py` | env 기반 선택·fallback 테스트 |

---

## Tasks

### Task 1: Baseline scenario protocol document

**Files:**
- Create: `docs/baselines/scenarios.md`

이 task는 순수 문서 산출물이라 TDD 스텝이 없다.

- [ ] **Step 1: Write the scenario protocol**

Create `docs/baselines/scenarios.md`:

```markdown
# Baseline Measurement Scenarios

> 측정 환경: 현재 BlackHole(macOS) 또는 Voicemeeter(Windows) 기반. Phase 1 완료 후 동일 시나리오를 native 캡처로 재측정해 비교한다.

## 공통 사전 조건
- 서버는 `docker compose ... up -d server` 로 정상 가동
- `.env` 확인: `GEMINI_RESPONSE_MODALITY=AUDIO`, `GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview`
- 클라이언트(dashboard)는 dev 모드(`pnpm --filter @yeson-meet/desktop tauri:dev`)
- 측정 시작 전 `/tmp/yeson-server-<scenario>.log` 로그 캡처 시작:
  `docker compose ... logs -f --since=0s server > /tmp/yeson-server-<scenario>.log 2>&1 &`
- 측정 종료 후 log capture kill, `scripts/baseline_collect.py --log <path> --scenario <name> --out docs/baselines/<date>-<scenario>.json`

## 시나리오 1: Zoom 1:1 EN→KO (5분)
- 화자 1인 (영어 native, 정상 음량)
- 정해진 영어 대본 약 60문장 낭독 (대본은 `docs/baselines/script_en.md` — 추후 작성)
- BlackHole / Multi-Output Device 셋업 확인
- 측정 종료 후 docker logs를 `2026-MM-DD-zoom-1on1.log`로 저장

## 시나리오 2: Teams 3+ mixed (10분)
- 3인 이상 회의, 한국어와 영어 혼재
- 자연 대화 흐름 — 스크립트 없음
- 화자 전환·짧은 silence 자연스럽게 발생해야 함
- 출력 로그: `2026-MM-DD-teams-3plus.log`

## 시나리오 3: YouTube TED EN (10분)
- 정해진 TED talk URL 1개 (예: "How great leaders inspire action" 첫 10분)
- 모노 출력, 음량 60% 고정
- 출력 로그: `2026-MM-DD-youtube-ted.log`

## 시나리오 4: Silent room (5분)
- 발화 없음, 캡처는 활성
- 자막이 false positive로 안 뜨는지 확인
- 출력 로그: `2026-MM-DD-silent.log`

## 측정 후 수집할 지표 (`baseline_collect.py`가 자동 추출)
- `subtitle_first_token_ms` — 첫 발화 → 첫 자막 토큰
- `subtitle_full_p50_ms`, `subtitle_full_p95_ms` — 발화 종료 → final 자막
- `chunks_per_sec_sustained` — 평균
- `audio_queue_drop_count` — 누적
- `gemini_segments_per_minute` — TPM 추정용

## 출력 파일 명명 규칙
- 로그: `/tmp/yeson-server-<scenario>.log` (수집 중) → `docs/baselines/raw/<date>-<scenario>.log` (보존)
- 지표 JSON: `docs/baselines/<date>-<scenario>.json`
```

- [ ] **Step 2: Commit**

Run:
```bash
git add docs/baselines/scenarios.md
git commit -m "docs(baselines): scenario protocol for phase 0 measurement"
```

---

### Task 2: Baseline log aggregation script — test fixture

**Files:**
- Create: `tests/scripts/__init__.py`
- Create: `tests/scripts/test_baseline_collect.py`
- Create: `tests/scripts/fixtures/baseline_sample.log`

- [ ] **Step 1: Create test fixture**

Create `tests/scripts/fixtures/baseline_sample.log`:

```
server-1  | INFO:apps.server.ai.gemini_live:Gemini Live first input transcription gemini_connect_to_first_input_ms=9400 gemini_first_input_chars=215 gemini_segment=1 session_id=abc-1
server-1  | INFO:apps.server.ai.gemini_live:Gemini Live first subtitle yielded gemini_connect_to_first_subtitle_ms=9988 gemini_segment=1 is_final=False seq=1 session_id=abc-1
server-1  | INFO:apps.server.ws.sidecar:AI utterance published ai_publish_latency_ms=0 is_final=False seq=1 session_id=abc-1
server-1  | INFO:apps.server.ws.sidecar:AI utterance published ai_publish_latency_ms=0 is_final=True seq=1 session_id=abc-1
server-1  | INFO:apps.server.ai.gemini_live:Gemini Live connect starting gemini_model=gemini-3.1-flash-live-preview gemini_segment=2 session_id=abc-1
server-1  | INFO:apps.server.ai.gemini_live:Gemini Live first subtitle yielded gemini_connect_to_first_subtitle_ms=8010 gemini_segment=2 is_final=False seq=1 session_id=abc-1
server-1  | INFO:apps.server.ws.sidecar:AI utterance published ai_publish_latency_ms=0 is_final=True seq=2 session_id=abc-1
server-1  | INFO:apps.server.ai.gemini_live:Gemini Live connect starting gemini_model=gemini-3.1-flash-live-preview gemini_segment=3 session_id=abc-1
server-1  | INFO:apps.server.ai.gemini_live:Gemini Live first subtitle yielded gemini_connect_to_first_subtitle_ms=7500 gemini_segment=3 is_final=False seq=1 session_id=abc-1
server-1  | INFO:apps.server.ws.sidecar:AI utterance published ai_publish_latency_ms=0 is_final=True seq=3 session_id=abc-1
server-1  | WARNING:apps.server.ai.live_session:Audio queue lossy drop — provider can't keep up dropped_chunks_total=50
server-1  | WARNING:apps.server.ai.live_session:Audio queue lossy drop — provider can't keep up dropped_chunks_total=100
```

- [ ] **Step 2: Write the failing test**

Create `tests/scripts/__init__.py` (empty file).

Create `tests/scripts/test_baseline_collect.py`:

```python
"""Tests for scripts/baseline_collect.py."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "baseline_sample.log"
SCRIPT = Path(__file__).parents[2] / "scripts" / "baseline_collect.py"


def test_collect_extracts_first_subtitle_latency(tmp_path):
    out = tmp_path / "metrics.json"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--log", str(FIXTURE),
            "--scenario", "fixture",
            "--out", str(out),
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.read_text())
    assert data["scenario"] == "fixture"
    assert data["subtitle_first_token_ms"] == 9988
    # Two further segments → p50 ≈ 8010, p95 ≈ ~7500-or-8010 depending on n
    assert "subtitle_full_p50_ms" in data
    assert "subtitle_full_p95_ms" in data
    assert data["audio_queue_drop_count"] == 100
    assert data["gemini_segment_count"] == 3
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/scripts/test_baseline_collect.py -v`
Expected: FAIL with "No such file or directory: scripts/baseline_collect.py" (or similar).

---

### Task 3: Baseline log aggregation script — implementation

**Files:**
- Create: `scripts/baseline_collect.py`

- [ ] **Step 1: Write minimal implementation**

Create `scripts/baseline_collect.py`:

```python
#!/usr/bin/env python3
"""Aggregate Gemini Live latency/throughput metrics from server logs.

Parses lines emitted by ``apps.server.ai.gemini_live`` and ``apps.server.ws.sidecar``
(both INFO/WARNING). Outputs a single JSON file per scenario suitable for
direct comparison with the post-Phase-1 native run.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path


# === ANCHOR: BASELINE_COLLECT_PATTERNS_START ===
FIRST_SUBTITLE_RE = re.compile(
    r"Gemini Live first subtitle yielded.*?gemini_connect_to_first_subtitle_ms=(\d+).*?gemini_segment=(\d+)"
)
DROP_RE = re.compile(r"dropped_chunks_total=(\d+)")
CONNECT_RE = re.compile(r"Gemini Live connect starting.*?gemini_segment=(\d+)")
# === ANCHOR: BASELINE_COLLECT_PATTERNS_END ===


# === ANCHOR: BASELINE_COLLECT_PARSE_START ===
def parse_log(path: Path) -> dict[str, object]:
    first_subtitle_ms_list: list[int] = []
    drop_total = 0
    segment_count = 0
    for line in path.read_text(errors="replace").splitlines():
        m = FIRST_SUBTITLE_RE.search(line)
        if m:
            first_subtitle_ms_list.append(int(m.group(1)))
            continue
        m = DROP_RE.search(line)
        if m:
            drop_total = max(drop_total, int(m.group(1)))
            continue
        m = CONNECT_RE.search(line)
        if m:
            segment_count = max(segment_count, int(m.group(1)))
            continue
    if not first_subtitle_ms_list:
        raise SystemExit("no 'Gemini Live first subtitle yielded' lines found")
    return {
        "subtitle_first_token_ms": first_subtitle_ms_list[0],
        "subtitle_full_p50_ms": int(statistics.median(first_subtitle_ms_list)),
        "subtitle_full_p95_ms": int(
            statistics.quantiles(first_subtitle_ms_list, n=20)[-1]
            if len(first_subtitle_ms_list) >= 2
            else first_subtitle_ms_list[0]
        ),
        "audio_queue_drop_count": drop_total,
        "gemini_segment_count": segment_count,
    }
# === ANCHOR: BASELINE_COLLECT_PARSE_END ===


# === ANCHOR: BASELINE_COLLECT_MAIN_START ===
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    metrics = parse_log(args.log)
    metrics["scenario"] = args.scenario
    metrics["source_log"] = str(args.log)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics, indent=2))
# === ANCHOR: BASELINE_COLLECT_MAIN_END ===


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make executable and run test**

Run:
```bash
chmod +x scripts/baseline_collect.py
pytest tests/scripts/test_baseline_collect.py -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

Run:
```bash
git add scripts/baseline_collect.py tests/scripts/__init__.py tests/scripts/test_baseline_collect.py tests/scripts/fixtures/baseline_sample.log
git commit -m "feat(scripts): baseline_collect aggregates gemini live metrics from server logs"
```

---

### Task 4: Baseline comparison script — test

**Files:**
- Create: `tests/scripts/test_baseline_compare.py`

- [ ] **Step 1: Write the failing test**

Create `tests/scripts/test_baseline_compare.py`:

```python
"""Tests for scripts/baseline_compare.py."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "baseline_compare.py"


def test_compare_produces_markdown_with_deltas(tmp_path):
    baseline = tmp_path / "baseline.json"
    native = tmp_path / "native.json"
    baseline.write_text(json.dumps({
        "scenario": "zoom-1on1",
        "subtitle_first_token_ms": 10000,
        "subtitle_full_p50_ms": 9500,
        "subtitle_full_p95_ms": 11200,
        "audio_queue_drop_count": 50,
        "gemini_segment_count": 30,
    }))
    native.write_text(json.dumps({
        "scenario": "zoom-1on1",
        "subtitle_first_token_ms": 7800,
        "subtitle_full_p50_ms": 7200,
        "subtitle_full_p95_ms": 8500,
        "audio_queue_drop_count": 5,
        "gemini_segment_count": 30,
    }))
    out = tmp_path / "report.md"
    subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--baseline", str(baseline),
            "--native", str(native),
            "--out", str(out),
        ],
        check=True,
    )
    body = out.read_text()
    assert "zoom-1on1" in body
    assert "subtitle_first_token_ms" in body
    assert "-22.0%" in body or "-22%" in body  # 10000 → 7800
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/scripts/test_baseline_compare.py -v`
Expected: FAIL with "No such file or directory".

---

### Task 5: Baseline comparison script — implementation

**Files:**
- Create: `scripts/baseline_compare.py`

- [ ] **Step 1: Write implementation**

Create `scripts/baseline_compare.py`:

```python
#!/usr/bin/env python3
"""Compare baseline (BlackHole/Voicemeeter) vs native capture metrics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

METRIC_KEYS = [
    "subtitle_first_token_ms",
    "subtitle_full_p50_ms",
    "subtitle_full_p95_ms",
    "audio_queue_drop_count",
    "gemini_segment_count",
]


# === ANCHOR: BASELINE_COMPARE_MAIN_START ===
def render(baseline: dict, native: dict) -> str:
    lines = [
        f"# Baseline vs Native — scenario `{baseline.get('scenario','?')}`",
        "",
        "| metric | baseline | native | delta |",
        "|---|---:|---:|---:|",
    ]
    for k in METRIC_KEYS:
        b = baseline.get(k)
        n = native.get(k)
        if isinstance(b, (int, float)) and isinstance(n, (int, float)) and b:
            delta_pct = (n - b) / b * 100.0
            delta = f"{delta_pct:+.1f}%"
        else:
            delta = "—"
        lines.append(f"| {k} | {b} | {n} | {delta} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", required=True, type=Path)
    p.add_argument("--native", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    baseline = json.loads(args.baseline.read_text())
    native = json.loads(args.native.read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(baseline, native))


if __name__ == "__main__":
    main()
# === ANCHOR: BASELINE_COMPARE_MAIN_END ===
```

- [ ] **Step 2: Make executable and run test**

Run:
```bash
chmod +x scripts/baseline_compare.py
pytest tests/scripts/test_baseline_compare.py -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

Run:
```bash
git add scripts/baseline_compare.py tests/scripts/test_baseline_compare.py
git commit -m "feat(scripts): baseline_compare renders markdown delta report"
```

---

### Task 6: Client subtitle timing instrumentation

**Files:**
- Create: `apps/desktop/src/timing/subtitleTiming.ts`
- Modify: 자막 수신부 — 실제 경로는 `apps/desktop/src/console/sessionApi.ts` 또는 viewer subscribe 부분에서 timing hook 호출. (구체 위치는 코드 인스펙션 후 결정. 모르면 `grep -rn "TranslatedUtterance\\|seq" apps/desktop/src` 로 찾기.)

이 task는 브라우저에서만 의미 있는 timing이라 단위 테스트는 시도하지만 핵심은 manual smoke. 단위 테스트는 timing 누적/JSON export만 검증.

- [ ] **Step 1: Write the failing test**

Create `apps/desktop/src/timing/subtitleTiming.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { SubtitleTimingRecorder } from "./subtitleTiming";

describe("SubtitleTimingRecorder", () => {
  let recorder: SubtitleTimingRecorder;
  beforeEach(() => {
    recorder = new SubtitleTimingRecorder(() => 1000);
  });

  it("records arrival timestamp per seq", () => {
    recorder.markArrival({ seq: 1, isFinal: false });
    recorder.markArrival({ seq: 1, isFinal: true });
    const events = recorder.export();
    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({ seq: 1, isFinal: false, t_ms: 1000 });
    expect(events[1]).toMatchObject({ seq: 1, isFinal: true });
  });

  it("exports as downloadable JSON string", () => {
    recorder.markArrival({ seq: 1, isFinal: true });
    const json = recorder.toJSON();
    const parsed = JSON.parse(json);
    expect(parsed.events).toHaveLength(1);
    expect(parsed.recorded_at).toBeDefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --filter @yeson-meet/desktop test`
(or `pnpm -C apps/desktop test`. If vitest isn't configured, install it first: `pnpm -C apps/desktop add -D vitest`)

Expected: FAIL with module not found.

- [ ] **Step 3: Write implementation**

Create `apps/desktop/src/timing/subtitleTiming.ts`:

```typescript
// === ANCHOR: SUBTITLE_TIMING_START ===
export interface SubtitleArrival {
  seq: number;
  isFinal: boolean;
}

interface TimingEvent extends SubtitleArrival {
  t_ms: number;
}

export class SubtitleTimingRecorder {
  private events: TimingEvent[] = [];
  private readonly now: () => number;

  constructor(now: () => number = () => performance.now()) {
    this.now = now;
  }

  markArrival(arrival: SubtitleArrival): void {
    this.events.push({ ...arrival, t_ms: this.now() });
  }

  export(): TimingEvent[] {
    return [...this.events];
  }

  toJSON(): string {
    return JSON.stringify(
      { recorded_at: new Date().toISOString(), events: this.events },
      null,
      2
    );
  }

  reset(): void {
    this.events = [];
  }
}
// === ANCHOR: SUBTITLE_TIMING_END ===
```

- [ ] **Step 4: Run tests**

Run: `pnpm -C apps/desktop test`
Expected: PASS.

- [ ] **Step 5: Wire into subtitle reception**

Find subtitle arrival site (typical location: `apps/desktop/src/console/sessionApi.ts` or wherever WS messages from `/ws/viewer` are dispatched). 

Locate the code that processes each `TranslatedUtterance`-shaped message. Add an import and a recorder instance:

```typescript
// at the top of the module
import { SubtitleTimingRecorder } from "../timing/subtitleTiming";

// near the WS subscription init
const timingRecorder = new SubtitleTimingRecorder();

// where each utterance arrives (inside the WS message handler)
timingRecorder.markArrival({ seq: utterance.seq, isFinal: utterance.is_final });
```

Expose the recorder via Settings/Diagnostics tab so user can download the JSON during a baseline run. Minimal version: add a global debug helper:

```typescript
// somewhere accessible
declare global {
  interface Window {
    __yesonTimingExport?: () => string;
  }
}
window.__yesonTimingExport = () => timingRecorder.toJSON();
```

User opens DevTools and runs `copy(window.__yesonTimingExport())` after a scenario to grab the JSON. Pasted into `docs/baselines/2026-MM-DD-<scenario>-client.json` manually for this PoC. (A proper UI button is out of scope for this plan.)

- [ ] **Step 6: Commit**

Run:
```bash
git add apps/desktop/src/timing/ apps/desktop/src/console/sessionApi.ts
git commit -m "feat(desktop): subtitle arrival timing recorder + global export hook"
```

---

### Task 7: Run baseline measurements (operational)

**Files:**
- Create: `docs/baselines/2026-MM-DD-<scenario>.json` (4개 — 실행 후)
- Create: `docs/baselines/raw/2026-MM-DD-<scenario>.log` (4개)

이건 코드 변경 task가 아니라 **실제 측정 수행**이다. TDD 스텝 대신 절차 체크리스트.

- [ ] **Step 1: 시나리오 1 — Zoom 1:1 EN→KO 실측**

```bash
# Terminal 1: 로그 캡처 시작
docker compose --env-file /Users/usabatch/coding/yeson_dev/yeson_meet/.env \
  -f /Users/usabatch/coding/yeson_dev/yeson_meet/deploy/docker-compose.yml \
  logs -f --since=0s server > /tmp/yeson-server-zoom-1on1.log 2>&1 &

# Terminal 2: 대시보드 dev 시작 (Phase 0 baseline 은 반드시 sounddevice 고정)
# 이유: 기본 `auto` 는 helper binary 가 있으면 native 를 선택하므로 baseline 이 오염될 수 있음.
export YESON_AUDIO_PROVIDER=sounddevice
export YESON_NATIVE_HELPER_BIN=/nonexistent/yeson-mac-audio-helper
pnpm --filter @yeson-meet/desktop tauri:dev

# Tauri/sidecar 콘솔에서 반드시 확인:
# sidecar audio mode → source=SoundDeviceSource url=...

# Zoom 1:1 회의 5분 진행 (영어 화자 1명, 정해진 대본)
# DevTools에서 copy(window.__yesonTimingExport()) → 저장

# 종료 후
kill %1  # log capture stop
mv /tmp/yeson-server-zoom-1on1.log docs/baselines/raw/2026-05-27-zoom-1on1.log

# 지표 집계 — 반드시 --schema v1 로 출력해야 Step 7 판정 표의 nested 키(ai.* / capture.*)와 정합.
# env 인자는 schema v1 필수: 누락 시 스크립트가 에러로 알려줌.
python scripts/baseline_collect.py \
  --log docs/baselines/raw/2026-05-27-zoom-1on1.log \
  --scenario zoom-1on1 \
  --out docs/baselines/2026-05-27-zoom-1on1.json \
  --schema v1 \
  --provider sounddevice --os macOS --os-version "$(sw_vers -productVersion)" \
  --audio-route "BlackHole 2ch + Multi-Output" --permission-state not_applicable \
  --server-commit "$(git rev-parse --short HEAD)" --client-commit "$(git rev-parse --short HEAD)" \
  --gemini-model gemini-3.1-flash-live-preview --gemini-modality AUDIO \
  --duration-seconds 300
```

> 시나리오 2~4도 동일하게 `--schema v1` + env 인자를 붙인다. silent 는 추가로 `--allow-empty`,
> `--duration-seconds` 는 실제 측정 길이(Teams/YouTube=600, silent=300)로 맞춘다.
> `--speech-onset-unix-ms` 는 현재 collector 가 latency delta 로 변환하지 못하므로 넣지 않는다.
> PRD 기준 user-perceived latency 가 필요하면 `baseline_collect.py` 에 true delta 계산을 먼저 추가한다.

- [ ] **Step 2: 시나리오 2 — Teams 3+ mixed 실측**

위 절차 동일, `<scenario>` = `teams-3plus`, 10분.

- [ ] **Step 3: 시나리오 3 — YouTube TED EN 실측**

위 절차 동일, `<scenario>` = `youtube-ted`, 10분.

- [ ] **Step 4: 시나리오 4 — Silent room 실측**

위 절차 동일, `<scenario>` = `silent`, 5분.
주의: 자막 0개여도 OK. silent 수집은 **Step 5처럼 `--allow-empty`를 반드시 붙인다.**

- [ ] **Step 5: Silent scenario uses `--allow-empty`**

`--allow-empty` support is already folded into Task 2-3. For the silent run, call the collector with `--allow-empty` so a no-subtitle log emits `null` AI fields instead of failing.

Argparser support and this test are already present; keep them green:

```python
def test_collect_empty_scenario_with_allow_empty(tmp_path):
    empty_log = tmp_path / "empty.log"
    empty_log.write_text("")
    out = tmp_path / "metrics.json"
    subprocess.run(
        [sys.executable, str(SCRIPT),
         "--log", str(empty_log), "--scenario", "silent",
         "--out", str(out), "--allow-empty"],
        check=True,
    )
    data = json.loads(out.read_text())
    assert data["empty_scenario"] is True
```

Re-run: `pytest tests/scripts/test_baseline_collect.py -v`. Then re-run silent collection with `--allow-empty`.

- [ ] **Step 6: Commit baselines**

```bash
git add docs/baselines/2026-05-27-*.json docs/baselines/raw/*.log scripts/baseline_collect.py tests/scripts/test_baseline_collect.py
git commit -m "data(baselines): phase 0 measurements for 4 scenarios (BlackHole)"
```

- [ ] **Step 7: Exit criteria — native smoke/re-measurement 진행 결정**

4 시나리오 측정이 끝나면 아래 표로 다음 단계를 박는다. 각 줄은 "이 수치면 → 이 행동" 단일 결정 규칙.

| 신호 (4 시나리오 종합) | 의미 | 다음 행동 |
|---|---|---|
| `ai.gemini_connect_to_first_subtitle_ms_p50` > 5000 (Zoom·Teams·YouTube 중 2개 이상) | 자막 지연의 주범이 Gemini 쪽 — 캡처 레이어 교체로 안 풀림 | Native 채택 보류. server-side(prompt / segment 분리 / partial 전략) 먼저 손봄 |
| `capture.audio_queue_drop_count` / (`duration_seconds`/60) > 10 (분당 환산) | 캡처 안정성이 진짜 문제 | Task 24/25 진행 — native 캡처 검증 가치 큼 |
| silent 시나리오에서 자막 1줄 이상 생성 | false positive (Gemini 가 무음에 환각) | Native 채택 보류. VAD/silence gate + prompt 재설계 먼저 |
| 위 3개 모두 정상 | 기술적으로 sounddevice 가 충분 | Task 24/25 진행 — 사용자 설치 UX 목적 (BlackHole/Voicemeeter 없애기) |

> ⚠️ **현 도구 한계**: `baseline_collect.py`는 `capture.chunks_per_sec_sustained` 와
> `ai.gemini_segments_per_minute` 를 `null`로만 출력한다(로그에서 자동 산출 안 됨).
> 따라서 위 표는 **자동 수집되는 `audio_queue_drop_count`** 만으로 캡처 안정성을 판정한다.
> chunks/sec 가 꼭 필요하면 별도 파서(서버의 chunk-cadence 로그 라인 기반)를 먼저 추가할 것.

판정 결과는 commit 메시지 또는 `docs/baselines/comparison-<date>.md` 상단에 한 줄 명시: `"Native GO: <이유>"` 또는 `"Native HOLD: <원인 + 선행 작업>"`. 결과가 명백하지 않은 경계 케이스(예: drop 8개, P50 4800ms)면 시나리오 1개씩만 rerun 후 재판정.

---

### Task 8: Swift package skeleton

**Files:**
- Create: `apps/native_helper_mac/Package.swift`
- Create: `apps/native_helper_mac/Sources/YesonMacAudioHelper/main.swift` (placeholder)
- Create: `apps/native_helper_mac/Tests/YesonMacAudioHelperTests/SmokeTests.swift`
- Create: `apps/native_helper_mac/.gitignore`

- [ ] **Step 1: Write the failing test**

Create `apps/native_helper_mac/Tests/YesonMacAudioHelperTests/SmokeTests.swift`:

```swift
import XCTest
@testable import YesonMacAudioHelper

final class SmokeTests: XCTestCase {
    func testHelperVersion() {
        XCTAssertEqual(yesonHelperVersion, "0.1.0")
    }
}
```

- [ ] **Step 2: Create Package.swift**

Create `apps/native_helper_mac/Package.swift`:

```swift
// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "YesonMacAudioHelper",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "YesonMacAudioHelper",
            path: "Sources/YesonMacAudioHelper"
        ),
        .testTarget(
            name: "YesonMacAudioHelperTests",
            dependencies: ["YesonMacAudioHelper"],
            path: "Tests/YesonMacAudioHelperTests"
        ),
    ]
)
```

- [ ] **Step 3: Create placeholder main.swift**

Create `apps/native_helper_mac/Sources/YesonMacAudioHelper/main.swift`:

```swift
import Foundation

public let yesonHelperVersion = "0.1.0"

// Entry point — fleshed out in later tasks.
FileHandle.standardError.write("yeson-mac-audio-helper \(yesonHelperVersion)\n".data(using: .utf8) ?? Data())
```

- [ ] **Step 4: Create .gitignore**

Create `apps/native_helper_mac/.gitignore`:

```
.build/
.swiftpm/
*.xcodeproj
DerivedData/
```

- [ ] **Step 5: Run test**

Run: `cd apps/native_helper_mac && swift test`
Expected: PASS — single smoke test passes.

- [ ] **Step 6: Commit**

```bash
git add apps/native_helper_mac/
git commit -m "feat(native-helper-mac): swift package skeleton with smoke test"
```

---

### Task 9: PCMConverter — test

**Files:**
- Create: `apps/native_helper_mac/Tests/YesonMacAudioHelperTests/PCMConverterTests.swift`

- [ ] **Step 1: Write the failing test**

Create the file:

```swift
import XCTest
@testable import YesonMacAudioHelper

final class PCMConverterTests: XCTestCase {
    func testConvertsFloat48kStereoTo16kMonoS16LE() throws {
        let conv = PCMConverter(sourceSampleRate: 48000, sourceChannels: 2)
        // 1 second of stereo 48k at 0.5 amplitude
        let samples = 48000 * 2
        var input = [Float](repeating: 0.5, count: samples)
        let output = try conv.process(floats: &input, frameCount: 48000)
        // Expected: 1 second at 16k mono s16le = 16000 samples * 2 bytes
        XCTAssertEqual(output.count, 16000 * 2)
        // Each s16le sample for amplitude 0.5 ≈ 16384
        let first = Int16(littleEndian: output.withUnsafeBytes { $0.load(fromByteOffset: 0, as: Int16.self) })
        XCTAssertEqual(first, 16384, accuracy: 100)
    }

    func testEmitsZeroBytesForEmptyInput() throws {
        let conv = PCMConverter(sourceSampleRate: 48000, sourceChannels: 2)
        var input: [Float] = []
        let output = try conv.process(floats: &input, frameCount: 0)
        XCTAssertEqual(output.count, 0)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/native_helper_mac && swift test`
Expected: FAIL — `PCMConverter` undefined.

---

### Task 10: PCMConverter — implementation

**Files:**
- Create: `apps/native_helper_mac/Sources/YesonMacAudioHelper/PCMConverter.swift`

> ⚠️ **본문 코드는 stale**: 아래 예제는 `converter.convert`를 1회만 호출한다. 실제 커밋된
> 구현(`Sources/YesonMacAudioHelperKit/PCMConverter.swift`)은 sample-rate 변환기의 filter tail 을
> 비우기 위해 `while` 루프로 drain 한다(`status == .endOfStream` 또는 `samples == 0`까지). 코드 기준은 커밋본.

- [ ] **Step 1: Write implementation**

Create the file:

```swift
import Foundation
import AVFoundation

// === ANCHOR: PCM_CONVERTER_START ===
/// Resamples float32 interleaved input to 16 kHz mono Int16 little-endian.
final class PCMConverter {
    private let converter: AVAudioConverter
    private let sourceFormat: AVAudioFormat
    private let targetFormat: AVAudioFormat
    private let sourceChannels: UInt32

    init(sourceSampleRate: Double, sourceChannels: UInt32) {
        self.sourceChannels = sourceChannels
        guard let src = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: sourceSampleRate,
            channels: sourceChannels,
            interleaved: true
        ) else {
            fatalError("source format invalid: sr=\(sourceSampleRate) ch=\(sourceChannels)")
        }
        guard let dst = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: 16000,
            channels: 1,
            interleaved: true
        ) else {
            fatalError("target format invalid")
        }
        self.sourceFormat = src
        self.targetFormat = dst
        guard let conv = AVAudioConverter(from: src, to: dst) else {
            fatalError("AVAudioConverter init failed")
        }
        self.converter = conv
    }

    /// Process N source frames (interleaved float32) and return s16le LE bytes at 16 kHz mono.
    /// `floats` must contain at least `frameCount * sourceChannels` samples.
    func process(floats: inout [Float], frameCount: AVAudioFrameCount) throws -> Data {
        if frameCount == 0 { return Data() }
        guard let inBuf = AVAudioPCMBuffer(pcmFormat: sourceFormat, frameCapacity: frameCount) else {
            throw NSError(domain: "PCMConverter", code: 1)
        }
        inBuf.frameLength = frameCount
        let ch = Int(sourceChannels)
        floats.withUnsafeBufferPointer { ptr in
            inBuf.floatChannelData![0].update(from: ptr.baseAddress!, count: Int(frameCount) * ch)
        }
        // Target frames ~ frameCount * 16000 / sourceFormat.sampleRate
        let dstCapacity = AVAudioFrameCount(Double(frameCount) * 16000.0 / sourceFormat.sampleRate + 16)
        guard let outBuf = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: dstCapacity) else {
            throw NSError(domain: "PCMConverter", code: 2)
        }
        var consumed = false
        var error: NSError?
        let status = converter.convert(to: outBuf, error: &error) { _, inputStatus in
            if consumed {
                inputStatus.pointee = .noDataNow
                return nil
            }
            consumed = true
            inputStatus.pointee = .haveData
            return inBuf
        }
        if let error = error { throw error }
        if status == .error {
            throw NSError(domain: "PCMConverter", code: 3)
        }
        let samples = Int(outBuf.frameLength)
        let bytes = samples * 2
        return Data(bytes: outBuf.int16ChannelData![0], count: bytes)
    }
}
// === ANCHOR: PCM_CONVERTER_END ===
```

- [ ] **Step 2: Run tests**

Run: `cd apps/native_helper_mac && swift test`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add apps/native_helper_mac/Sources/YesonMacAudioHelper/PCMConverter.swift apps/native_helper_mac/Tests/YesonMacAudioHelperTests/PCMConverterTests.swift
git commit -m "feat(native-helper-mac): PCMConverter to 16khz mono s16le via AVAudioConverter"
```

---

### Task 11: IPC framer — test

**Files:**
- Create: `apps/native_helper_mac/Tests/YesonMacAudioHelperTests/IPCTests.swift`

- [ ] **Step 1: Write the failing test**

```swift
import XCTest
@testable import YesonMacAudioHelper

final class IPCTests: XCTestCase {
    func testWritePCMChunkToBuffer() {
        let buf = DataBufferSink()
        let ipc = IPC(dataSink: buf, controlSink: DataBufferSink())
        let chunk = Data([0x00, 0x01, 0x02, 0x03])
        ipc.emitChunk(chunk)
        XCTAssertEqual(buf.collected, chunk)
    }

    func testWriteControlEventAsJSONLine() {
        let ctrl = DataBufferSink()
        let ipc = IPC(dataSink: DataBufferSink(), controlSink: ctrl)
        ipc.emitEvent(name: "permission_denied", payload: ["code": "E_PERM"])
        let line = String(data: ctrl.collected, encoding: .utf8) ?? ""
        XCTAssertTrue(line.hasSuffix("\n"))
        let json = try? JSONSerialization.jsonObject(with: ctrl.collected.dropLast()) as? [String: Any]
        XCTAssertEqual(json?["event"] as? String, "permission_denied")
        XCTAssertEqual((json?["payload"] as? [String: String])?["code"], "E_PERM")
    }
}

// Test double for FileHandle-shaped output
final class DataBufferSink: ByteSink {
    var collected = Data()
    func write(_ data: Data) { collected.append(data) }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/native_helper_mac && swift test`
Expected: FAIL — `IPC`, `ByteSink` undefined.

---

### Task 12: IPC framer — implementation

**Files:**
- Create: `apps/native_helper_mac/Sources/YesonMacAudioHelper/IPC.swift`

- [ ] **Step 1: Write implementation**

```swift
import Foundation

// === ANCHOR: IPC_START ===
protocol ByteSink {
    func write(_ data: Data)
}

struct FileHandleSink: ByteSink {
    let handle: FileHandle
    func write(_ data: Data) { handle.write(data) }
}

/// stdout = PCM binary stream. stderr = JSON line events.
final class IPC {
    private let dataSink: ByteSink
    private let controlSink: ByteSink

    init(dataSink: ByteSink, controlSink: ByteSink) {
        self.dataSink = dataSink
        self.controlSink = controlSink
    }

    static func standard() -> IPC {
        IPC(
            dataSink: FileHandleSink(handle: FileHandle.standardOutput),
            controlSink: FileHandleSink(handle: FileHandle.standardError)
        )
    }

    func emitChunk(_ data: Data) {
        dataSink.write(data)
    }

    func emitEvent(name: String, payload: [String: Any] = [:]) {
        let obj: [String: Any] = ["event": name, "payload": payload]
        guard let data = try? JSONSerialization.data(withJSONObject: obj) else { return }
        var line = data
        line.append(0x0A) // \n
        controlSink.write(line)
    }
}
// === ANCHOR: IPC_END ===
```

- [ ] **Step 2: Run tests**

Run: `cd apps/native_helper_mac && swift test`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add apps/native_helper_mac/Sources/YesonMacAudioHelper/IPC.swift apps/native_helper_mac/Tests/YesonMacAudioHelperTests/IPCTests.swift
git commit -m "feat(native-helper-mac): IPC framer (stdout pcm + stderr json line events)"
```

---

### Task 13: AudioCapture protocol

**Files:**
- Create: `apps/native_helper_mac/Sources/YesonMacAudioHelper/AudioCapture.swift`

순수 정의 파일이라 별도 테스트 없음. 다음 task의 `ScreenCaptureKitProvider`가 이 프로토콜에 conform 한다.

- [ ] **Step 1: Write the protocol file**

```swift
import Foundation

// === ANCHOR: AUDIO_CAPTURE_START ===
enum PermissionStatus: String, Encodable {
    case granted, denied, notDetermined, restricted, notApplicable
}

enum CaptureTarget {
    case systemDefault
    case device(String)
    case app(String)  // bundle_id
}

enum CaptureError: Error {
    case permissionDenied
    case unsupportedOS
    case deviceNotFound
    case internalError(String)
}

protocol AudioCapture {
    var permissionStatus: PermissionStatus { get }
    func requestPermission() async -> PermissionStatus
    func setTarget(_ target: CaptureTarget) throws
    func listTargets() -> [CaptureTarget]

    /// Start capture. Subsequent PCM frames flow to `frameHandler`.
    /// frameHandler is invoked with already-converted 16 kHz mono Int16 LE Data of size 640 bytes (20 ms).
    func start(frameHandler: @escaping (Data) -> Void) throws
    func stop()
    func dispose()
}

/// Constants — every implementation must honor.
enum AudioContract {
    static let sampleRate: Int = 16_000
    static let channels: Int = 1
    static let frameMs: Int = 20
    static let frameBytes: Int = 640 // 320 samples * 2 bytes
}
// === ANCHOR: AUDIO_CAPTURE_END ===
```

- [ ] **Step 2: Build verifies syntax**

Run: `cd apps/native_helper_mac && swift build`
Expected: BUILD SUCCEEDED.

- [ ] **Step 3: Commit**

```bash
git add apps/native_helper_mac/Sources/YesonMacAudioHelper/AudioCapture.swift
git commit -m "feat(native-helper-mac): AudioCapture protocol + AudioContract constants"
```

---

### Task 14: ScreenCaptureKitProvider implementation

**Files:**
- Create: `apps/native_helper_mac/Sources/YesonMacAudioHelper/ScreenCaptureKitProvider.swift`

이 구현은 ScreenCaptureKit 실제 사용으로 unit test가 어렵다. **smoke 절차로 검증**하고 unit test는 protocol conformance만 본다.

- [ ] **Step 1: Write the failing conformance test**

Append to `apps/native_helper_mac/Tests/YesonMacAudioHelperTests/SmokeTests.swift`:

```swift
    func testScreenCaptureKitProviderConformsToProtocol() {
        let provider: AudioCapture = ScreenCaptureKitProvider()
        XCTAssertEqual(provider.listTargets().count >= 1, true) // at minimum system default
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/native_helper_mac && swift test`
Expected: FAIL — `ScreenCaptureKitProvider` undefined.

- [ ] **Step 3: Write implementation**

Create `apps/native_helper_mac/Sources/YesonMacAudioHelper/ScreenCaptureKitProvider.swift`:

```swift
import Foundation
import ScreenCaptureKit
import AVFoundation

// === ANCHOR: SCK_PROVIDER_START ===
final class ScreenCaptureKitProvider: NSObject, AudioCapture, SCStreamOutput {
    private var stream: SCStream?
    private var converter: PCMConverter?
    private var frameHandler: ((Data) -> Void)?
    private var target: CaptureTarget = .systemDefault
    private var pending = Data()

    var permissionStatus: PermissionStatus {
        // ScreenCaptureKit needs Screen Recording permission for system audio.
        // No direct API to query — we check via CGPreflightScreenCaptureAccess.
        // Importing CoreGraphics for the check.
        return CGPreflightScreenCaptureAccess() ? .granted : .notDetermined
    }

    func requestPermission() async -> PermissionStatus {
        let ok = CGRequestScreenCaptureAccess()
        return ok ? .granted : .denied
    }

    func setTarget(_ target: CaptureTarget) throws {
        self.target = target
    }

    func listTargets() -> [CaptureTarget] {
        return [.systemDefault]  // PoC scope: system default only
    }

    func start(frameHandler: @escaping (Data) -> Void) throws {
        self.frameHandler = frameHandler
        Task {
            do {
                let shareable = try await SCShareableContent.excludingDesktopWindows(
                    false, onScreenWindowsOnly: false
                )
                guard let display = shareable.displays.first else {
                    throw CaptureError.deviceNotFound
                }
                let filter = SCContentFilter(display: display, excludingWindows: [])
                let config = SCStreamConfiguration()
                config.capturesAudio = true
                config.excludesCurrentProcessAudio = true
                config.sampleRate = 48000
                config.channelCount = 2
                // Suppress video — only audio matters
                config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
                config.width = 2
                config.height = 2

                self.converter = PCMConverter(sourceSampleRate: 48000, sourceChannels: 2)

                let stream = SCStream(filter: filter, configuration: config, delegate: nil)
                try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: .global())
                try await stream.startCapture()
                self.stream = stream
            } catch {
                FileHandle.standardError.write(
                    "screencapturekit_start_error: \(error)\n".data(using: .utf8) ?? Data()
                )
            }
        }
    }

    func stop() {
        Task {
            try? await stream?.stopCapture()
            stream = nil
        }
    }

    func dispose() {
        stop()
        converter = nil
        frameHandler = nil
    }

    // MARK: SCStreamOutput
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio,
              CMSampleBufferIsValid(sampleBuffer),
              let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }

        var totalLength = 0
        var dataPointer: UnsafeMutablePointer<Int8>?
        CMBlockBufferGetDataPointer(blockBuffer, atOffset: 0, lengthAtOffsetOut: nil,
                                    totalLengthOut: &totalLength, dataPointerOut: &dataPointer)
        guard let ptr = dataPointer else { return }
        let frameCount = AVAudioFrameCount(totalLength / 8) // 2 ch * 4 bytes float32
        var floats = [Float](repeating: 0, count: totalLength / 4)
        memcpy(&floats, ptr, totalLength)

        do {
            let converted = try converter?.process(floats: &floats, frameCount: frameCount) ?? Data()
            pending.append(converted)
            // Emit in 640-byte frames
            while pending.count >= AudioContract.frameBytes {
                let chunk = pending.prefix(AudioContract.frameBytes)
                pending.removeFirst(AudioContract.frameBytes)
                frameHandler?(Data(chunk))
            }
        } catch {
            FileHandle.standardError.write(
                "conversion_error: \(error)\n".data(using: .utf8) ?? Data()
            )
        }
    }
}
// === ANCHOR: SCK_PROVIDER_END ===
```

- [ ] **Step 4: Run tests**

Run: `cd apps/native_helper_mac && swift test`
Expected: PASS (protocol conformance check; actual capture not exercised in unit test).

- [ ] **Step 5: Commit**

```bash
git add apps/native_helper_mac/Sources/YesonMacAudioHelper/ScreenCaptureKitProvider.swift apps/native_helper_mac/Tests/YesonMacAudioHelperTests/SmokeTests.swift
git commit -m "feat(native-helper-mac): ScreenCaptureKitProvider — system audio via SCStream"
```

---

### Task 15: Helper main entrypoint wiring

**Files:**
- Modify: `apps/native_helper_mac/Sources/YesonMacAudioHelper/main.swift`

- [ ] **Step 1: Replace placeholder main**

Overwrite `apps/native_helper_mac/Sources/YesonMacAudioHelper/main.swift`:

```swift
import Foundation

public let yesonHelperVersion = "0.1.0"

// === ANCHOR: HELPER_MAIN_START ===
let ipc = IPC.standard()
ipc.emitEvent(name: "starting", payload: ["version": yesonHelperVersion])

let provider: AudioCapture = ScreenCaptureKitProvider()

switch provider.permissionStatus {
case .granted:
    break
case .notDetermined, .denied, .restricted, .notApplicable:
    ipc.emitEvent(name: "permission_required", payload: ["status": "\(provider.permissionStatus.rawValue)"])
    let status = await provider.requestPermission()
    ipc.emitEvent(name: "permission_status", payload: ["status": status.rawValue])
    if status != .granted {
        ipc.emitEvent(name: "fatal", payload: ["reason": "permission_denied"])
        exit(3)
    }
}

do {
    try provider.start { chunk in
        ipc.emitChunk(chunk)
    }
    ipc.emitEvent(name: "started", payload: [:])
} catch {
    ipc.emitEvent(name: "fatal", payload: ["reason": "start_failed", "detail": "\(error)"])
    exit(4)
}

// Graceful shutdown on SIGINT/SIGTERM
signal(SIGINT, SIG_IGN)
signal(SIGTERM, SIG_IGN)
let sigSrc = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigSrc.setEventHandler {
    ipc.emitEvent(name: "stopping", payload: [:])
    provider.dispose()
    exit(0)
}
sigSrc.resume()

dispatchMain()
// === ANCHOR: HELPER_MAIN_END ===
```

Wait — `main.swift` cannot use `await` at top level without `@main` and async context. We need to refactor. Replace with:

```swift
import Foundation

public let yesonHelperVersion = "0.1.0"

// === ANCHOR: HELPER_MAIN_START ===
@main
struct YesonMacAudioHelperApp {
    static func main() async {
        let ipc = IPC.standard()
        ipc.emitEvent(name: "starting", payload: ["version": yesonHelperVersion])

        let provider: AudioCapture = ScreenCaptureKitProvider()

        if provider.permissionStatus != .granted {
            ipc.emitEvent(name: "permission_required", payload: ["status": provider.permissionStatus.rawValue])
            let status = await provider.requestPermission()
            ipc.emitEvent(name: "permission_status", payload: ["status": status.rawValue])
            if status != .granted {
                ipc.emitEvent(name: "fatal", payload: ["reason": "permission_denied"])
                exit(3)
            }
        }

        do {
            try provider.start { chunk in
                ipc.emitChunk(chunk)
            }
            ipc.emitEvent(name: "started", payload: [:])
        } catch {
            ipc.emitEvent(name: "fatal", payload: ["reason": "start_failed", "detail": "\(error)"])
            exit(4)
        }

        signal(SIGINT, SIG_IGN)
        signal(SIGTERM, SIG_IGN)
        let sigSrc = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        sigSrc.setEventHandler {
            ipc.emitEvent(name: "stopping", payload: [:])
            provider.dispose()
            exit(0)
        }
        sigSrc.resume()

        // Block forever
        try? await Task.sleep(nanoseconds: UInt64.max)
    }
}
// === ANCHOR: HELPER_MAIN_END ===
```

- [ ] **Step 2: Build**

Run: `cd apps/native_helper_mac && swift build`
Expected: BUILD SUCCEEDED.

- [ ] **Step 3: Smoke run (manual)**

Run:
```bash
cd apps/native_helper_mac
.build/debug/YesonMacAudioHelper > /tmp/helper.pcm 2> /tmp/helper.err &
sleep 5
kill %1
wc -c /tmp/helper.pcm
cat /tmp/helper.err
```

Expected:
- `helper.err`에 `{"event":"starting",...}` ⏵ `{"event":"started",...}` 라인 보임 (권한 이미 부여돼 있으면)
- `helper.pcm`에 약 5초 × 32 KB/sec ≈ 160 KB의 데이터 (audio가 실제 흐를 때)
- 또는 권한 모달이 뜸 (첫 실행 시) — 허용 후 재시도

만약 권한 모달이 안 뜨고 곧장 `permission_denied`가 나오면, macOS Settings → Privacy & Security → Screen Recording 에서 Terminal(또는 실행한 호스트 앱)을 허용 후 재시도.

- [ ] **Step 4: Commit**

```bash
git add apps/native_helper_mac/Sources/YesonMacAudioHelper/main.swift
git commit -m "feat(native-helper-mac): main entry wires provider + IPC + signal handling"
```

---

### Task 16: Release build script

**Files:**
- Create: `apps/native_helper_mac/scripts/build-release.sh`

- [ ] **Step 1: Write script**

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Building YesonMacAudioHelper (release)…"
swift build -c release

OUT="$(swift build -c release --show-bin-path)/YesonMacAudioHelper"
if [[ ! -f "$OUT" ]]; then
    echo "ERROR: expected binary at $OUT" >&2
    exit 1
fi

# Copy to a stable location consumed by Python sidecar
DEST="../../target/native-helper-mac/yeson-mac-audio-helper"
mkdir -p "$(dirname "$DEST")"
cp "$OUT" "$DEST"
echo "→ $DEST"
echo "size: $(stat -f%z "$DEST") bytes"
```

- [ ] **Step 2: Make executable + smoke run**

```bash
chmod +x apps/native_helper_mac/scripts/build-release.sh
apps/native_helper_mac/scripts/build-release.sh
```
Expected: `target/native-helper-mac/yeson-mac-audio-helper` 존재.

- [ ] **Step 3: Add target/ to gitignore (if not already)**

Run:
```bash
grep -q "^target/" .gitignore || echo "target/" >> .gitignore
```

- [ ] **Step 4: Commit**

```bash
git add apps/native_helper_mac/scripts/build-release.sh .gitignore
git commit -m "build(native-helper-mac): release build script copies binary to target/"
```

---

### Task 17: Python `AudioSource` ABC

**Files:**
- Create: `apps/client_sidecar/audio/source.py`
- Create: `apps/client_sidecar/audio/sources/__init__.py`
- Create: `apps/client_sidecar/tests/test_source_abc.py`

- [ ] **Step 1: Write the failing test**

Create `apps/client_sidecar/tests/test_source_abc.py`:

```python
"""Tests for AudioSource abstract base class."""
from __future__ import annotations

import asyncio

import pytest

from apps.client_sidecar.audio.source import AudioSource


def test_abstract_cannot_instantiate():
    with pytest.raises(TypeError):
        AudioSource()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_concrete_impl_yields_bytes():
    class Fake(AudioSource):
        async def chunks(self):
            yield b"\x00" * 640
            yield b"\x01" * 640

        async def close(self):
            pass

    src = Fake()
    out = []
    async for c in src.chunks():
        out.append(c)
        if len(out) >= 2:
            break
    await src.close()
    assert len(out) == 2
    assert all(len(c) == 640 for c in out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/client_sidecar/tests/test_source_abc.py -v`
Expected: FAIL — `apps.client_sidecar.audio.source` module not found.

- [ ] **Step 3: Write implementation**

Create `apps/client_sidecar/audio/source.py`:

```python
# === ANCHOR: AUDIO_SOURCE_START ===
"""AudioSource abstract base — yields 640-byte 16kHz mono PCM s16le chunks."""
from __future__ import annotations

import abc
from collections.abc import AsyncIterator


class AudioSource(abc.ABC):
    """Async iterator producing 640-byte 16kHz mono s16le PCM chunks.

    Implementations: SoundDeviceSource (BlackHole/Voicemeeter), NativePipeSource
    (ScreenCaptureKit/WASAPI native helper subprocess).
    """

    @abc.abstractmethod
    def chunks(self) -> AsyncIterator[bytes]:
        """Yield 640-byte PCM chunks. Iterator lifetime tied to source lifecycle."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Release underlying resources (stop stream, kill subprocess, etc.)."""
# === ANCHOR: AUDIO_SOURCE_END ===
```

Create `apps/client_sidecar/audio/sources/__init__.py` (empty).

- [ ] **Step 4: Run test**

Run: `pytest apps/client_sidecar/tests/test_source_abc.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/client_sidecar/audio/source.py apps/client_sidecar/audio/sources/__init__.py apps/client_sidecar/tests/test_source_abc.py
git commit -m "feat(sidecar): AudioSource ABC for pluggable capture providers"
```

---

### Task 18: SoundDeviceSource wrapper

**Files:**
- Create: `apps/client_sidecar/audio/sources/sounddevice_source.py`
- Create: `apps/client_sidecar/tests/test_sounddevice_source.py`

- [ ] **Step 1: Write the failing test**

Create `apps/client_sidecar/tests/test_sounddevice_source.py`:

```python
"""SoundDeviceSource wraps the existing find_input_device + capture_chunks pipeline."""
from __future__ import annotations

import pytest

from apps.client_sidecar.audio.source import AudioSource


@pytest.mark.asyncio
async def test_sounddevice_source_is_audio_source():
    from apps.client_sidecar.audio.sources.sounddevice_source import SoundDeviceSource
    src = SoundDeviceSource()  # constructor with defaults; chunks() not exercised here
    assert isinstance(src, AudioSource)
    await src.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/client_sidecar/tests/test_sounddevice_source.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write implementation**

Create `apps/client_sidecar/audio/sources/sounddevice_source.py`:

```python
# === ANCHOR: SOUNDDEVICE_SOURCE_START ===
"""AudioSource implementation wrapping the existing sounddevice pipeline."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from apps.client_sidecar.audio.source import AudioSource
from apps.client_sidecar.audio.capture import capture_chunks
from apps.client_sidecar.audio.device import find_input_device
from apps.client_sidecar.config.audio import DEVICE_INDEX, DEVICE_NAME_REGEX

logger = logging.getLogger(__name__)


class SoundDeviceSource(AudioSource):
    """BlackHole(macOS) / Voicemeeter(Windows) compatible source.

    Wraps the legacy capture path. Behavior unchanged from pre-Phase-1.
    """

    def __init__(self, name_regex: str | None = None, index: int | None = None):
        self._name_regex = name_regex if name_regex is not None else DEVICE_NAME_REGEX
        self._index = index if index is not None else DEVICE_INDEX

    async def chunks(self) -> AsyncIterator[bytes]:
        device = find_input_device(self._name_regex, self._index)
        async for chunk in capture_chunks(device):
            yield chunk

    async def close(self) -> None:
        # capture_chunks owns its own stream lifecycle (finally block).
        return None
# === ANCHOR: SOUNDDEVICE_SOURCE_END ===
```

- [ ] **Step 4: Run test**

Run: `pytest apps/client_sidecar/tests/test_sounddevice_source.py -v`
Expected: PASS.

- [ ] **Step 5: Verify existing tests still pass**

Run: `pytest apps/client_sidecar/tests/ -v`
Expected: all PASS (we have NOT touched the old code paths).

- [ ] **Step 6: Commit**

```bash
git add apps/client_sidecar/audio/sources/sounddevice_source.py apps/client_sidecar/tests/test_sounddevice_source.py
git commit -m "feat(sidecar): SoundDeviceSource wraps existing capture pipeline as AudioSource"
```

---

### Task 19: NativePipeSource — test

**Files:**
- Create: `apps/client_sidecar/tests/test_native_pipe_source.py`

- [ ] **Step 1: Write the failing test**

```python
"""NativePipeSource reads PCM chunks from helper subprocess stdout."""
from __future__ import annotations

import asyncio
import io
import subprocess
from unittest.mock import MagicMock

import pytest

from apps.client_sidecar.audio.source import AudioSource


@pytest.mark.asyncio
async def test_native_pipe_source_yields_chunks_from_stdout(monkeypatch):
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource

    # Mock subprocess that emits two 640-byte chunks then closes stdout
    fake_proc = MagicMock()
    payload = (b"\x00" * 640) + (b"\x11" * 640)
    fake_proc.stdout = asyncio.StreamReader()
    fake_proc.stdout.feed_data(payload)
    fake_proc.stdout.feed_eof()
    fake_proc.stderr = asyncio.StreamReader()
    fake_proc.stderr.feed_eof()
    fake_proc.terminate = MagicMock()
    fake_proc.wait = MagicMock(return_value=asyncio.sleep(0))

    async def fake_create(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    src = NativePipeSource(bin_path="/tmp/fake-helper")
    assert isinstance(src, AudioSource)
    chunks = []
    async for c in src.chunks():
        chunks.append(c)
        if len(chunks) >= 2:
            break
    await src.close()
    assert len(chunks) == 2
    assert chunks[0] == b"\x00" * 640
    assert chunks[1] == b"\x11" * 640


@pytest.mark.asyncio
async def test_native_pipe_source_raises_if_bin_missing():
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource

    src = NativePipeSource(bin_path="/nonexistent/path/yeson-helper")
    with pytest.raises(FileNotFoundError):
        async for _ in src.chunks():
            break
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/client_sidecar/tests/test_native_pipe_source.py -v`
Expected: FAIL — module not found.

---

### Task 20: NativePipeSource — implementation

**Files:**
- Create: `apps/client_sidecar/audio/sources/native_pipe_source.py`

- [ ] **Step 1: Write implementation**

```python
# === ANCHOR: NATIVE_PIPE_SOURCE_START ===
"""AudioSource implementation spawning native ScreenCaptureKit helper process.

Reads 640-byte PCM chunks from helper stdout. Helper stderr JSON-line events
are logged at INFO/WARNING.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator

from apps.client_sidecar.audio.source import AudioSource
from apps.client_sidecar.config.audio import CHUNK_BYTES

logger = logging.getLogger(__name__)


class NativePipeSource(AudioSource):
    """Spawn native helper, stream stdout PCM, parse stderr JSON events."""

    def __init__(self, bin_path: str):
        self._bin_path = bin_path
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    async def _spawn(self) -> asyncio.subprocess.Process:
        if not os.path.isfile(self._bin_path):
            raise FileNotFoundError(f"native helper not found: {self._bin_path}")
        proc = await asyncio.create_subprocess_exec(
            self._bin_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info("native helper spawned pid=%s bin=%s", proc.pid, self._bin_path)
        self._proc = proc
        self._stderr_task = asyncio.create_task(self._drain_stderr(proc.stderr))
        return proc

    async def _drain_stderr(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            try:
                evt = json.loads(line)
                logger.info("native helper event: %s", evt)
                if evt.get("event") == "fatal":
                    logger.error("native helper fatal: %s", evt.get("payload"))
            except json.JSONDecodeError:
                logger.warning("native helper non-json stderr: %r", line[:200])

    async def chunks(self) -> AsyncIterator[bytes]:
        proc = await self._spawn()
        stdout = proc.stdout
        if stdout is None:
            raise RuntimeError("native helper has no stdout")
        try:
            while True:
                chunk = await stdout.readexactly(CHUNK_BYTES)
                yield chunk
        except asyncio.IncompleteReadError as e:
            if e.partial:
                logger.warning("native helper closed mid-chunk (%d bytes)", len(e.partial))
            else:
                logger.info("native helper stdout closed cleanly")

    async def close(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._proc.kill()
            self._proc = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
# === ANCHOR: NATIVE_PIPE_SOURCE_END ===
```

- [ ] **Step 2: Run test**

Run: `pytest apps/client_sidecar/tests/test_native_pipe_source.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add apps/client_sidecar/audio/sources/native_pipe_source.py apps/client_sidecar/tests/test_native_pipe_source.py
git commit -m "feat(sidecar): NativePipeSource spawns helper subprocess + reads PCM stream"
```

---

### Task 21: Provider config + factory — test

**Files:**
- Modify: `apps/client_sidecar/config/audio.py` (add anchor block right before `AUDIO_END`)
- Create: `apps/client_sidecar/audio/sources/factory.py`
- Create: `apps/client_sidecar/tests/test_source_factory.py`

- [ ] **Step 1: Add config constants**

Edit `apps/client_sidecar/config/audio.py`. Find the line `# === ANCHOR: AUDIO_END ===` and INSERT the following BEFORE it:

```python
# === ANCHOR: AUDIO_PROVIDER_START ===
# Provider selection. `auto` = try native, fallback to sounddevice on error.
YESON_AUDIO_PROVIDER: str = os.environ.get("YESON_AUDIO_PROVIDER", "auto").lower()
# Where to find the native helper binary (release: bundled by Tauri; dev: target/)
NATIVE_HELPER_BIN_PATH: str = os.environ.get(
    "YESON_NATIVE_HELPER_BIN",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        "target", "native-helper-mac", "yeson-mac-audio-helper",
    ),
)
# === ANCHOR: AUDIO_PROVIDER_END ===
```

- [ ] **Step 2: Write the failing factory test**

Create `apps/client_sidecar/tests/test_source_factory.py`:

```python
"""Provider factory selects source by YESON_AUDIO_PROVIDER env, with auto fallback."""
from __future__ import annotations

import pytest

from apps.client_sidecar.audio.source import AudioSource


def test_factory_returns_sounddevice_for_explicit_env(monkeypatch):
    from apps.client_sidecar.audio.sources.factory import make_source
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "sounddevice")
    src = make_source()
    from apps.client_sidecar.audio.sources.sounddevice_source import SoundDeviceSource
    assert isinstance(src, SoundDeviceSource)


def test_factory_returns_native_when_explicit_and_bin_exists(monkeypatch, tmp_path):
    fake_bin = tmp_path / "yeson-mac-audio-helper"
    fake_bin.write_bytes(b"\x00")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "native")
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", str(fake_bin))
    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
    assert isinstance(src, NativePipeSource)


def test_factory_auto_falls_back_to_sounddevice_if_native_bin_missing(monkeypatch):
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "auto")
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", "/nonexistent/yeson-helper")
    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.sounddevice_source import SoundDeviceSource
    assert isinstance(src, SoundDeviceSource)


def test_factory_native_explicit_with_missing_bin_raises(monkeypatch):
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "native")
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", "/nonexistent/yeson-helper")
    from apps.client_sidecar.audio.sources.factory import make_source
    with pytest.raises(FileNotFoundError):
        make_source()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest apps/client_sidecar/tests/test_source_factory.py -v`
Expected: FAIL — module not found.

---

### Task 22: Provider factory — implementation

**Files:**
- Create: `apps/client_sidecar/audio/sources/factory.py`

- [ ] **Step 1: Write implementation**

```python
# === ANCHOR: SOURCE_FACTORY_START ===
"""Select AudioSource implementation based on YESON_AUDIO_PROVIDER env.

native — explicit; raises FileNotFoundError if helper binary missing
sounddevice — explicit; uses BlackHole/Voicemeeter compatibility path
auto — try native; on any failure fall back to sounddevice
"""
from __future__ import annotations

import logging
import os

from apps.client_sidecar.audio.source import AudioSource
from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
from apps.client_sidecar.audio.sources.sounddevice_source import SoundDeviceSource
from apps.client_sidecar.config.audio import NATIVE_HELPER_BIN_PATH, YESON_AUDIO_PROVIDER

logger = logging.getLogger(__name__)


def make_source() -> AudioSource:
    provider = os.environ.get("YESON_AUDIO_PROVIDER", YESON_AUDIO_PROVIDER).lower()
    if provider == "sounddevice":
        logger.info("audio provider: sounddevice (explicit)")
        return SoundDeviceSource()
    bin_path = os.environ.get("YESON_NATIVE_HELPER_BIN", NATIVE_HELPER_BIN_PATH)
    if provider == "native":
        if not os.path.isfile(bin_path):
            raise FileNotFoundError(
                f"YESON_AUDIO_PROVIDER=native but helper binary missing: {bin_path}"
            )
        logger.info("audio provider: native (explicit, bin=%s)", bin_path)
        return NativePipeSource(bin_path=bin_path)
    # auto
    if os.path.isfile(bin_path):
        logger.info("audio provider: native (auto, bin=%s)", bin_path)
        return NativePipeSource(bin_path=bin_path)
    logger.warning(
        "audio provider: sounddevice (auto fallback — native helper missing at %s)",
        bin_path,
    )
    return SoundDeviceSource()
# === ANCHOR: SOURCE_FACTORY_END ===
```

- [ ] **Step 2: Run test**

Run: `pytest apps/client_sidecar/tests/test_source_factory.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add apps/client_sidecar/audio/sources/factory.py apps/client_sidecar/tests/test_source_factory.py apps/client_sidecar/config/audio.py
git commit -m "feat(sidecar): provider factory selects source by env, auto fallback to sounddevice"
```

---

### Task 23: Wire factory into main.py

**Files:**
- Modify: `apps/client_sidecar/main.py` (inside `MAIN_AUDIO_MAIN_*` anchor only)

- [ ] **Step 1: Replace `audio_main` body**

Edit `apps/client_sidecar/main.py`. Replace the entire content between `# === ANCHOR: MAIN_AUDIO_MAIN_START ===` and `# === ANCHOR: MAIN_AUDIO_MAIN_END ===` with:

```python
# === ANCHOR: MAIN_AUDIO_MAIN_START ===
async def audio_main() -> None:
    """S2 audio mode — provider factory selects source, then stream to server WS."""
    from apps.client_sidecar.audio.sources.factory import make_source
    from apps.client_sidecar.transport.audio_ws import stream_audio

    api_key = _required_env("YESON_DEVICE_API_KEY")
    session_id = UUID(_required_env("YESON_SESSION_ID"))

    source = make_source()
    url = f"{SERVER_WS_BASE}{SERVER_WS_PATH}?key={api_key}&session={session_id}"
    print(f"sidecar audio mode → source={type(source).__name__} url={url}")

    try:
        await stream_audio(url, source.chunks())
    finally:
        await source.close()
# === ANCHOR: MAIN_AUDIO_MAIN_END ===
```

- [ ] **Step 2: Verify existing smoke test still passes**

Run: `pytest apps/client_sidecar/tests/test_audio_main_smoke.py -v`
Expected: PASS. (If the smoke test asserted specific imports from the old path, fix it to match the new flow.)

- [ ] **Step 3: Run full sidecar test suite**

Run: `pytest apps/client_sidecar/tests/ -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/client_sidecar/main.py
git commit -m "refactor(sidecar): main delegates audio source selection to factory"
```

---

### Task 24: End-to-end smoke (manual)

**Files:** none (operational)

이 task는 전체 흐름을 native 모드로 한 번 돌려본다.

- [ ] **Step 1: Build native helper**

```bash
apps/native_helper_mac/scripts/build-release.sh
ls -la target/native-helper-mac/yeson-mac-audio-helper
```
Expected: 파일 존재.

- [ ] **Step 2: Permission check**

```bash
target/native-helper-mac/yeson-mac-audio-helper > /tmp/h.pcm 2> /tmp/h.err &
sleep 3
kill %1
cat /tmp/h.err
```
Expected: `{"event":"started",...}` 보임. 권한 모달 떠서 거부했다면 macOS Settings → Privacy & Security → Screen Recording 에서 Terminal 허용 후 재시도.

**⚠️ 오디오 포맷 검증 (필수)**: stderr 에 1회성 `audio_format_check: ... nonInterleaved=<bool> hasDataBuffer=<bool>` 라인이 찍혀야 한다.
probe 는 `CMSampleBufferGetDataBuffer` guard **이전**에 있어 planar/buffer-nil 케이스도 항상 로깅된다.
- `nonInterleaved=false` 면 현 interleaved 가정이 맞다 — 그대로 진행.
- `nonInterleaved=true` 면 SCStream 이 **planar** 오디오를 주는 것이므로 `ScreenCaptureKitProvider`의
  `memcpy` 경로(`totalLength/8` 프레임, 단일 버퍼를 interleaved 로 취급)가 **오디오를 깨뜨린다**.
  이 경우 `CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer` + per-channel deinterleave 로 교체해야 한다.
- `hasDataBuffer=false` 면 contiguous block buffer 가 아니라 현 memcpy 경로가 데이터를 못 읽는다 →
  `nonInterleaved` 값과 함께 위 AudioBufferList 경로로 교체 판단.
- `audio_format_check` 라인이 아예 없으면 콜백 자체가 안 불린 것(권한 미부여/스트림 시작 실패) — 그쪽을 먼저 본다.
- `/tmp/h.pcm` 가 약 5초 × 32 KB/s ≈ 160 KB 면 데이터가 흐르는 것. 0 byte 면 캡처/권한 재확인.

- [ ] **Step 3: Run dashboard with native provider**

```bash
# 같은 shell 에서 Tauri dev 를 시작해야 sidecar 자식 프로세스가 env 를 상속한다.
export YESON_AUDIO_PROVIDER=native

# Dashboard dev 모드 시작
pnpm --filter @yeson-meet/desktop tauri:dev
```

회의 또는 영상 재생 → 대시보드 자막 패널에 자막이 떠야 함.

- [ ] **Step 4: Verify provider selection in logs**

Tauri/sidecar 콘솔에서:
```
sidecar audio mode → source=NativePipeSource url=...
native helper event: {'event': 'started', ...}
```
이 보여야 함.

서버 로그(`docker compose logs -f server`)에서는 평소와 동일하게:
```
Sidecar first audio chunk received audio_first_chunk_bytes=640
Gemini Live connect starting ...
Gemini Live first subtitle yielded ...
```

자막이 안 뜨면:
- helper stderr 에 fatal 이벤트 없는지 확인
- sidecar 콘솔에서 NativePipeSource 가 chunks 를 받는지 확인
- 권한 다시 확인

- [ ] **Step 5: 종료 후 shell env 원복**

```bash
unset YESON_AUDIO_PROVIDER
```

---

### Task 25: Native scenario re-measurements

**Files:**
- Create: `docs/baselines/2026-MM-DD-<scenario>-native.json` (4개)
- Create: `docs/baselines/raw/2026-MM-DD-<scenario>-native.log` (4개)

Task 7과 동일 절차이되, Tauri dev 를 시작하는 같은 shell 에서 `YESON_AUDIO_PROVIDER=native` 를 export 한 뒤 진행.

- [ ] **Step 1: Set provider to native**

```bash
export YESON_AUDIO_PROVIDER=native
```

- [ ] **Step 2–5: 시나리오 1~4 native 재측정**

각 시나리오별로 Task 7과 같은 절차 수행. 출력 명명은 `<scenario>-native`:

```bash
# 예: zoom-1on1
docker compose --env-file /Users/usabatch/coding/yeson_dev/yeson_meet/.env \
  -f /Users/usabatch/coding/yeson_dev/yeson_meet/deploy/docker-compose.yml \
  logs -f --since=0s server > /tmp/yeson-server-zoom-1on1-native.log 2>&1 &

pnpm --filter @yeson-meet/desktop tauri:dev
# 5분 진행 후
kill %1
mv /tmp/yeson-server-zoom-1on1-native.log docs/baselines/raw/2026-05-27-zoom-1on1-native.log

python scripts/baseline_collect.py \
  --log docs/baselines/raw/2026-05-27-zoom-1on1-native.log \
  --scenario zoom-1on1-native \
  --out docs/baselines/2026-05-27-zoom-1on1-native.json \
  --schema v1 \
  --provider native --os macOS --os-version "$(sw_vers -productVersion)" \
  --audio-route "ScreenCaptureKit system default" --permission-state granted \
  --server-commit "$(git rev-parse --short HEAD)" --client-commit "$(git rev-parse --short HEAD)" \
  --gemini-model gemini-3.1-flash-live-preview --gemini-modality AUDIO \
  --duration-seconds 300
```

4개 시나리오 모두 동일 절차 반복. Teams/YouTube 는 `--duration-seconds 600`, silent 는 `--allow-empty --duration-seconds 300`.

- [ ] **Step 6: shell env 원복**

```bash
unset YESON_AUDIO_PROVIDER
```

- [ ] **Step 7: Commit native baselines**

```bash
git add docs/baselines/2026-05-27-*-native.json docs/baselines/raw/*-native.log
git commit -m "data(baselines): native measurements for 4 scenarios (ScreenCaptureKit)"
```

---

### Task 26: Generate comparison reports

**Files:**
- Create: `docs/baselines/comparison-2026-MM-DD.md`

- [ ] **Step 1: Run comparison script for each scenario**

```bash
for s in zoom-1on1 teams-3plus youtube-ted silent; do
    python scripts/baseline_compare.py \
        --baseline "docs/baselines/2026-05-27-${s}.json" \
        --native "docs/baselines/2026-05-27-${s}-native.json" \
        --out "docs/baselines/comparison-${s}.md"
done
```

- [ ] **Step 2: Aggregate into single comparison document**

Create `docs/baselines/comparison-2026-05-27.md` by concatenating the per-scenario reports:

```bash
cat > docs/baselines/comparison-2026-05-27.md << 'EOF'
# Phase 0 ↔ Phase 1 Native — 2026-05-27

| 시나리오 | baseline → native 핵심 변화 |
|---------|---------------------------|
EOF

for s in zoom-1on1 teams-3plus youtube-ted silent; do
    {
        echo ""
        echo "---"
        echo ""
        cat "docs/baselines/comparison-${s}.md"
    } >> docs/baselines/comparison-2026-05-27.md
done
```

- [ ] **Step 3: Add interpretation section (manual)**

Append to `docs/baselines/comparison-2026-05-27.md`:

```markdown

## 해석 및 결론

(아래는 측정 후 본인이 직접 채우는 항목)

- **subtitle_first_token_ms 변화**: …
- **drop count 변화**: …
- **segment_count 변화**: …
- **사용자 체감 (UI 응답성)**: …
- **권장 다음 단계**:
  - [ ] Phase 1 native를 dev 기본 provider로 채택
  - [ ] BlackHole 경로는 compatibility fallback로만 유지
  - [ ] Phase 2 Windows WASAPI 시작 (`docs/INTEGRATION_DESIGN.md` §3, §4 따름)
```

- [ ] **Step 4: Commit**

```bash
git add docs/baselines/comparison-*.md
git commit -m "data(baselines): phase 0 vs phase 1 native comparison report"
```

---

## Self-Review

After writing this plan, fresh-eye check:

**1. Spec coverage:**

| Spec section | Implemented by tasks |
|--------------|---------------------|
| §3 Audio Native helper 통합 | Tasks 8–23 |
| §4 `AudioCapture` 인터페이스 | Tasks 13, 14 (Swift); §4의 OS-agnostic 관점은 Phase 2에서 Rust 구현으로 확정 |
| §5 Phase 0 baseline 측정 | Tasks 1–7 |
| §5 Phase 1 검증 (재측정 + 비교) | Tasks 25, 26 |
| Provider 선택 + fallback (§3.2) | Tasks 21, 22, 23 |
| `YESON_AUDIO_PROVIDER` env | Tasks 21, 22 |

§6 (자동 노트 생성), §7 (진행 순서의 노트 v1·Launcher MVP)는 본 plan 범위 밖 — 별도 plan으로 작성.

**2. Placeholder scan:** 없음. 모든 step에 실제 코드/명령 포함. "manual" task(Task 7, 24, 25)는 명령 시퀀스 명시.

**3. Type consistency:**
- `AudioSource.chunks()` → `AsyncIterator[bytes]` 모든 구현체에서 동일
- `NATIVE_HELPER_BIN_PATH` 상수명 일관 (config + factory)
- `YESON_AUDIO_PROVIDER` 값 `native|sounddevice|auto` 일관 (test + factory + main)
- Swift `AudioContract.frameBytes=640` ↔ Python `CHUNK_BYTES=640` 일치

**4. Ambiguity:**
- Task 6 (client timing)에서 자막 수신부의 정확한 파일 경로는 코드 인스펙션 필요라고 명시. 엔지니어가 grep으로 찾도록 안내함.
- Task 15에서 `await` 사용 위해 `@main` 구조체로 변경한 이유 코드 내 주석으로 남김.

이상 자체 review 통과.

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-05-27-native-audio.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 각 task마다 fresh subagent 디스패치, 사이사이 리뷰, 빠른 iteration. plan이 26개 task로 길어 subagent 격리가 컨텍스트 부담 분산에 유리.

**2. Inline Execution** — 현재 세션에서 executing-plans로 batch 실행, checkpoint마다 본인 리뷰.

어느 방식으로 진행할까요?
