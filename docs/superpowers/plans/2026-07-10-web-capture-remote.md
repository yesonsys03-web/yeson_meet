# 웹 캡처 원격 지원 + 보안강화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 원격 진행자가 `https://<터널>/capture`에서 자립(로그인→회의→캡처)으로 회의를 진행할 수 있게 하고, 그 전제인 보안 4종(로그인 rate-limit / 세션 스코프 캡처 토큰 / self-enroll 웹 제거 / 터널 허용리스트 메서드 인지형 확장)을 넣는다.

**Architecture:** 서버에 인메모리 캡처 토큰 저장소와 신규 `/ws/capture`(첫 메시지 인증)를 추가하고, 기존 `/ws/sidecar`의 인증 후 스트림 루프를 공용 함수로 추출해 재사용한다. 웹 캡처는 영구 디바이스키 대신 세션당 1회용 토큰으로 전환하고 미리보기는 REST 폴링으로 통일한다. 터널 프록시(`tunnel_proxy.rs`)는 deny-by-default를 유지한 채 메서드+경로 허용 항목만 추가한다.

**Tech Stack:** FastAPI(pytest), React+TS(vitest), Rust hyper(tauri, cargo test)

**Spec:** `docs/superpowers/specs/2026-07-10-web-capture-remote-design.md`

## Global Constraints

- DB 스키마 무변경 (토큰·rate-limit 전부 인메모리; `project_bundle_additive_migration` 함정 회피)
- 어떤 토큰/키도 URL 쿼리에 싣지 않음 (신규 코드 기준)
- `/ws/sidecar`(영구 디바이스키)·`/api/v1/devices/self-enroll`·`/ws/operator`는 서버 측 무변경, 터널 허용리스트 불포함
- `tunnel_proxy.rs`는 deny-by-default·정규화 파이프라인(`normalize_path`) 무변경, 앵커(`ANCHOR: TUNNEL_PROXY_*`) 경계 준수
- apps/server 파이썬 소스 변경 후 실기기 검증 전 **재동결(`build-server.sh`) 필수**, tauri:dev 중 재동결 금지
- 테스트: 서버 `python -m pytest apps/server/tests -q`, 웹 `cd apps/web && pnpm test -- --run`, Rust `cargo test --manifest-path apps/server_desktop/src-tauri/Cargo.toml tunnel_proxy`
- 커밋 메시지 끝에 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: 로그인 rate-limit

**Files:**
- Create: `apps/server/auth/login_rate_limit.py`
- Modify: `apps/server/api/v1/auth.py` (login 함수, `ANCHOR: AUTH_LOGIN` 주변)
- Test: `apps/server/tests/test_login_rate_limit.py` (신규), `apps/server/tests/test_auth.py` (기존 통과 확인만)

**Interfaces:**
- Produces: `login_rate_limiter: LoginRateLimiter` 싱글턴 —
  `check(email: str) -> float | None`(잠금 중이면 남은 초, 아니면 None),
  `record_failure(email: str) -> None`, `record_success(email: str) -> None`,
  `reset() -> None`(테스트용). 생성자 `LoginRateLimiter(max_fails=5, lockout_seconds=300.0, clock=time.monotonic)`.

- [ ] **Step 1: 실패하는 테스트 작성**

`apps/server/tests/test_login_rate_limit.py`:

```python
"""계정 기준 로그인 rate-limit 단위 테스트 (주입 가능한 clock 사용)."""
from apps.server.auth.login_rate_limit import LoginRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_under_threshold():
    clock = FakeClock()
    rl = LoginRateLimiter(max_fails=5, lockout_seconds=300.0, clock=clock)
    for _ in range(4):
        rl.record_failure("a@b.c")
    assert rl.check("a@b.c") is None


def test_locks_after_max_fails_and_reports_retry_after():
    clock = FakeClock()
    rl = LoginRateLimiter(max_fails=5, lockout_seconds=300.0, clock=clock)
    for _ in range(5):
        rl.record_failure("a@b.c")
    retry_after = rl.check("a@b.c")
    assert retry_after is not None and 0 < retry_after <= 300.0


def test_lockout_expires_after_window():
    clock = FakeClock()
    rl = LoginRateLimiter(max_fails=5, lockout_seconds=300.0, clock=clock)
    for _ in range(5):
        rl.record_failure("a@b.c")
    clock.now += 300.1
    assert rl.check("a@b.c") is None
    # 만료 후 실패 카운터도 초기화 — 1회 실패로 다시 잠기지 않는다
    rl.record_failure("a@b.c")
    assert rl.check("a@b.c") is None


def test_success_resets_counter():
    clock = FakeClock()
    rl = LoginRateLimiter(max_fails=5, lockout_seconds=300.0, clock=clock)
    for _ in range(4):
        rl.record_failure("a@b.c")
    rl.record_success("a@b.c")
    for _ in range(4):
        rl.record_failure("a@b.c")
    assert rl.check("a@b.c") is None


def test_accounts_are_independent():
    clock = FakeClock()
    rl = LoginRateLimiter(max_fails=5, lockout_seconds=300.0, clock=clock)
    for _ in range(5):
        rl.record_failure("a@b.c")
    assert rl.check("other@b.c") is None


def test_email_key_is_case_insensitive():
    clock = FakeClock()
    rl = LoginRateLimiter(max_fails=5, lockout_seconds=300.0, clock=clock)
    for _ in range(5):
        rl.record_failure("A@B.C")
    assert rl.check("a@b.c") is not None
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest apps/server/tests/test_login_rate_limit.py -q`
Expected: FAIL (`ModuleNotFoundError: apps.server.auth.login_rate_limit`)

- [ ] **Step 3: 구현**

`apps/server/auth/login_rate_limit.py`:

```python
# === ANCHOR: LOGIN_RATE_LIMIT_START ===
"""계정(email) 기준 인메모리 로그인 rate-limit.

터널 경유 요청은 원 IP가 에지에 가려질 수 있어 IP 기준은 신뢰할 수 없다 —
계정 기준 연속 실패 잠금이 주 방어선이다. 번들 서버는 단일 프로세스 uvicorn이라
인메모리로 충분하며 DB 스키마를 건드리지 않는다(서버 재시작 시 카운터 소실 수용).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class _Entry:
    fails: int = 0
    locked_until: float = 0.0


@dataclass
class LoginRateLimiter:
    max_fails: int = 5
    lockout_seconds: float = 300.0
    clock: Callable[[], float] = time.monotonic
    _entries: dict[str, _Entry] = field(default_factory=dict)

    @staticmethod
    def _key(email: str) -> str:
        return email.strip().lower()

    def check(self, email: str) -> float | None:
        """잠금 중이면 남은 초(> 0), 허용이면 None."""
        entry = self._entries.get(self._key(email))
        if entry is None:
            return None
        now = self.clock()
        if entry.locked_until > now:
            return entry.locked_until - now
        if entry.locked_until:
            # 잠금 만료 — 카운터째 초기화
            del self._entries[self._key(email)]
        return None

    def record_failure(self, email: str) -> None:
        entry = self._entries.setdefault(self._key(email), _Entry())
        entry.fails += 1
        if entry.fails >= self.max_fails:
            entry.locked_until = self.clock() + self.lockout_seconds

    def record_success(self, email: str) -> None:
        self._entries.pop(self._key(email), None)

    def reset(self) -> None:
        self._entries.clear()


login_rate_limiter = LoginRateLimiter()
# === ANCHOR: LOGIN_RATE_LIMIT_END ===
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest apps/server/tests/test_login_rate_limit.py -q`
Expected: 6 passed

