"""Slice 3 AI provider health endpoint tests."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


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
        "model": "gemini-live-2.5-flash-preview",
        "input_sample_rate": 16000,
    }


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
    assert dummy_key not in resp.text
