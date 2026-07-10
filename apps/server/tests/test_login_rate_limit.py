"""계정 기준 로그인 rate-limit 단위 테스트 (주입 가능한 clock 사용)."""
import pytest
from httpx import AsyncClient

from apps.server.auth.login_rate_limit import LoginRateLimiter, login_rate_limiter
from apps.server.db.models import AppUser


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


def test_max_entries_evicts_unlocked_entry_and_keeps_size():
    clock = FakeClock()
    rl = LoginRateLimiter(max_fails=5, lockout_seconds=300.0, max_entries=3, clock=clock)
    rl.record_failure("a@x.c")
    rl.record_failure("b@x.c")
    rl.record_failure("c@x.c")
    assert len(rl._entries) == 3

    # 4번째 신규 키 삽입 시 상한 초과 대신 잠기지 않은 엔트리 하나를 제거
    rl.record_failure("d@x.c")
    assert len(rl._entries) == 3
    assert "d@x.c" in rl._entries


def test_max_entries_preserves_locked_entries():
    clock = FakeClock()
    rl = LoginRateLimiter(max_fails=2, lockout_seconds=300.0, max_entries=2, clock=clock)
    for _ in range(2):
        rl.record_failure("locked@x.c")  # max_fails=2 → 잠김
    assert rl.check("locked@x.c") is not None
    rl.record_failure("b@x.c")  # 1회 실패 — 잠기지 않음
    assert len(rl._entries) == 2

    # 상한 초과 삽입 — 잠기지 않은 b@x.c만 제거 대상, locked@x.c는 보존
    rl.record_failure("c@x.c")
    assert len(rl._entries) == 2
    assert rl.check("locked@x.c") is not None
    assert "locked@x.c" in rl._entries
    assert "c@x.c" in rl._entries
    assert "b@x.c" not in rl._entries


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


@pytest.mark.asyncio
async def test_login_rejects_overlong_email(client: AsyncClient) -> None:
    """터널 노출 로그인의 무한 메모리 성장 방지 — 254자 초과 이메일은 422로 거부."""
    overlong_email = "a" * 255
    r = await client.post(
        "/api/v1/auth/login", json={"email": overlong_email, "password": "whatever"}
    )
    assert r.status_code == 422