- [ ] **Step 5: login 엔드포인트 배선 — 실패하는 테스트 먼저**

`apps/server/tests/test_login_rate_limit.py`에 추가. 픽스처는 conftest.py의 실제 이름을 쓴다 — `admin_user: AppUser`(비밀번호 평문 `"test-admin-pw"`), `client: AsyncClient`, 마커는 `@pytest.mark.asyncio`(test_auth.py와 동일):

```python
import pytest
from httpx import AsyncClient

from apps.server.auth.login_rate_limit import login_rate_limiter
from apps.server.db.models import AppUser


@pytest.fixture(autouse=True)
def _reset_limiter():
    login_rate_limiter.reset()
    yield
    login_rate_limiter.reset()


@pytest.mark.asyncio
async def test_login_locked_returns_429(admin_user: AppUser, client: AsyncClient) -> None:
    for _ in range(5):
        r = await client.post(
            "/api/v1/auth/login", json={"email": admin_user.email, "password": "wrong"}
        )
        assert r.status_code == 401
    r = await client.post(
        "/api/v1/auth/login", json={"email": admin_user.email, "password": "wrong"}
    )
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    # 잠금 중에는 올바른 비밀번호도 거부(온라인 브루트포스 무력화의 핵심)
    r = await client.post(
        "/api/v1/auth/login", json={"email": admin_user.email, "password": "test-admin-pw"}
    )
    assert r.status_code == 429
```

참고: 스펙 §4.1의 "IP당 분당 20회 보조 제한"은 구현하지 않는다(퀵터널 경유 시 원 IP가 에지에 가려져 실효 없음 + 공유 IP 오차단 위험 — 계정 기준 잠금+고정 지연이 주 방어선). 이 결정은 스펙에 이미 "가능할 때만"으로 유보돼 있다.

- [ ] **Step 6: 실패 확인**

Run: `python -m pytest apps/server/tests/test_login_rate_limit.py -q`
Expected: 새 테스트 FAIL (429 대신 401)

- [ ] **Step 7: auth.py 수정**

`apps/server/api/v1/auth.py`의 login 함수 본문을 다음으로 교체(앵커 `AUTH_LOGIN_START/END`는 시그니처를 감싸므로 본문 수정은 앵커 밖 — import 2줄 추가 + 본문 로직 삽입):

```python
import asyncio  # 상단 import에 추가

from apps.server.auth.login_rate_limit import login_rate_limiter  # 상단 import에 추가

_LOGIN_FAIL_DELAY_SECONDS = 0.3  # 온라인 시도 속도 제한(고정 지연)


# login 함수 본문:
async def login(...) -> TokenPair:  # 시그니처는 기존 그대로(앵커 보존)
    retry_after = login_rate_limiter.check(body.email)
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed login attempts",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    user = (
        await db.execute(
            select(AppUser).where(
                AppUser.email == body.email,
                AppUser.is_active == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        login_rate_limiter.record_failure(body.email)
        await asyncio.sleep(_LOGIN_FAIL_DELAY_SECONDS)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid email or password"
        )
    login_rate_limiter.record_success(body.email)
    sub = str(user.id)
    return TokenPair(access_token=create_access(sub), refresh_token=create_refresh(sub))
```

- [ ] **Step 8: 통과 + 기존 회귀 확인**

Run: `python -m pytest apps/server/tests/test_login_rate_limit.py apps/server/tests/test_auth.py -q`
Expected: all passed (기존 test_auth.py에 연속 실패 5회+ 시나리오가 있어 깨지면 해당 테스트에 `login_rate_limiter.reset()` 픽스처를 추가)

- [ ] **Step 9: Commit**

```bash
git add apps/server/auth/login_rate_limit.py apps/server/api/v1/auth.py apps/server/tests/test_login_rate_limit.py
git commit -m "feat(auth): 계정 기준 로그인 rate-limit — 연속 실패 5회 시 5분 잠금(429)"
```

---

### Task 2: 세션 스코프 캡처 토큰 (저장소 + 발급 API + 세션 종료 시 폐기)

**Files:**
- Create: `apps/server/auth/capture_tokens.py`
- Modify: `apps/server/api/v1/sessions.py` (capture-token 엔드포인트 추가, end 엔드포인트에 revoke 1줄)
- Test: `apps/server/tests/test_capture_tokens.py` (신규)

**Interfaces:**
- Produces: `capture_tokens: CaptureTokenStore` 싱글턴 —
  `issue(session_uuid: UUID) -> tuple[str, datetime]`(평문 토큰, 만료시각; 세션당 활성 1개로 대체),
  `validate(token: str, session_uuid: UUID) -> bool`,
  `revoke_session(session_uuid: UUID) -> None`, `reset() -> None`.
  REST: `POST /api/v1/sessions/{external_id}/capture-token` (operator JWT) → `{"token": str, "expires_at": iso8601}`.
- Consumes: 없음 (Task 1과 독립)

- [ ] **Step 1: 저장소 실패 테스트 작성**

`apps/server/tests/test_capture_tokens.py`:

