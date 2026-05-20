"""Slice 3 AI provider health endpoint tests."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from apps.server.ops.alerts import operator_alerts


@pytest.fixture(autouse=True)
def reset_operator_alerts() -> None:
    operator_alerts.reset()
    yield
    operator_alerts.reset()


@pytest.mark.asyncio
async def test_ai_health_reports_missing_gemini_key(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_LIVE_MODEL", raising=False)

    resp = await client.get("/api/v1/health/ai")

    assert resp.status_code == 200
    data = resp.json()
    assert data["gemini"] == {
        "configured": False,
        "status": "missing_api_key",
        "model": "gemini-3.1-flash-live-preview",
        "input_sample_rate": 16000,
    }
    assert data["google_stt_translate"]["configured"] is False


@pytest.mark.asyncio
async def test_ai_health_missing_key_raises_operator_alert(
    admin_token: str,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    await client.get("/api/v1/health/ai")
    resp = await client.get(
        "/api/v1/operator/alerts",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200
    alerts = resp.json()
    assert len(alerts) == 1
    assert alerts[0]["code"] == "gemini_api_key_missing"
    assert alerts[0]["severity"] == "critical"
    assert "GEMINI_API_KEY" in alerts[0]["message"]


@pytest.mark.asyncio
async def test_ai_health_configured_key_resolves_operator_alert(
    admin_token: str,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    await client.get("/api/v1/health/ai")

    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-gemini-key-for-test")
    await client.get("/api/v1/health/ai")
    resp = await client.get(
        "/api/v1/operator/alerts",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_ai_health_reports_configured_gemini_without_leaking_key(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dummy_key = "not-a-real-gemini-key-for-test"
    monkeypatch.setenv("GEMINI_API_KEY", dummy_key)
    monkeypatch.setenv("GEMINI_LIVE_MODEL", "custom-live-model")

    resp = await client.get("/api/v1/health/ai")

    assert resp.status_code == 200
    data = resp.json()
    assert data["gemini"] == {
        "configured": True,
        "status": "configured",
        "model": "custom-live-model",
        "input_sample_rate": 16000,
    }
    assert "google_stt_translate" in data
    assert dummy_key not in resp.text
