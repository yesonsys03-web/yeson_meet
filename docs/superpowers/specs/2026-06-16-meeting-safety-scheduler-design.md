# 회의 안전 타이머 — Background Scheduler

- **Date:** 2026-06-16
- **Scope:** A — 벽시계 최대시간 강제만 (유휴 타임아웃 B는 범위 밖)
- **Branch:** topyeson
- **Status:** Approved design, pre-implementation

## 1. 문제

회의 세션이 설정된 최대 시간(`YESON_MEETING_MAX_DURATION_HOURS`, 기본 3h)을 초과하면
Gemini Live 비용이 계속 누적된다. 현재 강제종료(`enforce_meeting_duration_limit`)는
**오디오 청크가 흐를 때만** 호출된다(`apps/server/ws/sidecar.py` ingress, binary 프레임 수신부).

좀비 세션 — 사이드카가 조용하거나(무음) 프로세스가 hang 되어 더 이상 청크를 보내지 않는
세션 — 은 ingress 체크가 다시 돌지 않으므로 영원히 강제종료되지 않는다. 결과적으로
Gemini 비용이 무한정 누적될 수 있는 갭이 존재한다.

## 2. 목표 / 비목표

**목표**
- 시작 후 최대 시간을 초과한 모든 live 세션을, 오디오 흐름과 무관하게 자동 종료한다.
- ingress 경로와 scheduler 경로가 **동일한** 강제종료 동작(상태 마킹 + 운영자 alert +
  viewer 통지)을 공유한다.

**비목표**
- 유휴(무음) 기반 타임아웃(scope B). 별개 작업.
- 멀티워커 환경의 분산 리더 선출/락. 현재 단일 프로세스 배포라 범위 밖(§6 참고).

## 3. 기존 코드 사실 (검증됨)

- `apps/server/ops/session_safety.py`
  - `meeting_max_duration()` — env `YESON_MEETING_MAX_DURATION_HOURS`(기본 3h), `≤0`이면
    `timedelta.max`(비활성).
  - `session_started_at_exceeds_max_duration(started_at, now=None) -> bool` — 순수 체크.
  - `enforce_meeting_duration_limit(db, meeting, now=None) -> bool` — `status="ended"` +
    `ended_at` + `commit` + `raise_meeting_max_duration_alert`. **멱등**(이미 `ended`면 `False`).
    현재 **viewer 버스 통지 없음, 리포트 작성 없음**.
- `apps/server/api/v1/sessions.py::end_session` (수동 종료, HTTP) — 상태 마킹 +
  `write_session_report` + `bus.publish(SessionEnded)`. 리포트는 `download_session_report`가
  없으면 다운로드 시 **지연 생성**하므로 종료 시점 eager write는 필수가 아닌 최적화.
- `apps/server/main.py::lifespan` — 현재 gemini 헬스 체크 후 `yield`만. `asyncio.create_task`로
  watchdog 시작 + `finally`에서 cancel하기 적합.
- `apps/server/db/session.py` — `AsyncSessionLocal` 세션메이커를 요청 밖에서
  `async with AsyncSessionLocal() as db:`로 직접 사용 가능.
- 순환 임포트 위험 없음: `apps/server/ws/bus.py`·`apps/server/domain/events.py`는 외부 import 없음.

## 4. 설계

### 4.1 변경 ① — 공유 enforce에 viewer 통지 추가 (`session_safety.py`)

강제종료 성공 시(`return True` 직전) `SessionEnded`를 버스로 발행한다. 이로써 ingress·scheduler
양 경로가 자동으로 일관된 viewer 통지를 갖는다. 리포트는 기존대로 다운로드 시 지연 생성한다.

```python
meeting.status = "ended"
meeting.ended_at = ended_at
await db.commit()
raise_meeting_max_duration_alert(str(meeting.external_id))
await bus.publish(
    meeting.external_id,
    serialize(SessionEnded(
        session_id=meeting.external_id,
        occurred_at=ended_at,
        ended_at=ended_at,
    )),
)
return True
```

신규 import: `from apps.server.ws.bus import bus`,
`from apps.server.domain.events import SessionEnded, serialize`.

### 4.2 변경 ② — 신규 모듈 `apps/server/ops/session_safety_scheduler.py`

