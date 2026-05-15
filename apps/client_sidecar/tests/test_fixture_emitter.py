"""Tests for transport.fixture_emitter."""
from __future__ import annotations

import pytest
from uuid import UUID

from apps.client_sidecar.transport.fixture_emitter import fixture_stream, FIXTURES


SESSION_ID = UUID("12345678-1234-5678-1234-567812345678")


@pytest.mark.asyncio
async def test_seq_monotonically_increasing() -> None:
    """seq values should be 1, 2, 3, 4, 5."""
    results = []
    async for evt in fixture_stream(SESSION_ID, interval_seconds=0.01):
        results.append(evt)
        if len(results) == 5:
            break

    seqs = [r["seq"] for r in results]
    assert seqs == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_all_five_fixtures_covered() -> None:
    """5 iterations should cover all 5 distinct fixtures (no repeat in first 5)."""
    results = []
    async for evt in fixture_stream(SESSION_ID, interval_seconds=0.01):
        results.append(evt)
        if len(results) == 5:
            break

    texts_en = [r["text_en"] for r in results]
    expected_en = [f[0] for f in FIXTURES]
    assert texts_en == expected_en

    texts_ko = [r["text_ko"] for r in results]
    expected_ko = [f[1] for f in FIXTURES]
    assert texts_ko == expected_ko


@pytest.mark.asyncio
async def test_event_type_and_session_id() -> None:
    """to_json_dict result must have type == 'utterance.transcribed' and session_id as str."""
    results = []
    async for evt in fixture_stream(SESSION_ID, interval_seconds=0.01):
        results.append(evt)
        if len(results) == 5:
            break

    for evt in results:
        assert evt["type"] == "utterance.transcribed"
        assert isinstance(evt["session_id"], str)
        assert evt["session_id"] == str(SESSION_ID)