```python
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from apps.server.auth.capture_tokens import CaptureTokenStore


class FakeNow:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def test_issue_and_validate():
    now = FakeNow()
    store = CaptureTokenStore(ttl=timedelta(hours=12), now=now)
    sid = uuid4()
    token, expires_at = store.issue(sid)
    assert len(token) >= 32
    assert expires_at == now.now + timedelta(hours=12)
    assert store.validate(token, sid) is True


def test_validate_rejects_wrong_session_and_unknown_token():
    now = FakeNow()
    store = CaptureTokenStore(ttl=timedelta(hours=12), now=now)
    sid = uuid4()
    token, _ = store.issue(sid)
    assert store.validate(token, uuid4()) is False
    assert store.validate("nonsense", sid) is False


def test_reissue_replaces_previous_token():
    now = FakeNow()
    store = CaptureTokenStore(ttl=timedelta(hours=12), now=now)
    sid = uuid4()
    old, _ = store.issue(sid)
    new, _ = store.issue(sid)
    assert store.validate(old, sid) is False
    assert store.validate(new, sid) is True


def test_expiry():
    now = FakeNow()
    store = CaptureTokenStore(ttl=timedelta(hours=12), now=now)
    sid = uuid4()
    token, _ = store.issue(sid)
    now.now += timedelta(hours=12, seconds=1)
    assert store.validate(token, sid) is False


def test_revoke_session():
    now = FakeNow()
    store = CaptureTokenStore(ttl=timedelta(hours=12), now=now)
    sid = uuid4()
    token, _ = store.issue(sid)
    store.revoke_session(sid)
    assert store.validate(token, sid) is False
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest apps/server/tests/test_capture_tokens.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: 구현**

`apps/server/auth/capture_tokens.py`:

```python
# === ANCHOR: CAPTURE_TOKENS_START ===
"""세션 스코프 캡처 토큰(웹 캡처 /ws/capture 인증용) 인메모리 저장소.

영구 디바이스키를 터널에 노출하지 않기 위한 대체물: 회의(세션)마다 발급되고
세션 종료 시 즉시 폐기 + 안전상한 TTL. 평문은 응답으로만 나가고 서버에는
sha256 해시만 남는다. DB 무변경(단일 프로세스 인메모리); 서버 재시작 시
소실은 수용 — 회의도 끊기므로 클라가 JWT로 재발급받는다.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID

DEFAULT_TTL = timedelta(hours=12)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _Entry:
    token_hash: str
    expires_at: datetime


@dataclass
class CaptureTokenStore:
    ttl: timedelta = DEFAULT_TTL
    now: Callable[[], datetime] = _utcnow
    _by_session: dict[UUID, _Entry] = field(default_factory=dict)

    def issue(self, session_uuid: UUID) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(32)  # vibelign: allow-secret
        expires_at = self.now() + self.ttl
        self._by_session[session_uuid] = _Entry(_hash(token), expires_at)
        return token, expires_at

    def validate(self, token: str, session_uuid: UUID) -> bool:
        entry = self._by_session.get(session_uuid)
        if entry is None:
            return False
        if entry.expires_at <= self.now():
            del self._by_session[session_uuid]
            return False
        return secrets.compare_digest(entry.token_hash, _hash(token))

    def revoke_session(self, session_uuid: UUID) -> None:
        self._by_session.pop(session_uuid, None)

    def reset(self) -> None:
        self._by_session.clear()


capture_tokens = CaptureTokenStore()
# === ANCHOR: CAPTURE_TOKENS_END ===
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest apps/server/tests/test_capture_tokens.py -q`
Expected: 5 passed

- [ ] **Step 5: 발급 API + 종료 시 폐기 — 실패 테스트**

`apps/server/tests/test_capture_tokens.py`에 API 테스트 추가. 픽스처는 conftest.py의 `admin_token: str`(운영자 겸 admin JWT)·`client: AsyncClient` 사용, 마커 `@pytest.mark.asyncio`:

```python
import pytest
from httpx import AsyncClient

from apps.server.auth.capture_tokens import capture_tokens


@pytest.fixture(autouse=True)
def _reset_capture_tokens():
    capture_tokens.reset()
    yield
    capture_tokens.reset()


@pytest.mark.asyncio
async def test_capture_token_endpoint(admin_token: str, client: AsyncClient) -> None:
    headers = {"Authorization": f"Bearer {admin_token}"}
    created = await client.post("/api/v1/sessions", json={"title": "t"}, headers=headers)
    assert created.status_code == 201
    sid = created.json()["session_id"]

    r = await client.post(f"/api/v1/sessions/{sid}/capture-token", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["token"] and body["expires_at"]

    # 인증 없으면 401/403
    r2 = await client.post(f"/api/v1/sessions/{sid}/capture-token")
    assert r2.status_code in (401, 403)

    # 종료된 세션에는 발급 거부 + 기존 토큰 폐기
    ended = await client.post(f"/api/v1/sessions/{sid}/end", headers=headers)
    assert ended.status_code in (200, 204)
    r3 = await client.post(f"/api/v1/sessions/{sid}/capture-token", headers=headers)
    assert r3.status_code == 409
    from uuid import UUID

    assert capture_tokens.validate(body["token"], UUID(sid)) is False
```

- [ ] **Step 6: 실패 확인**

Run: `python -m pytest apps/server/tests/test_capture_tokens.py -q`
Expected: 새 테스트 FAIL (404 — 라우트 없음)

- [ ] **Step 7: sessions.py 수정**

`apps/server/api/v1/sessions.py`에 추가 — 기존 end 엔드포인트와 같은 파일. 기존 코드의 세션 조회·운영자 의존성 패턴(같은 파일의 end 핸들러가 쓰는 `require_operator`/세션 lookup 헬퍼)을 재사용:

```python
from apps.server.auth.capture_tokens import capture_tokens  # 상단 import에 추가


class CaptureTokenOut(BaseModel):
    token: str
    expires_at: datetime


@router.post("/{external_id}/capture-token", response_model=CaptureTokenOut)
async def issue_capture_token(
    external_id: UUID,
    _operator: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CaptureTokenOut:
    meeting = (
        await db.execute(select(Session).where(Session.external_id == external_id))
    ).scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    if meeting.status == "ended":
        raise HTTPException(status.HTTP_409_CONFLICT, "Session already ended")
    token, expires_at = capture_tokens.issue(external_id)
    return CaptureTokenOut(token=token, expires_at=expires_at)
```

그리고 기존 **end 엔드포인트** 성공 경로(세션 status를 "ended"로 만드는 지점 바로 다음)에 1줄:

```python
capture_tokens.revoke_session(external_id)
```

주의: sessions.py의 실제 import·의존성 이름(`require_operator` 등)과 end 핸들러의 파라미터명은 파일을 열어 기존 것에 맞춘다. 앵커 경계가 있으면 그 안에서만 수정.

- [ ] **Step 8: 통과 + 세션 스위트 회귀**

Run: `python -m pytest apps/server/tests/test_capture_tokens.py apps/server/tests -q -k "session or capture"`
Expected: all passed

- [ ] **Step 9: Commit**

```bash
git add apps/server/auth/capture_tokens.py apps/server/api/v1/sessions.py apps/server/tests/test_capture_tokens.py
git commit -m "feat(sessions): 세션 스코프 캡처 토큰 발급 API + 종료 시 즉시 폐기"
```

---

### Task 3: `/ws/capture` — 첫 메시지 인증 + 사이드카 스트림 루프 재사용

**Files:**
- Modify: `apps/server/ws/sidecar.py` (accept 후 스트림 루프를 `run_capture_stream()`으로 추출 — 동작 무변경 리팩터)
- Create: `apps/server/ws/capture.py`
- Modify: `apps/server/main.py` (capture 라우터 include — 기존 sidecar 라우터 include 지점 옆)
- Test: `apps/server/tests/test_ws_capture.py` (신규)

**Interfaces:**
- Consumes: Task 2의 `capture_tokens.validate(token, session_uuid)`
- Produces: WS `/ws/capture` — 쿼리 없음. 연결 후 5초 내 첫 텍스트 메시지
  `{"type": "auth", "token": "<capture token>", "session": "<external uuid>"}`.
  인증 실패/타임아웃/형식 오류 → `close(1008)`. 성공 → 텍스트 `{"type":"auth.ok"}` 응답 후
  기존 사이드카 오디오 계약(audio.started → PCM 바이너리 → chunk_meta → audio.stopped) 동일 동작.
- 추출 함수: `run_capture_stream(ws: WebSocket, session_pk: int, session_uuid: UUID, meeting_started_at: datetime) -> None` — **accept 이후** 본문(스테일 AI 세션 정리부터 finally까지)과 동일.

- [ ] **Step 1: 리팩터 — 스트림 루프 추출 (테스트는 기존 것이 지킴)**

`apps/server/ws/sidecar.py`의 `ws_sidecar`에서 `await ws.accept()` **다음 줄부터 함수 끝까지**(스테일 AI 정리 → while 루프 → except/finally)를 그대로 모듈 레벨 함수로 옮긴다:

```python
async def run_capture_stream(
    ws: WebSocket,
    session_pk: int,
    session_uuid: UUID,
    meeting_started_at: datetime,
) -> None:
    """인증·accept가 끝난 캡처 소켓의 공용 스트림 루프.

    /ws/sidecar(디바이스키)와 /ws/capture(세션 캡처토큰)가 동일 오디오 계약을
    공유한다 — 본문은 기존 ws_sidecar의 accept 이후와 문자 그대로 동일(이동만).
    """
    stale_ai_session = _active_ai_sessions.pop(session_uuid, None)
    ...  # 기존 316~473행 본문을 문자 그대로 이동 (수정 금지)
```

`ws_sidecar`는 인증(255~313행) + `await ws.accept()` + `await run_capture_stream(ws, session_pk, session_uuid, meeting_started_at)` 호출만 남긴다.

- [ ] **Step 2: 회귀 확인**

Run: `python -m pytest apps/server/tests/test_ws_sidecar_binary.py apps/server/tests/test_ws_flow.py -q`
Expected: all passed (동작 무변경)

- [ ] **Step 3: Commit (리팩터 단독)**

```bash
git add apps/server/ws/sidecar.py
git commit -m "refactor(ws): 사이드카 스트림 루프를 run_capture_stream()으로 추출 (동작 무변경)"
```

- [ ] **Step 4: /ws/capture 실패 테스트 작성**

`apps/server/tests/test_ws_capture.py` — `test_ws_flow.py`와 동일한 sync `TestClient` 패턴(같은 이벤트 루프에서 WS를 다루기 위한 이 리포의 표준 방식). test_ws_flow.py 상단의 헬퍼(`_sync_insert_admin`, `_make_frame`)와 import 블록을 이 파일에 복제한다(교차 import 하지 않는다 — 기존 테스트 파일 간 관례):

```python
"""/ws/capture 첫 메시지 인증 계약 테스트 (test_ws_flow.py의 sync TestClient 패턴)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from starlette.testclient import TestClient