순수 predicate(`session_safety.py`)와 장기 실행 루프(orchestration)는 책임이 다르므로 분리한다
(`ops/alerts.py` vs `ops/session_safety.py`로 관심사를 나누는 기존 패턴과 일치).

- `safety_poll_interval() -> float` — env `YESON_MEETING_SAFETY_POLL_SECONDS`(기본 `60.0`).
  `≤0`이면 watchdog을 띄우지 않는다(opt-out, `meeting_max_duration() ≤0` 비활성 패턴과 대칭).
- `async def _sweep_once() -> int` — `AsyncSessionLocal`로 `Session.status == "live"` 전부 조회 →
  각각 `enforce_meeting_duration_limit(db, meeting)` 호출 → 종료된 세션 수를 반환. (테스트 핵심 단위)
- `async def run_meeting_safety_watchdog(interval_seconds: float | None = None) -> None` —
  `interval = interval_seconds or safety_poll_interval()`; `while True: _sweep_once();
  await asyncio.sleep(interval)`. `asyncio.CancelledError`는 재발생, 그 외 예외는
  `logger.exception(...)` 후 루프 계속(한 번의 DB 실패가 watchdog을 죽이지 않게).

### 4.3 변경 ③ — 와이어링 (`main.py` lifespan)

```python
interval = safety_poll_interval()
watchdog = (
    asyncio.create_task(run_meeting_safety_watchdog(interval)) if interval > 0 else None
)
try:
    yield
finally:
    if watchdog is not None:
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog
```

## 5. 데이터 흐름

```
[watchdog loop] --interval--> _sweep_once()
    -> SELECT live sessions
    -> for each: enforce_meeting_duration_limit(db, meeting)
        -> (over duration?) status=ended + ended_at + commit
        -> raise_meeting_max_duration_alert  (운영자 alert)
        -> bus.publish(SessionEnded)         (viewer '회의 종료')
[viewer ws] <-- SessionEnded -- bus
[operator ws] <-- alert -- alert store
[report] <-- lazy build on /sessions/{id}/report download
```

## 6. 동시성 / 멀티워커

현재 단일 프로세스(uvicorn `reload=False`, 단일 워커). 멀티워커로 확장되면 각 워커가
watchdog을 돌려 sweep이 중복되지만, `enforce_meeting_duration_limit`이 멱등이므로 안전하다
(먼저 commit한 워커가 종료, 나머지는 `status=="ended" → False`). 단 `InMemoryBus`는 프로세스별
이라 다른 워커에 연결된 viewer는 `SessionEnded`를 못 받는다 — 이는 **기존 멀티워커 한계로 범위 밖**.
모듈 docstring에 가정만 명시한다.

## 7. 에러 처리

- `_sweep_once` 내 예외는 `run_meeting_safety_watchdog`에서 잡아 `logger.exception` 후 루프를
  계속한다(한 번의 DB 오류가 watchdog을 영구 중단시키지 않음).
- `asyncio.CancelledError`는 잡지 않고 재발생시켜 lifespan `finally`의 cancel이 깨끗이 종료되게 한다.

## 8. 테스트 전략

capture_status `run_watchdog` 선례를 따른다. 시간 의존 케이스는 `started_at`을 과거로 시드해
실제 시각으로 초과를 트리거하고(별도 클록 주입 불필요), 루프는 주입 가능한 짧은 interval로 검증한다.

- `_sweep_once`:
  - 과기간 live 세션 → `ended` 마킹 + alert + `SessionEnded` 버스 발행, 반환 1.
  - 신선한 live 세션 → 무변경, 반환 0, `status=="live"` 유지.
  - 이미 `ended` 세션 → 스킵(멱등).
- enforce 버스 발행:
  - 버스 구독 후 과기간 세션 강제종료 → `SessionEnded` 수신 검증.
- watchdog 루프:
  - 짧은 interval로 task 띄움 → sweep 1회 이상 호출 확인 → cancel → 깨끗한 종료(예외 없음).

## 9. 설정 (env)

| 변수 | 기본 | 의미 |
| --- | --- | --- |
| `YESON_MEETING_MAX_DURATION_HOURS` | `3.0` | 회의 최대 시간(기존). `≤0` 비활성. |
| `YESON_MEETING_SAFETY_POLL_SECONDS` | `60.0` | watchdog 폴 주기(신규). `≤0`이면 watchdog 미가동. |
