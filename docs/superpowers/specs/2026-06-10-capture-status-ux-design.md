# 실시간 캡처 상태 + 거친 활동 표시 — 설계 (Phase 3 첫 슬라이스)

> 목적: 회의 중 운영자가 **"지금 오디오가 잡히고 있나"**를 한눈에 보게 한다. 06-08 인시던트처럼
> "자막이 안 나오는데 이유를 모름"인 블라인드를 메운다. 캡처 계층의 라이브 상태(연결중/정상/무음/
> 전송끊김)를 데스크톱 상태칩으로 상시 표시한다.
> 상위 기획: `docs/NATIVE_DESKTOP_HELPER_PLAN.md` Phase 3(캡처 상태 표시 / 활동 표시).
> 선행 완료: Phase 2(캡처) + Phase 2b(고아정리·기본장치 추적).

---

## 0. 범위 결정 (브레인스토밍 합의, 2026-06-10)

Phase 3 산출물(상태표시·레벨미터·소스선택·복구안내) 중 **첫 슬라이스 = 실시간 캡처 상태 + 거친 활동
표시**만. 다음은 명시적 **비범위**(후속 슬라이스):
- **정밀 dBFS 레벨 미터** — 네이티브 경로는 현재 RMS 미계산(sounddevice 전용). 새 배선 필요 → 후속.
  본 슬라이스의 "활동"은 **청크 흐름 기반 거친 표시**(정상/무음)로 충분.
- **캡처 소스 선택** — 네이티브 경로는 장치 선택을 무시하고 **항상 Windows 기본 출력**만 캡처하므로
  네이티브에선 사실상 무의미. sounddevice 경로 전용 관심사 → 별도 슬라이스.
- **AI 제공자 오류** — 이미 `LiveSubtitlePreview`의 `providerError` 배너가 처리(별개 계층, Gemini 등).

---

## 1. 결정 요약

| 항목 | 결정 | 근거 |
|---|---|---|
| 데이터 출처 | **사이드카 하트비트(방식 A)** — 사이드카가 `CAPTURE_STATUS <state>` stdout 마커 emit | 로컬, 신규 의존 0, 사이드카가 모든 신호(헬퍼 이벤트+청크흐름+WS상태) 보유. 기존 `NATIVE_STATUS` 마커 배선 재사용 |
| 활동 정밀도 | **거친 활동**(청크 흐름 기반 정상/무음) | "지금 캡처되나" 목적엔 충분. RMS 배선 불필요(YAGNI) |
| 상태 집합 | `connecting` / `active` / `silent` / `transport_down` (+ 실패는 기존 배너) | 라이브 4상태로 운영자 판단 충분 |
| 무음 판정 | **임계 10초 + 정보성(경보 아님) + 비대칭 히스테리시스** | 화자 페이스 비의존(자연 공백보다 훨씬 위), false alarm 회피, 깜빡임 없음 |
| 실패와의 분담 | 캡처 실패(장치없음/권한/크래시)는 **기존 `NativeCaptureBanner`**, 라이브 상태는 **신규 칩** | 배너=actionable 실패(dismissible+설정열기), 칩=상시 라이브 상태. 보완 관계, 중복 없음 |
| 결정 로직 | `capture_status.py` **순수 결정기**(clock 주입) + `CaptureStatusReporter` | device_watch처럼 pytest 가능(asyncio/WS 미의존) |
| 표시 위치 | `LiveSubtitlePreview` 헤더 근처 상태칩, 회의 진행 중에만(라이프사이클 게이트) | 운영자가 출력 보는 곳 |

---

## 2. 상태 집합 (캡처 계층)

| 칩 | state | 의미 | 색 |
|---|---|---|---|
| ⚪ 연결 중 | `connecting` | 회의 시작 직후 — WS 연결/헬퍼 시작, 첫 청크 전 | 회색 |
| 🟢 정상 | `active` | 최근 청크 수신(오디오 캡처·전송 중) | 초록 |
| 🟡 무음 | `silent` | ≥10초 청크 없음 — 캡처는 살아있고 진짜 무음(WASAPI 무패킷). **정보성**, 경보 아님 | 노랑 |
| 🔴 전송 끊김 | `transport_down` | 서버 WS 끊김/재연결 중(백오프) | 빨강 |

**실패는 칩이 아니라 배너**: `no_default_render_device`/`permission_denied`/`wasapi_init_failed`/크래시는
`NativeCaptureError`→`main.py`가 `NATIVE_STATUS <reason>` emit → 기존 `NativeCaptureBanner` 표시(무변경).
칩과 배너는 같은 app-log를 독립 구독, 서로 결합 없음.

