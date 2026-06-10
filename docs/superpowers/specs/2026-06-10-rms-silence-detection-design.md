# RMS 기반 무음 감지 (양 플랫폼 통일) — 설계 (Phase 3 slice 2)

> 목적: 캡처 상태칩의 🟡무음 상태를 **양 플랫폼에서 정확히** 동작시킨다. 청크 *존재*가 아니라 청크의
> *소리 크기*(RMS dBFS)로 무음을 판정해, Mac처럼 무음에도 청크가 계속 흐르는 경로에서도 무음이 잡히게 한다.
> 선행: Phase 3 slice 1(실시간 캡처 상태칩, `2026-06-10-capture-status-ux-design.md`).

---

## 0. 동기 — 실측으로 확정된 문제

slice 1의 무음 판정은 **청크 흐름 기반**(`compute_state`가 `last_chunk_at` 경과 ≥ 10초면 silent)이다.
이는 Windows WASAPI loopback(무음 시 패킷 0)에선 맞지만, **Mac ScreenCaptureKit은 무음에도 0으로 채운
버퍼를 계속 송출**한다.

**실측(2026-06-10, 이 Mac, 릴리스 헬퍼)**: 6초 완전 무음에 stdout **158,720 바이트**(≈248개 640B 프레임,
거의 풀레이트). 오디오 재생 시 205,440 B. 즉 Mac은 무음/유음 모두 거의 같은 양의 청크를 낸다 →
slice 1의 `last_chunk_at`은 Mac에서 무음에도 계속 갱신 → **Mac에선 🟡무음이 사실상 안 뜸**(늘 🟢정상).

→ 해결: 무음 신호를 "청크 없음"에서 **"소리 있는 청크 없음"**으로 바꾼다(RMS 게이팅).

---

## 1. 결정 요약 (브레인스토밍 합의, 2026-06-10)

| 항목 | 결정 | 근거 |
|---|---|---|
| 적용 범위 | **양 플랫폼 통일** — Windows도 청크흐름 대신 RMS 기반으로 | "무음 = 들리는 소리 없음" 단일 코드경로. Windows도 동일 동작(무음=청크없음=loud없음=silent) |
| 슬라이스 범위 | **무음 판정만** — 레벨 미터 아님 | RMS는 loud/silent 판정에만. dBFS 미터는 별도 후속 슬라이스(YAGNI) |
| 무음 신호 | "마지막으로 *소리 있던* 청크" 시각(`last_loud_at`) ≥ 10초 경과 | 청크 존재(`last_chunk_at`)는 connecting 탈출에만 |
| RMS 임계 | 기존 `YESON_RMS_DBFS_THRESHOLD`(기본 **-60 dBFS**) 재사용 | 사이드카가 이미 받는 env, 일관·조정 가능 |
| RMS 계산 위치 | `audio_ws.py::stream_audio`(청크 바이트 보유), 기존 `rms.py` 재사용 | 스칼라 dbfs만 리포터로 전달 → 리포터 순수 유지 |
| 데스크톱 | **무변경** | 같은 `CAPTURE_STATUS` 마커·4상태. 칩/파서 그대로 |

---

## 2. 핵심 메커니즘 — 두 타임스탬프

slice 1의 단일 `last_chunk_at`을 두 개로 분리한다:
- `last_chunk_at`: **아무 청크나** 수신 시각 → connecting 탈출(파이프라인 확인)용.
- `last_loud_at`: **dBFS ≥ 임계인 청크** 수신 시각 → active/silent 판정용.

```
note_chunk(now, dbfs):
    last_chunk_at = now
    if dbfs >= rms_threshold_dbfs:
        last_loud_at = now

compute_state(ws_connected, ever_connected, last_chunk_at, last_loud_at, now, threshold_s):
    if not ws_connected:
        return TRANSPORT_DOWN if ever_connected else CONNECTING
    if last_chunk_at is None:                 # 청크 0개 — 아직 흐름 미확인
        return CONNECTING
    if last_loud_at is None or now - last_loud_at >= threshold_s:
        return SILENT                         # 소리 있는 청크가 10초+ 없음
    return ACTIVE
```

**양 플랫폼 통일 증명:**
- **Windows**(무음=청크 0): 무음 구간 loud 청크 0 → `last_loud_at` 정지 → 10초 후 SILENT. (slice 1과 동일 결과)
- **Mac**(무음=조용한 청크 계속): 청크는 와서 `last_chunk_at`은 갱신되나 dbfs<임계라 `last_loud_at` 정지 →
  10초 후 SILENT. **(새로 고쳐지는 경로)**

**비대칭 히스테리시스 유지**: 정상→무음 10초, 무음→정상은 소리 있는 청크 하나에 즉시(`note_chunk`가
`last_loud_at` 갱신 → 다음 poll이 ACTIVE). transport_down 우선, ever_connected로 connecting/transport_down 구분 — 모두 slice 1과 동일.

---

## 3. RMS 계산

`stream_audio`의 청크는 **16kHz mono s16le 640바이트**. 변환 후 dBFS:

