# dBFS 캡처 레벨미터 (Phase 3 slice 3) — 설계

- **상태**: 승인됨 (2026-06-15)
- **브랜치**: `topyeson`
- **선행**: Phase 3 slice 1(캡처 상태칩) + slice 2(RMS 무음 감지), 둘 다 Windows E2E PASS
- **관련 설계**: `docs/superpowers/specs/2026-06-10-capture-status-ux-design.md`, `docs/superpowers/specs/2026-06-10-rms-silence-detection-design.md`

## 1. 문제 / 목적

자막 헤더의 캡처 상태칩(🟢정상/🟡무음/🔴전송끊김/⚪연결중)은 **상태**만 알려준다.
운영자는 "소리가 실제로 잘 잡히고 있는지, 너무 작거나 큰지"를 연속적으로 확인할 수단이 없다.

dBFS는 이미 사이드카의 `audio_ws.stream_audio`가 청크마다 `pcm16_dbfs()`로 계산하고
있다(slice 2에서 도입). 이 값을 데스크톱으로 흘려 칩 옆에 **1초 주기 세그먼트 레벨미터**로
표시한다. 칩(상태) + 미터(음량)는 상호 보완하며, slice 1/2 파이프라인을 그대로 확장한다.

## 2. 핵심 결정 (브레인스토밍 합의)

| 결정 | 선택 | 이유 |
|---|---|---|
| 반응성 | **거친 1초** | 운영자용으로 충분, VU 출렁임 불필요. 기존 워치독 1초 폴 주기에 편승. |
| 데이터 경로 | **전용 `capture-level` Tauri 이벤트** | 1Hz 연속 스트림을 app-log(1000줄 링)에 넣으면 ~17분에 진단 로그를 덮음 + 1Hz 구독자 재알림. 전용 채널로 분리해 진단 로그·저장 스냅샷을 깨끗하게 유지. 레벨=텔레메트리라 개념적으로도 맞음. |
| 미터 모양 | **세그먼트 막대(6칸)** | 직관적, 좌표공간 작아 칩 옆에 깔끔. 상위 칸은 과대 시 노랑/빨강. |
| 표시 규칙 | **`active`/`silent`에서만** | 칩이 connecting/transport_down을 이미 전달. `silent`=빈 막대로 Windows 무음(청크 0) 케이스를 staleness 없이 처리. |
| dBFS 매핑 | **[-54, -6] dBFS → 6칸 선형** | 일반 발화 -30~-20이 중간. env 노브 없이 데스크톱 표시 상수. |

비범위(YAGNI): 새 env 노브, 피크 홀드/감쇠 애니메이션, 캡처 소스 선택, device 제거 자가치유.

## 3. 데이터 흐름

```
청크(s16le) ─ audio_ws.stream_audio
                └ pcm16_dbfs(chunk)            (이미 계산 중, slice 2)
                  └ reporter.note_chunk(now, dbfs)
                      └ 최근 1초 dBFS 롤링 버퍼에 누적

run_watchdog (1초 폴 루프, 기존 태스크)
  ├ reporter.poll(now)  → 전이 시 CAPTURE_STATUS <state>  (기존, app-log 경로)
  └ reporter.level(now) → 매 틱 CAPTURE_LEVEL <dbfs>      (신규, 전용 채널)

Rust spawn_output_forwarder
  ├ "CAPTURE_LEVEL " 접두사 → app.emit("capture-level", {dbfs}) + continue  (app-log 미적재)
  └ 그 외(CAPTURE_STATUS 포함) → emit_backend_log("app-log", ...)            (기존)

데스크톱
  capture-level 이벤트 → captureLevel.ts(useCaptureLevel) → 최신 dBFS
  CaptureStatusChip(state) + CaptureLevelMeter(dbfs, state) → SubtitleHeader
```

## 4. 컴포넌트별 설계

### 4.1 사이드카 — `apps/client_sidecar/transport/capture_status.py`
앵커 `CAPTURE_STATUS_START/END` 내부 수정. 신규 파일 없음.

- 신규 상수 `LEVEL_MARKER = "CAPTURE_LEVEL "`.
- `CaptureStatusReporter`:
  - 최근 1초 `(t, dbfs)` 롤링 버퍼(deque) 추가. `note_chunk(now, dbfs)`에서 append + 1초보다 오래된 항목 정리.
  - 신규 `level(now) -> float | None`: 버퍼 내 최근 1초 dBFS 평균. 최근 ~1.5초 청크가 없으면 `None`(무신호).
  - `poll()`/`compute_state`는 **무변경**(순수성 유지). 레벨은 별도 메서드.
- `run_watchdog`: 1초 틱마다 `poll` 결과(전이)는 기존대로 `emit(state)`, 추가로 `level(now)`가
  `None`이 아니고 ws 연결 상태면 `emit(f"{LEVEL_MARKER}{dbfs:.1f}")`. 레벨은 전이-coalesce
  하지 않고 매 틱 emit(텔레메트리). `emit` 콜백은 기존 시그니처 그대로(문자열 1개).
  - 주의: 레벨 emit은 ws 미연결(transport_down)·청크 없음(connecting)일 때 생략 → 데스크톱이
    굳이 staleness 처리 안 해도 됨(표시 규칙이 state로 거름).

