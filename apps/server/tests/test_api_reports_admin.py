from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from apps.server.db.models import Session, Utterance


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path: Path):
    # 라우터가 STORAGE_ROOT 환경변수로 보고서 디렉터리를 찾으므로, 이 테스트의
    # tmp_path와 동일한 경로를 가리키게 한다 (video_jobs 테스트와 동일 패턴).
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))


async def _make_session(db_session, admin_user, *, status="ended", title="회의A"):
    s = Session(
        external_id=uuid4(),
        owner_user_id=admin_user.id,
        title=title,
        status=status,
    )
    db_session.add(s)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(
        Utterance(
            session_id=s.id, seq=1, text_en="hello", text_ko="안녕",
            started_at=now, ended_at=now,
        )
    )
    await db_session.commit()
    return s


async def test_list_reports_returns_sessions(client, db_session, admin_user):
    s = await _make_session(db_session, admin_user, title="분기회의")
    resp = await client.get("/api/v1/reports")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(
        it["session_id"] == str(s.external_id) and it["title"] == "분기회의"
        for it in items
    )


async def test_list_reports_report_ready_from_status(client, db_session, admin_user):
    s = await _make_session(db_session, admin_user, status="ended")
    resp = await client.get("/api/v1/reports")
    row = next(it for it in resp.json()["items"] if it["session_id"] == str(s.external_id))
    assert row["report_ready"] is True


async def test_list_reports_with_sizes(client, db_session, admin_user, tmp_path):
    s = await _make_session(db_session, admin_user)
    d = tmp_path / str(s.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text("# hi", encoding="utf-8")
    resp = await client.get("/api/v1/reports?with_sizes=true")
    row = next(it for it in resp.json()["items"] if it["session_id"] == str(s.external_id))
    assert row["size_bytes"] >= 4


async def test_storage_usage(client, db_session, admin_user, tmp_path):
    s = await _make_session(db_session, admin_user)
    d = tmp_path / str(s.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text("abcdef", encoding="utf-8")
    resp = await client.get("/api/v1/reports/storage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_count"] >= 1
    assert body["total_bytes"] >= 6
