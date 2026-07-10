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
