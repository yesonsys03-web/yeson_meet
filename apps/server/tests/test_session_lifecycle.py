"""Slice 4 session end + report lifecycle tests."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.db.models import Session, Utterance
from apps.server.ws.bus import bus


async def _create_session(client: AsyncClient, admin_token: str) -> tuple[UUID, str]:
    response = await client.post(
        "/api/v1/sessions",
        json={"title": "Lifecycle Test", "client_label": "CLIENT-A"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return UUID(body["session_id"]), body["viewer_url"].rsplit("/", 1)[-1]


async def _add_utterance(db_session: AsyncSession, session_id: UUID) -> None:
    meeting = (
        await db_session.execute(select(Session).where(Session.external_id == session_id))
    ).scalar_one()
    now = datetime.now(timezone.utc)
    db_session.add(
        Utterance(
            session_id=meeting.id,
            seq=1,
            speaker=None,
            text_en="Please send the background fix by Friday.",
            text_ko="금요일까지 background fix를 보내주세요.",
            started_at=now,
            ended_at=now,
            is_final=True,
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_end_session_publishes_event_and_writes_report(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    session_id, viewer_token = await _create_session(client, admin_token)
    await _add_utterance(db_session, session_id)
    queue = bus.subscribe(session_id)

    try:
        response = await client.post(
            f"/api/v1/sessions/{session_id}/end",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        event = await queue.get()
    finally:
        bus.unsubscribe(session_id, queue)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ended"
    assert event["type"] == "session.ended"
    assert event["session_id"] == str(session_id)

    report_path = Path(body["report_path"])
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert "# Lifecycle Test" in report
    assert "Please send the background fix by Friday." in report
    assert "금요일까지 background fix를 보내주세요." in report

    backfill = await client.get(f"/api/v1/viewer/utterances?token={viewer_token}")
    assert backfill.status_code == 200
    assert backfill.json()["session_status"] == "ended"


@pytest.mark.asyncio
async def test_download_session_report_generates_missing_report_for_ended_session(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    session_id, _viewer_token = await _create_session(client, admin_token)
    meeting = (
        await db_session.execute(select(Session).where(Session.external_id == session_id))
    ).scalar_one()
    meeting.status = "ended"
    meeting.ended_at = datetime.now(timezone.utc)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/sessions/{session_id}/report",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200, response.text
    assert "# Lifecycle Test" in response.text
    assert "_No utterances recorded._" in response.text


@pytest.mark.asyncio
async def test_download_session_report_rejects_live_session(
    client: AsyncClient,
    admin_token: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    session_id, _viewer_token = await _create_session(client, admin_token)

    response = await client.get(
        f"/api/v1/sessions/{session_id}/report",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 409


async def _end_session_and_write_summary(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    session_id: UUID,
    storage_root: Path,
) -> None:
    meeting = (
        await db_session.execute(select(Session).where(Session.external_id == session_id))
    ).scalar_one()
    meeting.status = "ended"
    meeting.ended_at = datetime.now(timezone.utc)
    await db_session.commit()

    # Write the standalone summary.md the routes read from.
    from apps.server.domain.reports import summary_path

    s_path = summary_path(storage_root, str(session_id), "md")
    s_path.parent.mkdir(parents=True, exist_ok=True)
    s_path.write_text("# 요약 — Lifecycle Test\n\n핵심 요약 본문입니다.\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_download_summary_html_renders_when_summary_present(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    session_id, _viewer_token = await _create_session(client, admin_token)
    await _end_session_and_write_summary(client, admin_token, db_session, session_id, tmp_path)

    response = await client.get(
        f"/api/v1/sessions/{session_id}/report.summary.html",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200, response.text
    assert "<!DOCTYPE html>" in response.text
    # The md H1 header must be stripped from the body before rendering.
    assert "핵심 요약 본문입니다." in response.text
    assert "# 요약" not in response.text


@pytest.mark.asyncio
async def test_download_summary_docx_renders_when_summary_present(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    session_id, _viewer_token = await _create_session(client, admin_token)
    await _end_session_and_write_summary(client, admin_token, db_session, session_id, tmp_path)

    response = await client.get(
        f"/api/v1/sessions/{session_id}/report.summary.docx",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


@pytest.mark.asyncio
async def test_download_summary_html_404_when_summary_missing(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    session_id, _viewer_token = await _create_session(client, admin_token)
    meeting = (
        await db_session.execute(select(Session).where(Session.external_id == session_id))
    ).scalar_one()
    meeting.status = "ended"
    meeting.ended_at = datetime.now(timezone.utc)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/sessions/{session_id}/report.summary.html",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_download_summary_pdf_503_when_soffice_absent(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    session_id, _viewer_token = await _create_session(client, admin_token)
    await _end_session_and_write_summary(client, admin_token, db_session, session_id, tmp_path)

    from unittest.mock import patch

    with patch("apps.server.domain.report_pdf.find_pdf_engine", return_value=[]):
        response = await client.get(
            f"/api/v1/sessions/{session_id}/report.summary.pdf",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 503, response.text