### 4.2 Rust 포워더 — `apps/desktop/src-tauri/src/sidecar.rs`
앵커 `SIDECAR` 내부 수정. 신규 파일 없음.

- `spawn_output_forwarder` 루프에서 디코드된 `message`가 `"CAPTURE_LEVEL "`로 시작하면:
  접두사 뒤 토큰을 `f32`로 파싱 → 성공 시 `app.emit("capture-level", CaptureLevelEvent { dbfs })`
  후 `continue`(app-log emit 건너뜀). 파싱 실패 시 그냥 일반 로그로 흘려보냄(방어적).
- 신규 `#[derive(Clone, Serialize)] struct CaptureLevelEvent { dbfs: f32 }`(기존 `BackendLogEvent` 옆).
- `CAPTURE_STATUS` 등 그 외 모든 줄은 기존 `emit_backend_log` 경로 그대로.

### 4.3 데스크톱 — 신규 파일 2개 + 기존 1개 수정

**신규 `apps/desktop/src/console/captureLevel.ts`** (slice 1 `captureStatus.ts` 패턴 미러):
- 순수 함수 `dbfsToSegments(dbfs: number, segments: number): number` — [-54,-6] 선형 매핑,
  0..segments로 clamp. 단위 테스트 대상.
- `useCaptureLevel(): number | null` — `capture-level` Tauri 이벤트 구독, 최신 dBFS 보관.
  이벤트 끊기면(예: 회의 종료) 마지막 값 유지하되, 표시 여부는 `CaptureLevelMeter`가 state로 판단.
  비-Tauri 런타임(vitest/web)에서는 no-op로 `null`.

**신규 `apps/desktop/src/console/CaptureLevelMeter.tsx`**:
- props `{ dbfs: number | null; state: CaptureState }`.
- 렌더 규칙:
  - `state === "active"`: `dbfsToSegments(dbfs ?? -120, 6)` 칸 채움. 채운 칸 중 상위 칸은
    임계(≥ -12 dBFS 위치) 노랑, (≥ -6) 빨강, 그 외 초록 계열(칩 active 색과 일관).
  - `state === "silent"`: 빈 막대(0칸). dbfs 무시 — Windows 무음=청크0(레벨 None)도 동일 표현.
  - `state === "connecting" | "transport_down"`: `null` 반환(미렌더).
- 인라인 스타일, `CaptureStatusChip`과 동일 톤. `role="img"` + `aria-label`로 접근성(예: "캡처 레벨 4/6").

**수정 `apps/desktop/src/console/LiveSubtitlePreview.tsx`** (앵커 `LIVE_SUBTITLE_PREVIEW`):
- `useCaptureLevel()` 호출, `SubtitleHeader`에 `level` 전달.
- `SubtitleHeader`에서 칩 옆(`subtitleHeaderActions` 내, 칩과 전체화면 버튼 사이)에
  `captureStatus`가 있을 때 `<CaptureLevelMeter dbfs={level} state={captureStatus} />` 렌더.
  비전체화면 분기에서만(기존 칩과 동일 조건).

### 4.4 매핑 상수
`captureLevel.ts`에 모음: `LEVEL_FLOOR_DBFS = -54`, `LEVEL_CEIL_DBFS = -6`, `SEGMENTS = 6`,
과대 경고 임계 `WARN_DBFS = -12`, `CLIP_DBFS = -6`. env 불필요.

## 5. 테스트 계획

- **사이드카 pytest** (`apps/client_sidecar/tests`):
  - `reporter.level()`: 청크 누적 후 1초 평균, 1.5초 무청크 시 `None`, 빈 상태 `None`.
  - 워치독: 1틱에 `CAPTURE_LEVEL ` emit 형식(소수 1자리), 미연결 시 레벨 미emit, 무음(loud 없음)에도
    청크가 흐르면(Mac) 레벨 emit 지속.
- **데스크톱 vitest** (`apps/desktop/src/console`):
  - `dbfsToSegments`: -54→0, -6→6, 중간값, 범위 밖 clamp.
  - `capture-level` 파싱/`useCaptureLevel` 최신값(가능 범위 내, slice 1 테스트 패턴).
- `pnpm --filter ... tsc` 클린. 기존 사이드카 56 + 데스크톱 12 무회귀.

## 6. E2E (Windows, 다음 CI 빌드)

- 재생 중(🟢): 막대가 음량 따라 차고(보통 3~5칸), 큰 소리에 상위 칸 노랑/빨강.
- 일시정지(🟡): 막대 빈칸.
- 네트워크 off(🔴)/연결중(⚪): 미터 사라지고 칩만.
- 진단 로그(설정 패널): `CAPTURE_LEVEL` 줄이 **안 보임**(전용 채널 분리 검증).

## 7. 위험 / 주의

- Rust 포워더는 사이드카 서브트리 전체 stdout을 받음 — `CAPTURE_LEVEL` 접두사 매칭은 정확히
  마커 형식일 때만(파싱 실패는 일반 로그로 흘림)이라 오탐 위험 낮음.
- 1Hz 이벤트가 React 재렌더를 유발하나, `SubtitleHeader`만 영향 + 1초 주기라 무해. 필요 시
  `useCaptureLevel`이 값 변화 없을 때 setState 생략(미세 최적화, 선택).
- 매핑 상수는 실측 후 튜닝 가능(코드 상수라 빌드 필요 — env 승격은 후속, 현재 YAGNI).
