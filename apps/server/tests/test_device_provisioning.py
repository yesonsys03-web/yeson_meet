"""Device-key provisioning tests (S1 harden + S3 list/revoke).

Covers PAIR.1'/PAIR.2'/PAIR.3-server/PAIR.4 from
.omc/plans/device-key-provisioning.md §5.
"""
from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.password import hash_password
from apps.server.db.models import AppUser, Device


# ── T-UNIT-MINT (PAIR.2') ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mint_returns_plaintext_once_persists_only_hash(
    admin_token: str, client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(
        "/api/v1/devices",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "sidecar-1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    plaintext = body["api_key"]
    assert plaintext

    row = (
        await db_session.execute(select(Device).where(Device.id == body["id"]))
    ).scalar_one()
    assert row.api_key_hash != plaintext
    # Stored value is the hash of the plaintext, never the plaintext itself.
    assert "api_key" not in {c.name for c in Device.__table__.columns}


# ── T-UNIT-NOLOG / T-OBS-NOLEAK-SERVER (PAIR.3-server) ────────────────────────
@pytest.mark.asyncio
async def test_mint_does_not_log_plaintext(
    admin_token: str, client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        resp = await client.post(
            "/api/v1/devices",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "sidecar-nolog"},
        )
    assert resp.status_code == 201, resp.text
    plaintext = resp.json()["api_key"]
    assert plaintext not in caplog.text


# ── T-UNIT-LISTSHAPE (PAIR.2') ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_devices_omits_hash(
    admin_token: str, client: AsyncClient
) -> None:
    await client.post(
        "/api/v1/devices",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "sidecar-list"},
    )
    resp = await client.get(
        "/api/v1/devices", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    assert set(items[0].keys()) == {"id", "name", "is_active", "created_at"}
    assert "api_key_hash" not in items[0]
    assert "api_key" not in items[0]


# ── T-INT-AUTH-401 (PAIR.1') ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mint_requires_bearer(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/devices", json={"name": "nope"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_mint_rejects_non_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    operator = AppUser(
        email="operator@test.example",
        name="Test Operator",
        password_hash=hash_password("test-operator-pw"),
        role="operator",
        is_active=True,
    )
    db_session.add(operator)
    await db_session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": operator.email, "password": "test-operator-pw"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    resp = await client.post(
        "/api/v1/devices",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "nope"},
    )
    assert resp.status_code == 403