```python
samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
dbfs = rms_dbfs(samples)          # apps/client_sidecar/audio/rms.py (기존)
reporter.note_chunk(time.monotonic(), dbfs)
```

- 50/s × 320샘플 → CPU 무시 가능.
- 작은 헬퍼 `pcm16_dbfs(chunk: bytes) -> float`를 `rms.py`에 추가(변환+`rms_dbfs` 1줄 래퍼)해
  `audio_ws`가 호출 → transport 레이어에 numpy 변환 로직이 흩어지지 않게.
- 스무딩 불필요: 단일 loud 청크가 `last_loud_at`을 리셋(소리가 있었다는 뜻이므로 의도된 동작),
  10초 윈도가 스무딩 역할.

---

## 4. 모듈 구조 / 파일

| 파일 | 변경 | 테스트 |
|---|---|---|
| `transport/capture_status.py` | `compute_state`에 `last_loud_at` 추가(silent는 last_loud_at, connecting은 last_chunk_at); `CaptureStatusReporter`에 `rms_threshold_dbfs` + `note_chunk(now, dbfs)`에 loud 판정; `RMS_SILENCE_DBFS` 기본 -60 | pytest(결정기 전이 + RMS) |
| `audio/rms.py` | `pcm16_dbfs(chunk: bytes) -> float` 헬퍼 추가 | pytest(무음/풀스케일 바이트) |
| `transport/audio_ws.py` | 청크당 `reporter.note_chunk(now, pcm16_dbfs(chunk))` (기존 `note_chunk(now)` 교체) | (배선; E2E) |
| `main.py::audio_main` | 리포터 생성 시 `rms_threshold_dbfs` 주입(env `YESON_RMS_DBFS_THRESHOLD`, 기본 -60) | (배선) |
| 데스크톱 `captureStatus.ts`/`CaptureStatusChip` | **무변경** | — |

---

## 5. 검증 전략

- **사이드카 pytest** (`test_capture_status.py` 확장):
  - 핵심 신규: **"청크는 매 poll마다 오지만 전부 조용함(dbfs<임계) → 10초 후 SILENT"** (Mac 시나리오 증명).
  - loud 청크 → ACTIVE; 무음→정상 즉시 복구; last_loud_at None인데 청크 흐름(Mac 시작부터 무음) → SILENT;
    Windows 동등성(loud 청크만 와도 정상 동작); 기존 transport_down/connecting/coalesce 회귀.
- **`rms.py` pytest**: `pcm16_dbfs` — 0 바이트/무음 바이트 → 매우 낮은 dBFS, 풀스케일 → ~0 dBFS.
- **Mac 실측 E2E**: 사이드카+헬퍼 가동, 무음 10초 → 칩 🟡무음(slice 1에선 안 떴던 것), 소리 → 즉시 🟢.
- **Windows 회귀 E2E**: slice 1과 동일하게 재생→정상/일시정지→무음/서버종료→전송끊김 4상태 유지.
- Mac에서 사이드카 pytest + 실측으로 핵심 검증 가능.

---

## 6. 명시적 비범위 / 알려진 엣지

- **dBFS 레벨 미터** — RMS를 계산하지만 값은 내보내지 않음(전이 마커만). 미터는 별도 후속 슬라이스
  (연속 레벨 스트림 + 미터 UI).
- **무음 게이팅(Gemini 비용 절감)** — `should_gate_silence`는 별개 기능, 본 슬라이스 무관.
- **알려진 minor 엣지**: Windows에서 회의 시작부터 한 번도 소리가 안 난 경우 청크가 0개라 `connecting`에
  머묾(Mac은 조용한 청크가 와서 즉시 `silent`). 소리가 한 번이라도 나면 해소. slice 1 출하 동작과
  동일이라 수용. (필요 시 후속에서 connecting 타임아웃으로 통일.)
- **임계값 튜닝** — -60 dBFS 기본. 실측에서 너무 민감/둔감하면 env로 조정(코드 변경 불필요).

---

## 7. 산출물 체크리스트

- [ ] `rms.py`: `pcm16_dbfs(chunk: bytes) -> float` + pytest
- [ ] `capture_status.py`: `compute_state(last_loud_at 추가)` + `CaptureStatusReporter(rms_threshold_dbfs, note_chunk(now,dbfs))` + `RMS_SILENCE_DBFS` 기본 -60
- [ ] `test_capture_status.py`: 신규 RMS 케이스(특히 Mac "조용한 청크 계속 → silent") + 기존 회귀
- [ ] `audio_ws.py`: `note_chunk(now)` → `note_chunk(now, pcm16_dbfs(chunk))`
- [ ] `main.py`: 리포터에 `rms_threshold_dbfs` 주입(env -60 기본)
- [ ] 데스크톱 무변경 확인(vitest/tsc 회귀 그린)
- [ ] Mac 실측: 무음 10초 → 🟡무음 (slice 1 대비 개선)
- [ ] Windows 회귀 E2E: 4상태 유지
- [ ] 문서 동기화: `NATIVE_DESKTOP_HELPER_PLAN.md` Phase 3 / `ROADMAP.md`