from apps.server.auth.capture_tokens import capture_tokens
from apps.server.auth.password import hash_password
from apps.server.main import app

_SYNC_DSN = (
    "postgresql://yeson:6fad32ad29a12088da075219fdeb809d"  # vibelign: allow-secret — 기존 테스트 파일과 동일한 로컬 테스트 DB
    "@127.0.0.1:5432/yeson_meet_test"
)


@pytest.fixture(autouse=True)
def _reset_capture_tokens():
    capture_tokens.reset()
    yield
    capture_tokens.reset()


def _sync_insert_admin() -> tuple[int, str]:
    email = "capture-admin@test.example"
    pw_hash = hash_password("capture-admin-pw")
    with psycopg.connect(_SYNC_DSN) as conn:
        row = conn.execute(
            """
            INSERT INTO app_user (email, name, password_hash, role, is_active)
            VALUES (%s, %s, %s, 'admin', true)
            ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash
            RETURNING id
            """,
            [email, "Capture Admin", pw_hash],
        ).fetchone()
        conn.commit()
    return row[0], email


def _sync_count_utterances_by_external(external_id: str) -> int:
    with psycopg.connect(_SYNC_DSN) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM utterance u
            JOIN session s ON s.id = u.session_id
            WHERE s.external_id = %s::uuid
            """,
            [external_id],
        ).fetchone()
    return row[0]


def _make_frame(session_id: str, seq: int) -> str:
    now = datetime.now(timezone.utc).isoformat()
    return json.dumps(
        {
            "type": "utterance.transcribed",
            "session_id": session_id,
            "occurred_at": now,
            "seq": seq,
            "text_en": f"Hello seq {seq}",
            "text_ko": f"안녕 seq {seq}",
            "started_at": now,
            "ended_at": now,
            "is_final": True,
        }
    )


def _login_and_create_session(tc: TestClient) -> tuple[dict[str, str], str]:
    _admin_id, email = _sync_insert_admin()
    login = tc.post(
        "/api/v1/auth/login", json={"email": email, "password": "capture-admin-pw"}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    created = tc.post("/api/v1/sessions", json={"title": "capture-ws-test"}, headers=headers)
    assert created.status_code == 201
    return headers, created.json()["session_id"]


def _auth_msg(token: str, session_id: str) -> str:
    return json.dumps({"type": "auth", "token": token, "session": session_id})


def test_capture_ws_rejects_bad_token() -> None:
    with TestClient(app, raise_server_exceptions=True) as tc:
        _headers, sid = _login_and_create_session(tc)
        with pytest.raises(Exception):  # 서버가 close 1008 → 클라이언트 측 예외
            with tc.websocket_connect("/ws/capture") as ws:
                ws.send_text(_auth_msg("wrong-token", sid))
                ws.receive_text()  # auth.ok 대신 close → 예외


def test_capture_ws_rejects_non_auth_first_message() -> None:
    with TestClient(app, raise_server_exceptions=True) as tc:
        _headers, sid = _login_and_create_session(tc)
        token, _ = capture_tokens.issue(UUID(sid))
        assert token
        with pytest.raises(Exception):
            with tc.websocket_connect("/ws/capture") as ws:
                ws.send_text(json.dumps({"type": "audio.started", "sample_rate": 16000}))
                ws.receive_text()


def test_capture_ws_rejects_ended_session() -> None:
    with TestClient(app, raise_server_exceptions=True) as tc:
        headers, sid = _login_and_create_session(tc)
        token, _ = capture_tokens.issue(UUID(sid))
        tc.post(f"/api/v1/sessions/{sid}/end", headers=headers)
        # end가 capture_tokens.revoke_session을 호출하므로 validate부터 실패한다
        with pytest.raises(Exception):
            with tc.websocket_connect("/ws/capture") as ws:
                ws.send_text(_auth_msg(token, sid))
                ws.receive_text()


def test_capture_ws_happy_path_streams_utterance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    with TestClient(app, raise_server_exceptions=True) as tc:
        _headers, sid = _login_and_create_session(tc)
        token, _ = capture_tokens.issue(UUID(sid))
        with tc.websocket_connect("/ws/capture") as ws:
            ws.send_text(_auth_msg(token, sid))
            ack = json.loads(ws.receive_text())
            assert ack == {"type": "auth.ok"}
            # 기존 사이드카 계약 그대로: S1 fixture 텍스트 프레임이 DB에 도달
            ws.send_text(_make_frame(sid, 1))
            ws.send_text(_make_frame(sid, 2))
        assert _sync_count_utterances_by_external(sid) == 2
```

주의: WS close 시 TestClient가 던지는 구체 예외 타입(`WebSocketDisconnect` 등)은 test_ws_flow.py의 기존 거부 테스트가 어떻게 잡는지 확인해 `pytest.raises(Exception)`보다 좁게 맞춘다. happy path의 DB 반영 단언은 WS 종료(컨텍스트 탈출) 후 수행 — 프레임 처리 완료를 기다리기 위함이며, 그래도 경합하면 test_ws_flow.py가 쓰는 대기 방식을 복제한다.

- [ ] **Step 5: 실패 확인**

Run: `python -m pytest apps/server/tests/test_ws_capture.py -q`
Expected: FAIL (라우트 없음 — 연결 자체가 404/거부)

- [ ] **Step 6: 구현**

`apps/server/ws/capture.py`:

```python
# === ANCHOR: WS_CAPTURE_START ===
"""웹 캡처 전용 WS(/ws/capture) — 첫 메시지 인증(세션 캡처 토큰).