**idle(회의 전/후)**: 칩 미표시(부모 패널이 라이프사이클로 가시성 제어).

---

## 3. 데이터 흐름

```
[사이드카]
 main.py::audio_main
   reporter = CaptureStatusReporter()
   watchdog = asyncio.create_task(run_watchdog(reporter, emit=print_marker, interval=1s))
   try: await stream_audio(url, source.chunks(), reporter)
   finally: watchdog.cancel()

 audio_ws.py::stream_audio(url, chunks, reporter)
   connect 성공      → reporter.set_connected(True)   # ever_connected=True
   async for chunk:  → reporter.note_chunk(now)       # now=time.monotonic()
   except ws closed  → reporter.set_connected(False)  # 재연결 백오프 동안 유지

 watchdog(매 ~1초):  state = reporter.poll(now)
   if state 변함:    print(f"CAPTURE_STATUS {state}", flush=True)   # 전이 시에만(coalesced)

[Rust] sidecar.rs: stdout → app-log 이벤트 포워딩 (무변경)

[데스크톱]
 parseCaptureStatus(msg) → state | null         # "CAPTURE_STATUS <state>" 파싱(알려진 집합만)
 useCaptureStatus() → 최신 state                # app-log 구독, latest-wins (nativeCaptureStatus 패턴)
 <CaptureStatusChip state=…/>                   # 색+라벨, 회의 진행 중에만 렌더
```

- **무음 감지 핵심**: 네이티브 경로는 무음 시 패킷 0 → `async for chunk`가 블록됨. 워치독은 **독립
  asyncio 태스크**라 블록과 무관하게 `last_chunk_at` 경과를 보고 무음 전이를 emit.
- **시계**: `time.monotonic()` 사용(벽시계 변경에 견고). 결정기엔 `now`로 주입.

---

## 4. 순수 결정기 — `apps/client_sidecar/transport/capture_status.py` (신규)

cpal/WS/asyncio 미의존. 타임스탬프+불리언만 받는 순수 함수 → pytest로 모든 전이 검증(device_watch 패턴).

```python
SILENCE_THRESHOLD_S = 10.0  # 자연 대화 공백보다 훨씬 위 — 화자 페이스 비의존

def compute_state(*, ws_connected, ever_connected, last_chunk_at, now, threshold=SILENCE_THRESHOLD_S):
    if not ws_connected:
        return "transport_down" if ever_connected else "connecting"
    if last_chunk_at is None:
        return "connecting"                      # 연결됐으나 첫 청크 전
    if now - last_chunk_at >= threshold:
        return "silent"
    return "active"

class CaptureStatusReporter:
    # 보관: ws_connected, ever_connected, last_chunk_at, _emitted
    def set_connected(self, ok): ...             # ok면 ever_connected=True
    def note_chunk(self, now): self.last_chunk_at = now
    def poll(self, now) -> str | None:           # compute 후 _emitted와 다르면 새 state 반환(전이만)
```

**우선순위/히스테리시스**: ① `transport_down`(WS down)이 무음보다 우선 ② 무음→정상은 청크 들어오는
즉시(`note_chunk`가 `last_chunk_at` 갱신→다음 poll이 active), 정상→무음만 10초 인내 = **비대칭**.
③ `connecting`(최초)과 `transport_down`(연결됐다 끊김)은 `ever_connected`로 구분.

워치독 루프(`run_watchdog`)는 thin: `while True: await sleep(interval); s = reporter.poll(monotonic()); if s: emit(s)`.

---

## 5. 데스크톱 — 파서 + 훅 + 칩

| 단위 | 책임 | 테스트 |
|---|---|---|
| `captureStatus.ts` `parseCaptureStatus(msg)` | `"CAPTURE_STATUS <state>"`에서 state 추출, 알려진 4집합만 통과(else null) | vitest(파서) |
| `captureStatus.ts` `useCaptureStatus()` | app-log 구독, 최신 CAPTURE_STATUS state(latest-wins by id) | vitest(리듀서 latest-wins) |
| `CaptureStatusChip.tsx` | state→{색,라벨,이모지} 렌더. 회의 진행 중에만(부모 게이트) | tsc |

- `nativeCaptureStatus.ts`의 마커 파싱 패턴을 그대로 미러(검증된 패턴).
- **배치**: `LiveSubtitlePreview` 헤더에 칩. 기존 `NativeCaptureBanner`는 그대로(실패 시 배너, 진행 중 칩).
- **stale 방지**: app-log는 메모리 append라 이전 세션 CAPTURE_STATUS가 남음. 부모가 **회의 진행 중에만**
  칩을 렌더 → idle엔 안 보임. 새 세션 시작 시 첫 새 마커(~1초)까지 직전 값이 잠깐 보일 수 있으나
  무해(곧 갱신). (선택적 강화: 세션 id 변경 시 훅 리셋 — 1차 비범위.)

