"""Auth endpoint tests."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from apps.server.auth.jwt import decode_token
from apps.server.auth.login_rate_limit import login_rate_limiter
from apps.server.db.models import AppUser


@pytest.fixture(autouse=True)
def _reset_login_rate_limiter() -> None:
    """전역 login_rate_limiter를 테스트마다 리셋 — 테스트 순서 의존 제거."""
    login_rate_limiter.reset()
    yield
    login_rate_limiter.reset()


@pytest.mark.asyncio
async def test_login_happy(admin_user: AppUser, client: AsyncClient) -> None:
    """POST /api/v1/auth/login with correct credentials returns a valid token pair."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "test-admin-pw"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["refresh_token"]

    payload = decode_token(data["access_token"])
    assert payload["sub"] == str(admin_user.id)
    assert payload["kind"] == "access"


@pytest.mark.asyncio
async def test_login_wrong_password(admin_user: AppUser, client: AsyncClient) -> None:
    """POST /api/v1/auth/login with wrong password returns 401."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "wrong-password"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_email(client: AsyncClient) -> None:
    """POST /api/v1/auth/login with unknown email returns 401."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "whatever"},
    )
    assert resp.status_code == 401