URL 쿼리에 아무것도 싣지 않는다(에지 로그·히스토리 잔존 방지). 인증 후에는
sidecar와 동일한 오디오 계약을 run_capture_stream()으로 재사용한다.
디바이스 바인딩은 없다 — 세션당 1개뿐인 캡처 토큰이 접근을 가둔다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from apps.server.auth.capture_tokens import capture_tokens
from apps.server.db.models import Session
from apps.server.db.session import AsyncSessionLocal
from apps.server.ops.session_safety import enforce_meeting_duration_limit
from apps.server.ws.sidecar import run_capture_stream

router = APIRouter()
logger = logging.getLogger(__name__)

AUTH_TIMEOUT_SECONDS = 5.0


@router.websocket("/ws/capture")
async def ws_capture(ws: WebSocket) -> None:
    # 첫 메시지 인증을 받으려면 accept가 선행되어야 한다(쿼리 인증인 sidecar와
    # 달리 handshake 403이 불가능한 구조 — 실패는 close 1008로만 표현).
    await ws.accept()
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        msg = json.loads(raw)
        session_uuid = UUID(str(msg["session"]))
        token = str(msg["token"])
        if msg.get("type") != "auth":
            raise ValueError("not an auth message")
    except (ValueError, KeyError, TypeError):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    if not capture_tokens.validate(token, session_uuid):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with AsyncSessionLocal() as db:
        meeting = (
            await db.execute(select(Session).where(Session.external_id == session_uuid))
        ).scalar_one_or_none()
        if meeting is None or meeting.status == "ended":
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        if meeting.disconnected_at is not None:
            meeting.disconnected_at = None
            await db.commit()
        if await enforce_meeting_duration_limit(db, meeting):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        session_pk = meeting.id
        meeting_started_at = meeting.started_at

    await ws.send_text(json.dumps({"type": "auth.ok"}))
    logger.info("Capture websocket authenticated", extra={"session_id": str(session_uuid)})
    await run_capture_stream(ws, session_pk, session_uuid, meeting_started_at)
# === ANCHOR: WS_CAPTURE_END ===
```

`apps/server/main.py`: 기존 sidecar 라우터 include 지점을 찾아(grep `sidecar`) 바로 옆에 capture 라우터 include 1줄 추가 (기존 include 스타일 그대로).

- [ ] **Step 7: 통과 + 서버 전체 회귀**

Run: `python -m pytest apps/server/tests -q`
Expected: all passed (기지 무관 1실패 test_viewer_spa_mount는 main에도 존재 — 그 외 전부 통과)

- [ ] **Step 8: Commit**

```bash
git add apps/server/ws/capture.py apps/server/main.py apps/server/tests/test_ws_capture.py
git commit -m "feat(ws): /ws/capture — 캡처 토큰 첫 메시지 인증으로 사이드카 오디오 계약 개방"
```

---

### Task 4: 웹 캡처 클라이언트 전환 (self-enroll 제거 · 캡처 토큰 · 미리보기 폴링)

**Files:**
- Modify: `apps/web/src/capture/captureApi.ts`, `apps/web/src/capture/audioWsClient.ts`, `apps/web/src/capture/useCaptureSession.ts`, `apps/web/src/capture/useOperatorSubtitles.ts`
- Test: `apps/web/src/capture/captureApi.test.ts`, `apps/web/src/capture/audioWsClient.test.ts` (기존 수정 + 추가)

**Interfaces:**
- Consumes: Task 2 REST(`POST /api/v1/sessions/{id}/capture-token` → `{token, expires_at}`), Task 3 WS 계약(`auth` 첫 메시지 → `auth.ok`).
- Produces (다른 태스크가 의존하지는 않음 — 내부 인터페이스):
  - `fetchCaptureToken(operatorToken: string, sessionId: string): Promise<string>`
  - `captureWsUrl(loc?: WsLocation): string` — 쿼리 없는 `/ws/capture`
  - `AudioWsClient(url, auth: { token: string; session: string }, onStatus, wsFactory?)` — onopen 시 auth 전송 → `auth.ok` 수신 후 `audio.started` 전송·`streaming` 전이

- [ ] **Step 1: captureApi 실패 테스트**

`captureApi.test.ts`에서 self-enroll/sidecarWsUrl/credentialStore/operatorWsUrl 테스트를 삭제하고 다음을 추가 (기존 테스트 파일의 fetch mock 패턴 유지):

```typescript
it("captureWsUrl은 쿼리 없이 /ws/capture를 가리킨다", () => {
  const url = new URL(captureWsUrl({ protocol: "https:", host: "example.com" }));
  expect(url.pathname).toBe("/ws/capture");
  expect(url.search).toBe("");
  expect(url.protocol).toBe("wss:");
});

