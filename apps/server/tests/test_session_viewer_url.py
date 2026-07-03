"""Mid-meeting viewer-link re-fetch tests (GET /sessions/{id}/viewer-url).

Tunnel recovery: when the public quick-tunnel drops mid-meeting, the desktop
re-publishes and a NEW trycloudflare host is minted. The viewer URL string
returned at session creation is stale from that moment, so the client console
re-fetches the link here — same DB viewer token, CURRENT viewer base.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient


async def _create_session(client: AsyncClient, admin_token: str) -> tuple[UUID, str]:
    response = await client.post(
        "/api/v1/sessions",
        json={"title": "Viewer URL Test", "client_label": "CLIENT-A"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return UUID(body["session_id"]), body["viewer_url"].rsplit("/", 1)[-1]


@pytest.mark.asyncio
async def test_viewer_url_reflects_current_base(
    client: AsyncClient,
    admin_token: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    session_id, viewer_token = await _create_session(client, admin_token)

    # Simulate a mid-meeting Go Live re-publish: the desktop writes a fresh
    # public base to {STORAGE_ROOT}/viewer_base.txt. The re-fetched link must
    # combine that CURRENT base with the session's ORIGINAL viewer token.
    (tmp_path / "viewer_base.txt").write_text(
        "https://new-host.trycloudflare.com", encoding="utf-8"
    )

    response = await client.get(
        f"/api/v1/sessions/{session_id}/viewer-url",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == str(session_id)
    assert body["viewer_url"] == f"https://new-host.trycloudflare.com/v/{viewer_token}"


@pytest.mark.asyncio
async def test_viewer_url_requires_operator(client: AsyncClient, admin_token: str) -> None:
    session_id, _ = await _create_session(client, admin_token)
    response = await client.get(f"/api/v1/sessions/{session_id}/viewer-url")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_viewer_url_unknown_session_is_404(
    client: AsyncClient, admin_token: str
) -> None:
    response = await client.get(
        f"/api/v1/sessions/{uuid4()}/viewer-url",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
