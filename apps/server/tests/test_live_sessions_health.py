"""P4.1b: read-only live-meeting count endpoint (the tunnel restart gate).

`GET /api/v1/health/live-sessions` returns ``{"live": N}`` where N is the count
of sessions with ``status == "live"`` — the SAME authoritative DB flag the safety
watchdog queries. The packaged Tauri shell GETs this over loopback BEFORE
restarting the server to refuse going public while a meeting is active.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_live_sessions_zero_when_no_meeting(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health/live-sessions")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"live": 0}


@pytest.mark.asyncio
async def test_live_sessions_counts_live_then_clears_on_end(
    client: AsyncClient, admin_token: str
) -> None:
    headers = {"Authorization": f"Bearer {admin_token}"}

    # create_session sets status="live" → count goes to 1.
    create = await client.post(
        "/api/v1/sessions",
        json={"title": "Gate Test"},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    external_id = create.json()["session_id"]

    live = await client.get("/api/v1/health/live-sessions")
    assert live.status_code == 200, live.text
    assert live.json() == {"live": 1}

    # end_session sets status="ended" → count returns to 0 (gate clears).
    end = await client.post(f"/api/v1/sessions/{external_id}/end", headers=headers)
    assert end.status_code == 200, end.text

    cleared = await client.get("/api/v1/health/live-sessions")
    assert cleared.status_code == 200, cleared.text
    assert cleared.json() == {"live": 0}


@pytest.mark.asyncio
async def test_live_sessions_requires_no_auth(client: AsyncClient) -> None:
    # Mirrors /health: unauthenticated (loopback-only; never on the tunnel
    # allowlist) so the shell can probe it without operator creds.
    resp = await client.get("/api/v1/health/live-sessions")
    assert resp.status_code == 200, resp.text