it("fetchCaptureToken은 세션 캡처 토큰을 발급받는다", async () => {
  // fetch mock: POST /api/v1/sessions/abc/capture-token → { token: "T", expires_at: "..." }
  // Authorization: Bearer 헤더 포함 검증
  const token = await fetchCaptureToken("JWT", "abc");
  expect(token).toBe("T");
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/web && pnpm test -- --run src/capture/captureApi.test.ts`
Expected: FAIL (함수 없음)

- [ ] **Step 3: captureApi.ts 수정**

- 삭제: `sidecarWsUrl`, `operatorWsUrl`, `selfEnrollDevice`, `credentialStore`, `DEVICE_KEY_STORAGE`
- 추가:

```typescript
export function captureWsUrl(loc: WsLocation = window.location): string {
  return `${wsBase(loc)}/ws/capture`;
}

export async function fetchCaptureToken(operatorToken: string, sessionId: string): Promise<string> {
  const response = await fetch(
    `${API_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}/capture-token`,
    { method: "POST", headers: authHeaders(operatorToken) },
  );
  const body = await parseJson<{ token: string; expires_at: string }>(response, "Issue capture token");
  return body.token;
}
```

- [ ] **Step 4: audioWsClient 실패 테스트**

`audioWsClient.test.ts`의 기존 FakeWebSocket 패턴에 auth 핸드셰이크 검증 추가:

```typescript
it("open 시 auth를 먼저 보내고 auth.ok 수신 후에만 audio.started·streaming", () => {
  // client = new AudioWsClient(url, { token: "T", session: "S" }, onStatus, fakeFactory)
  // fake open → 첫 send가 {"type":"auth","token":"T","session":"S"}
  // 이 시점 status는 아직 streaming 아님, sendChunk는 드롭
  // fake가 {"type":"auth.ok"} 메시지 전달 → audio.started 전송 + streaming 전이
});

it("auth.ok 전에 서버가 닫으면(잘못된 토큰) 기존 reject 경로를 탄다", () => {
  // open → (auth 전송) → close: hadOpened=true & <2s → 3연속이면 rejected
});
```

주의: FakeWebSocket에 `onmessage` 핸들러 지원이 없으면 `WebSocketLike` 타입에 `onmessage: ((e: { data: string }) => void) | null`을 추가하고 fake도 확장한다.

- [ ] **Step 5: 실패 확인**

Run: `cd apps/web && pnpm test -- --run src/capture/audioWsClient.test.ts`
Expected: FAIL

- [ ] **Step 6: audioWsClient.ts 수정**

- `WebSocketLike`에 `onmessage: ((e: { data: string }) => void) | null;` 추가 (wsFactory 기본 구현 캐스팅은 그대로 동작).
- 생성자: `constructor(url, private readonly auth: { token: string; session: string }, onStatus, wsFactory?)`
- `connect()`의 `ws.onopen`을 다음으로 교체:

```typescript
ws.onopen = () => {
  this.openedAt = Date.now();
  this.consecutiveOpenlessCloses = 0;
  ws.send(JSON.stringify({ type: "auth", token: this.auth.token, session: this.auth.session }));
  // auth.ok가 오기 전에는 streaming으로 전이하지 않는다(sendChunk 드롭 유지).
};
ws.onmessage = (e) => {
  try {
    const msg = JSON.parse(e.data) as { type?: string };
    if (msg.type === "auth.ok" && this.status !== "streaming") {
      ws.send(
        JSON.stringify({
          type: "audio.started",
          sample_rate: 16000,
          channels: 1,
          format: "pcm_s16le",
          started_at: new Date().toISOString(),
        }),
      );
      this.setStatus("streaming");
    }
  } catch {}
};
```

`onclose` 로직은 무변경 — auth 거부(accept 후 즉시 close)는 기존 "open 후 2s 내 닫힘" reject 경로가 그대로 잡는다.

- [ ] **Step 7: useCaptureSession·useOperatorSubtitles 수정**

`useCaptureSession.ts`:
- import에서 `credentialStore`/`selfEnrollDevice`/`sidecarWsUrl` 제거, `captureWsUrl`/`fetchCaptureToken` 추가
- `store`(useMemo)·`ensureDeviceKey` 삭제
- `startCapture`의 클라이언트 생성부 교체:

```typescript
const captureToken = await fetchCaptureToken(state.operatorToken, state.sessionId);
const client = new AudioWsClient(
  captureWsUrl(),
  { token: captureToken, session: state.sessionId },
  (wsStatus) => {
    patch({ wsStatus });
    if (wsStatus === "rejected") {
      patch({ error: "서버가 연결을 거부했습니다. 세션이 종료됐거나 캡처 토큰이 만료됐을 수 있습니다. 회의를 종료하고 새 회의를 시작하세요." });
    } else if (wsStatus === "unreachable") {
      patch({ error: "서버에 연결할 수 없습니다. 서버가 실행 중인지, 주소가 맞는지 확인하세요. 문제가 계속되면 회의를 종료하고 새 회의로 다시 시작하세요." });
    }
  },
);
```

(디바이스키 clearDeviceKey 로직·주석 삭제 — deps 배열에서 store/ensureDeviceKey 제거.)

`useOperatorSubtitles.ts`: WS 연결(`connect()`/`operatorWsUrl`) 전체를 2.5초 폴링으로 교체:

```typescript
// === ANCHOR: USE_OPERATOR_SUBTITLES_START ===
// 진행자 자막 미리보기 — REST 폴링(2.5s). 이전 /ws/operator는 JWT가 URL 쿼리에
// 실려 터널 노출에 부적합해 제거했다(참석자 뷰어 WS는 무변경·실시간).
import { useEffect, useRef, useState } from "react";
import type { UtteranceTranscribed } from "../types/events";
import { latestUtterance, upsertUtterance } from "../lib/utterances";
import { fetchOperatorBackfill } from "./captureApi";

const POLL_INTERVAL_MS = 2500;

export type OperatorSubtitles = {
  utterances: UtteranceTranscribed[];
  latest: UtteranceTranscribed | null;
  connected: boolean;
};

export function useOperatorSubtitles(sessionId: string | null, operatorToken: string | null): OperatorSubtitles {
  const [state, setState] = useState<OperatorSubtitles>({ utterances: [], latest: null, connected: false });
  const endedRef = useRef(false);

  useEffect(() => {
    if (!sessionId || !operatorToken) {
      setState({ utterances: [], latest: null, connected: false });
      return;
    }
    endedRef.current = false;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      if (!active || endedRef.current) return;
      try {
        const backfill = await fetchOperatorBackfill(operatorToken!, sessionId!);
        if (!active) return;
        const sorted = [...backfill.utterances]
          .sort((a, b) => a.seq - b.seq)
          .reduce<UtteranceTranscribed[]>(upsertUtterance, []);
        setState({ utterances: sorted, latest: latestUtterance(sorted), connected: true });
        if (backfill.session_status === "ended") {
          endedRef.current = true;
          setState((s) => ({ ...s, connected: false }));
          return;
        }
      } catch {
        if (active) setState((s) => ({ ...s, connected: false }));
      }
      timer = setTimeout(poll, POLL_INTERVAL_MS);
    }

    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [sessionId, operatorToken]);

  return state;
}
// === ANCHOR: USE_OPERATOR_SUBTITLES_END ===
```

- [ ] **Step 8: 웹 전체 테스트 + 타입 체크**

Run: `cd apps/web && pnpm test -- --run && pnpm build`
Expected: all passed, build 성공 (남은 `credentialStore` 등 참조가 있으면 컴파일 에러로 드러남 — 모두 제거)

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/capture/
git commit -m "feat(web-capture): 영구 디바이스키 → 세션 캡처 토큰 전환, /ws/capture 인증, 미리보기 REST 폴링화"
```

---

### Task 5: 터널 프록시 — 메서드 인지형 허용리스트 확장

**Files:**
- Modify: `apps/server_desktop/src-tauri/src/tunnel_proxy.rs` (`ANCHOR: TUNNEL_PROXY_DECIDE` 구간 + `proxy_request` 호출부 + 테스트 앵커 구간)

**Interfaces:**
- Produces: `decide(method: &str, normalized_path: &str) -> PathDecision`,
  `viewer_allows(method: &str, raw_path: &str) -> PathDecision` (기존 호출부 `proxy_request`가 `req.method().as_str()` 전달)

- [ ] **Step 1: 실패하는 테스트 추가**

`TUNNEL_PROXY_TESTS` 앵커 안 — 기존 `allows(raw)` 헬퍼를 메서드 인자 버전으로 바꾸고(기존 호출은 `allows("GET", ...)`로 일괄 수정), 신규 테스트 추가:

```rust
fn allows(method: &str, raw: &str) -> bool {
    viewer_allows(method, raw) == PathDecision::Allow
}

#[test]
fn allows_capture_surface() {
    assert!(allows("GET", "/capture"), "capture SPA route");
    assert!(allows("POST", "/api/v1/auth/login"), "operator login");
    assert!(allows("POST", "/api/v1/sessions"), "create session");
    assert!(allows("POST", "/api/v1/sessions/abc-123/end"), "end session");
    assert!(allows("GET", "/api/v1/sessions/abc-123/utterances"), "preview polling");
    assert!(allows("POST", "/api/v1/sessions/abc-123/capture-token"), "capture token");
    assert!(allows("GET", "/ws/capture"), "capture websocket (upgrade is GET)");
}

#[test]
fn denies_capture_adjacent_surface() {
    // 메서드 불일치
    assert!(!allows("GET", "/api/v1/auth/login"));
    assert!(!allows("GET", "/api/v1/sessions"), "회의기록 목록은 계속 차단");
    assert!(!allows("POST", "/api/v1/sessions/abc/utterances"));
    // 세션 상세·타 REST
    assert!(!allows("GET", "/api/v1/sessions/abc-123"), "세션 상세 차단");
    assert!(!allows("POST", "/api/v1/devices/self-enroll"), "영구키 발급 창구 차단");
    assert!(!allows("GET", "/ws/sidecar"), "영구키 WS 계속 차단");
    assert!(!allows("GET", "/ws/operator"), "operator WS 계속 차단");
    // <id> 와일드카드 경계
    assert!(!allows("POST", "/api/v1/sessions//end"), "빈 세그먼트 불가");
    assert!(!allows("POST", "/api/v1/sessions/a/b/end"), "중첩 세그먼트 불가");
    assert!(!allows("GET", "/api/v1/sessions/abc/utterances/extra"), "뒤 추가 세그먼트 불가");
    assert!(!allows("GET", "/capture/anything"), "capture는 정확 일치만");
}

#[test]
fn capture_surface_defeats_smuggling() {
    // ".."가 <id> 자리로 들어오면 정규화가 세그먼트를 pop해 다른 경로가 된다 → deny
    assert_eq!(normalize_path("/api/v1/sessions/%2e%2e/end"), "/api/v1/end");
    assert!(!allows("POST", "/api/v1/sessions/%2e%2e/end"));
    // 대소문자는 정규화(소문자화)로 흡수된다
    assert!(allows("POST", "/API/V1/AUTH/LOGIN"));
    assert!(allows("GET", "/CAPTURE"));
    // 인코딩 슬래시 스머글링
    assert!(!allows("GET", "/ws%2fcapture/../sidecar"));
    assert!(!allows("GET", "/v/../ws/sidecar"));
}
```

기존 테스트의 `allows("/...")` 호출은 전부 `allows("GET", "/...")`로 수정하되, 기존 ALLOW/DENY 판정은 하나도 바뀌지 않아야 한다.

- [ ] **Step 2: 실패 확인**

Run: `cargo test --manifest-path apps/server_desktop/src-tauri/Cargo.toml tunnel_proxy`
Expected: 컴파일 에러(시그니처 불일치) — 시그니처 변경 후 신규 테스트 FAIL

- [ ] **Step 3: 구현**

`TUNNEL_PROXY_DECIDE` 앵커 안의 `decide`/`viewer_allows` 교체:

```rust
/// 정규화된 경로 + HTTP 메서드에 대한 허용 판정. deny-by-default 유지.
/// 뷰어 표면(기존)은 전 메서드 허용을 GET/HEAD 중심으로 쓰던 기존 동작을 보존하기
/// 위해 메서드 무관 허용을 유지하고, 신규 캡처 표면만 메서드를 못박는다.
pub fn decide(method: &str, normalized_path: &str) -> PathDecision {
    let p = normalized_path;
    let m = method.to_ascii_uppercase();

    // --- 기존 뷰어 표면 (메서드 무관 — 기존 동작 보존) ---
    if p == "/" || p == "/index.html" || p == "/v" || p == "/ws/viewer" {
        return PathDecision::Allow;
    }
    if p.starts_with("/v/") || p.starts_with("/assets/") || p.starts_with("/favicon") {
        return PathDecision::Allow;
    }
    if p.starts_with("/api/v1/viewer/") {
        return PathDecision::Allow;
    }

    // --- 캡처 표면 (메서드 못박음) ---
    if p == "/capture" && m == "GET" {
        return PathDecision::Allow;
    }
    if p == "/api/v1/auth/login" && m == "POST" {
        return PathDecision::Allow;
    }
    if p == "/api/v1/sessions" && m == "POST" {
        return PathDecision::Allow;
    }
    if p == "/ws/capture" && m == "GET" {
        return PathDecision::Allow;
    }
    // /api/v1/sessions/<id>/{end|capture-token|utterances} — <id>는 단일 비어있지 않은 세그먼트
    if let Some(rest) = p.strip_prefix("/api/v1/sessions/") {
        let mut parts = rest.split('/');
        let (id, tail, extra) = (parts.next(), parts.next(), parts.next());
        if extra.is_none() {
            if let (Some(id), Some(tail)) = (id, tail) {
                if !id.is_empty() {
                    match (tail, m.as_str()) {
                        ("end", "POST") | ("capture-token", "POST") | ("utterances", "GET") => {
                            return PathDecision::Allow;
                        }
                        _ => {}
                    }
                }
            }
        }
    }

    PathDecision::Deny
}

pub fn viewer_allows(method: &str, raw_path: &str) -> PathDecision {
    decide(method, &normalize_path(raw_path))
}
```

`proxy_request`의 판정 호출을 교체:

```rust
let method = req.method().as_str().to_string();
if viewer_allows(&method, &raw_path) == PathDecision::Deny {
    return Ok(not_found());
}
```

- [ ] **Step 4: 통과 확인**

Run: `cargo test --manifest-path apps/server_desktop/src-tauri/Cargo.toml tunnel_proxy`
Expected: all passed (기존+신규)

- [ ] **Step 5: Commit**

```bash
git add apps/server_desktop/src-tauri/src/tunnel_proxy.rs
git commit -m "feat(tunnel): 캡처 표면 메서드 인지형 허용 — /capture·login·sessions 3종·/ws/capture (deny-by-default 유지)"
```

---

### Task 6: UX 정리 + 가이드 문서

**Files:**
- Modify: `apps/web/src/capture/CaptureView.tsx` (캡처 시작 버튼 support 게이팅)
- Modify: `docs/web-capture-operator-guide.md` (원격 사용법 갱신)
- Test: 기존 vitest 스위트 통과 확인 (CaptureView 전용 테스트 없음 — 게이팅은 captureSupport 단위 테스트가 커버)

**Interfaces:**
- Consumes: `checkCaptureSupport()` (기존, `captureSupport.ts`)

- [ ] **Step 1: 버튼 게이팅**

`CaptureView.tsx`의 "탭 선택하고 캡처 시작" 버튼(현재 `disabled={s.busy}`)을 support 게이팅으로 교체. 상단에서 `const support = checkCaptureSupport();`는 이미 186행에 있으므로 해당 값이 ready 패널까지 내려오도록 같은 컴포넌트인지 확인 후(아니면 버튼 컴포넌트에서 재호출 — 순수 함수라 무해):

```tsx
<button
  className="w-full rounded bg-emerald-600 py-2 font-semibold hover:bg-emerald-500 disabled:opacity-50"
  disabled={s.busy || !support.ok}
  title={support.ok ? undefined : "이 주소/브라우저에서는 탭 캡처를 쓸 수 없습니다 — 상단 안내를 확인하세요"}
  onClick={() => void s.startCapture()}
>
  {s.busy ? "준비 중…" : support.ok ? "탭 선택하고 캡처 시작" : "탭 캡처 불가 (상단 안내 참조)"}
</button>
```

이로써 비보안 컨텍스트에서 원시 JS 에러(`Cannot read properties of undefined`)가 뜰 경로가 사라진다.

- [ ] **Step 2: 가이드 문서 갱신**

`docs/web-capture-operator-guide.md` 전면 개정:
- 헤드 노트: "현재 지원 범위(v1.2.0): localhost 전용" 블록을 "지원 범위: **서버 컴퓨터 localhost + Go Live 터널(https) 원격**"으로 교체. 원격 전제 2가지 명시 — ① 서버 콘솔 공개(Go Live) 상태여야 함(자동 공개 권장) ② 퀵터널 URL은 서버 재시작마다 바뀌므로 최신 주소 필요.
- 사용법에 "다른 컴퓨터에서(원격)" 절 추가: `https://<터널주소>/capture` 접속 → 로그인 → 이하 동일.
- 제약에서 "지금은 서버 컴퓨터의 localhost에서만" 항목 제거, "LAN `http://<IP>`는 여전히 불가(브라우저 보안 컨텍스트) — localhost 또는 터널 https 사용" 유지.
- 릴리스 체크리스트 항목의 이중 클라이언트 검증 문구는 유지.

- [ ] **Step 3: 확인 + Commit**

Run: `cd apps/web && pnpm test -- --run && pnpm build`
Expected: all passed

```bash
git add apps/web/src/capture/CaptureView.tsx docs/web-capture-operator-guide.md
git commit -m "fix(web-capture): 미지원 환경 캡처 버튼 비활성화 + 가이드 원격 지원 갱신"
```

---

### Task 7: 통합 검증 (재동결 → 회귀 → E2E)

**Files:** 없음 (검증 전용)

- [ ] **Step 1: 전체 테스트 3종**

```bash
python -m pytest apps/server/tests -q
cd apps/web && pnpm test -- --run && pnpm build && cd ../..
cargo test --manifest-path apps/server_desktop/src-tauri/Cargo.toml
```

Expected: 서버 pytest(기지 test_viewer_spa_mount 1건 제외) · vitest · cargo 전부 통과

- [ ] **Step 2: 재동결 + dev 사본 갱신**

```bash
bash scripts/build-server.sh   # (실제 경로/이름은 리포 루트 스크립트 확인)
```

주의: tauri:dev 실행 중이면 먼저 종료. tauri:dev의 target/debug binaries 사본은 Rust 미변경 시 재복사되지 않지만, 이번엔 Rust(tunnel_proxy)도 변경되므로 재빌드에 포함된다.

- [ ] **Step 3: localhost 회귀 (수동)**

서버 콘솔 실행 → 같은 컴퓨터 Chrome에서 `http://localhost:8000/capture` → 로그인 → 회의 생성 → 탭 캡처 → 자막 미리보기(폴링) 갱신 확인 → 종료. (기존 흐름이 새 토큰 경로로 그대로 동작하는지)

- [ ] **Step 4: 터널 E2E (수동, 다른 컴퓨터)**

Go Live(공개) → 다른 컴퓨터(Windows Chrome 권장) `https://<터널>/capture`:
1. 로그인 → 회의 생성 → 캡처 → 뷰어(제3의 기기)에서 자막 수신
2. 차단 확인 — 전부 404여야 한다:
   - `https://<터널>/api/v1/devices/self-enroll` (POST)
   - `https://<터널>/api/v1/sessions` (GET)
   - `https://<터널>/ws/sidecar`, `https://<터널>/ws/operator`
3. 로그인 오답 6회 → 429 확인(5분 후 해제)

- [ ] **Step 5: 결과 기록 + Commit (필요 시 수정 커밋)**

E2E 결과를 `.superpowers/sdd/progress.md`(있으면) 또는 PR 본문에 기록.