---

## 6. 명시적 비범위

- **정밀 dBFS 레벨 미터** — RMS 배선 필요(네이티브 경로 미계산). 후속 슬라이스.
- **캡처 소스 선택** — 네이티브는 기본장치 고정이라 무의미. sounddevice 전용 → 별도.
- **서버 audio_stats 연동(방식 B)** — 본 슬라이스는 로컬 사이드카 하트비트만(엔드투엔드 서버도달
  교차확인은 후속 필요 시).
- **AI 제공자 오류 표시** — 이미 `providerError` 배너가 담당(별개 계층).
- **sounddevice 경로의 무음 의미** — sounddevice는 무음에도 청크가 흐를 수 있어 `silent`가 거의 안 뜸.
  네이티브가 프로덕션 경로라 수용(칩은 청크흐름 기반으로 양 경로 동일 동작, 정보성).

---

## 7. 주요 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| 마커 스팸(매초 print) | 로그 오염 | **전이 시에만 emit**(reporter.poll이 `_emitted` 대비 변할 때만 반환) |
| 워치독 태스크 누수 | 종료 후 잔존 | `audio_main`에서 `try/finally`로 cancel |
| 이전 세션 stale 칩 | 새 세션 초기 잠깐 직전 상태 | 부모가 회의 진행 중에만 렌더 + ~1초 내 갱신. (선택: 세션 id 리셋) |
| 무음 임계가 화자별로 다름 | false alarm | 임계 10초(자연 공백보다 위) + 정보성(경보 아님) + 비대칭 히스테리시스 = 화자 비의존 |
| sounddevice 경로 무음 안 뜸 | 일관성 | 수용(네이티브가 프로덕션). 거친 활동은 양 경로 청크흐름 동일 |
| 벽시계 변경 | 오판 | `time.monotonic()` 사용 |

---

## 8. 검증 전략

- **사이드카 pytest** (`test_capture_status.py`): `compute_state` 전이 전수 — connecting(미연결/연결후 첫청크전),
  active(최근 청크), silent(gap≥10s), transport_down(연결 후 끊김), 무음→정상 즉시 복구, transport_down 우선,
  ever_connected 구분. `poll`이 전이 시에만 반환(coalesce). clock 주입으로 결정적.
- **데스크톱 vitest** (`captureStatus.test.ts`): `parseCaptureStatus`(유효/무효/미지 state), 훅 latest-wins.
- **Windows 수동 E2E**: 재생→🟢정상 / 일시정지 10초→🟡무음 / 재개→즉시🟢 / 서버 종료→🔴전송끊김 /
  캡처 실패(장치 비활성)→칩 빠지고 기존 배너.
- **Mac**: 사이드카 pytest + 데스크톱 vitest + tsc로 전부 검증 가능(워치독 배선만 Windows E2E).

---

## 9. 산출물 체크리스트

- [ ] `transport/capture_status.py`(신규): `compute_state` 순수 결정기 + `CaptureStatusReporter` + `run_watchdog` + `SILENCE_THRESHOLD_S`
- [ ] `test_capture_status.py`(신규): 결정기 전이 + poll coalesce, pytest 통과
- [ ] `audio_ws.py::stream_audio`: reporter 인자 + 3지점 업데이트(connect/chunk/disconnect)
- [ ] `main.py::audio_main`: reporter 생성 + 워치독 태스크 spawn/cancel(try/finally) + `CAPTURE_STATUS` print
- [ ] `console/captureStatus.ts`(신규): `parseCaptureStatus` + `useCaptureStatus`
- [ ] `console/captureStatus.test.ts`(신규): 파서 + 훅 latest-wins, vitest 통과
- [ ] `console/CaptureStatusChip.tsx`(신규): state→색/라벨, 회의 진행 중 렌더
- [ ] `LiveSubtitlePreview`(또는 부모): 칩 배치 + 라이프사이클 가시성 게이트
- [ ] Mac 검증: 사이드카 pytest + 데스크톱 vitest + tsc
- [ ] Windows 수동 E2E: 정상/무음/전송끊김/실패 4상태 실측
- [ ] 문서 동기화: `NATIVE_DESKTOP_HELPER_PLAN.md` Phase 3 / `ROADMAP.md`
