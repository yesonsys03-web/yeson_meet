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