# ── T-INT-MINT-OK (PAIR.1' + P1 setup) ────────────────────────────────────────
@pytest.mark.asyncio
async def test_mint_with_admin_bearer_ok(
    admin_token: str, client: AsyncClient
) -> None:
    resp = await client.post(
        "/api/v1/devices",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "sidecar-ok"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "sidecar-ok"
    assert body["api_key"]


# ── T-INT-REVOKE-DBSTATE (PAIR.4 state) ───────────────────────────────────────
@pytest.mark.asyncio
async def test_revoke_sets_inactive_and_reflected_in_list(
    admin_token: str, client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = {"Authorization": f"Bearer {admin_token}"}
    mint = await client.post(
        "/api/v1/devices", headers=headers, json={"name": "sidecar-revoke"}
    )
    device_id = mint.json()["id"]

    revoke = await client.post(
        f"/api/v1/devices/{device_id}/revoke", headers=headers
    )
    assert revoke.status_code == 204, revoke.text

    row = (
        await db_session.execute(select(Device).where(Device.id == device_id))
    ).scalar_one()
    assert row.is_active is False

    listing = await client.get("/api/v1/devices", headers=headers)
    entry = next(d for d in listing.json() if d["id"] == device_id)
    assert entry["is_active"] is False


@pytest.mark.asyncio
async def test_revoke_unknown_device_404(
    admin_token: str, client: AsyncClient
) -> None:
    resp = await client.post(
        "/api/v1/devices/999999/revoke",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_and_revoke_require_admin(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/devices")).status_code == 401
    assert (await client.post("/api/v1/devices/1/revoke")).status_code == 401


# ── T-INT-SELF-ENROLL-OK (client zero-config onboarding P0) ──────────────────────
@pytest.mark.asyncio
async def test_self_enroll_with_operator_ok(client: AsyncClient, db_session: AsyncSession) -> None:
    operator = AppUser(
        email="op-enroll@test.example",
        name="Op Enroll",
        password_hash=hash_password("op-enroll-pw"),
        role="operator",
        is_active=True,
    )
    db_session.add(operator)
    await db_session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": operator.email, "password": "op-enroll-pw"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    resp = await client.post(
        "/api/v1/devices/self-enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "client-macpro"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "client-macpro"
    assert body["api_key"]


@pytest.mark.asyncio
async def test_self_enroll_requires_bearer(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/devices/self-enroll", json={"name": "nope"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_self_enroll_rejects_non_privileged(client: AsyncClient, db_session: AsyncSession) -> None:
    viewer = AppUser(
        email="viewer-enroll@test.example",
        name="Viewer",
        password_hash=hash_password("viewer-pw"),
        role="viewer",
        is_active=True,
    )
    db_session.add(viewer)
    await db_session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": viewer.email, "password": "viewer-pw"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    resp = await client.post(
        "/api/v1/devices/self-enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "nope"},
    )
    assert resp.status_code == 403


# ── T-INT-SELF-ENROLL-DEDUP (one live key per named client) ──────────────────
@pytest.mark.asyncio
async def test_self_enroll_dedup_deactivates_prior_same_name(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Re-enrolling with the same name must deactivate the prior active row."""
    # Create an operator to self-enroll with.
    operator = AppUser(
        email="op-dedup@test.example",
        name="Op Dedup",
        password_hash=hash_password("op-dedup-pw"),
        role="operator",
        is_active=True,
    )
    db_session.add(operator)
    await db_session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": operator.email, "password": "op-dedup-pw"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Enroll a different name first — it must remain untouched.
    resp_b = await client.post(
        "/api/v1/devices/self-enroll",
        headers=headers,
        json={"name": "client-host-b"},
    )
    assert resp_b.status_code == 201, resp_b.text
    id_b = resp_b.json()["id"]

    # First enrollment of client-host-a.
    resp1 = await client.post(
        "/api/v1/devices/self-enroll",
        headers=headers,
        json={"name": "client-host-a"},
    )
    assert resp1.status_code == 201, resp1.text
    id_a1 = resp1.json()["id"]

    # Second enrollment of client-host-a (re-enroll / key rotation).
    resp2 = await client.post(
        "/api/v1/devices/self-enroll",
        headers=headers,
        json={"name": "client-host-a"},
    )
    assert resp2.status_code == 201, resp2.text
    id_a2 = resp2.json()["id"]

    # Refresh the session so we see committed state.
    db_session.expire_all()

    # Fetch all Device rows with name == "client-host-a".
    rows_a = (
        await db_session.execute(
            select(Device).where(Device.name == "client-host-a")
        )
    ).scalars().all()

    assert len(rows_a) == 2, f"Expected 2 rows for client-host-a, got {len(rows_a)}"

    active_a = [r for r in rows_a if r.is_active]
    inactive_a = [r for r in rows_a if not r.is_active]

    assert len(active_a) == 1, (
        f"Expected exactly 1 active row for client-host-a, got {len(active_a)}"
    )
    assert len(inactive_a) == 1, (
        f"Expected exactly 1 inactive row for client-host-a, got {len(inactive_a)}"
    )
    # The newer row must be the live one; the older one deactivated.
    assert active_a[0].id == id_a2
    assert inactive_a[0].id == id_a1

    # client-host-b must still be active (unrelated name).
    row_b = (
        await db_session.execute(select(Device).where(Device.id == id_b))
    ).scalar_one()
    assert row_b.is_active is True, "client-host-b must remain active"
