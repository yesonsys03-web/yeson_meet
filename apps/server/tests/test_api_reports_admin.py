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


async def test_report_view_html(client, db_session, admin_user):
    s = await _make_session(db_session, admin_user, title="뷰회의")
    resp = await client.get(f"/api/v1/reports/{s.external_id}/view")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "뷰회의" in resp.text


async def test_report_view_404_for_unknown(client):
    from uuid import uuid4
    resp = await client.get(f"/api/v1/reports/{uuid4()}/view")
    assert resp.status_code == 404


async def test_summary_view_when_present(client, db_session, admin_user, tmp_path):
    s = await _make_session(db_session, admin_user)
    d = tmp_path / str(s.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.md").write_text("핵심 요약 문장", encoding="utf-8")
    resp = await client.get(f"/api/v1/reports/{s.external_id}/summary/view")
    assert resp.status_code == 200
    assert "핵심 요약" in resp.text


async def test_report_download_md(client, db_session, admin_user):
    s = await _make_session(db_session, admin_user, title="다운회의")
    resp = await client.get(f"/api/v1/reports/{s.external_id}/download?fmt=md")
    assert resp.status_code == 200
    assert "다운회의" in resp.text


async def test_report_download_docx_bytes(client, db_session, admin_user):
    s = await _make_session(db_session, admin_user)
    resp = await client.get(f"/api/v1/reports/{s.external_id}/download?fmt=docx")
    assert resp.status_code == 200
    # docx(zip)는 PK 매직 바이트로 시작
    assert resp.content[:2] == b"PK"


async def test_download_rejects_bad_fmt(client, db_session, admin_user):
    s = await _make_session(db_session, admin_user)
    resp = await client.get(f"/api/v1/reports/{s.external_id}/download?fmt=xml")
    assert resp.status_code == 400


async def test_delete_files_only(client, db_session, admin_user, tmp_path):
    s = await _make_session(db_session, admin_user)
    d = tmp_path / str(s.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text("x", encoding="utf-8")
    (d / "report.pdf").write_bytes(b"%PDF")
    (d / "summary.md").write_text("y", encoding="utf-8")

    resp = await client.delete(f"/api/v1/reports/{s.external_id}/files")
    assert resp.status_code == 204
    assert not (d / "report.md").exists()
    assert not (d / "report.pdf").exists()
    assert not (d / "summary.md").exists()

    # DB 세션과 자막은 보존됨
    from apps.server.db.models import Session as S, Utterance as U
    from sqlalchemy import select
    kept = (await db_session.execute(select(S).where(S.external_id == s.external_id))).scalar_one_or_none()
    assert kept is not None
    utt = (await db_session.execute(select(U).where(U.session_id == s.id))).scalars().all()
    assert len(utt) == 1


async def test_delete_whole_session(client, db_session, admin_user, tmp_path):
    s = await _make_session(db_session, admin_user)
    sid_pk = s.id
    ext = s.external_id
    d = tmp_path / str(ext)
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text("x", encoding="utf-8")

    resp = await client.delete(f"/api/v1/reports/{ext}/session")
    assert resp.status_code == 204

    from apps.server.db.models import Session as S, Utterance as U
    from sqlalchemy import select
    gone = (await db_session.execute(select(S).where(S.external_id == ext))).scalar_one_or_none()
    assert gone is None
    # Utterance는 CASCADE로 삭제됨
    utt = (await db_session.execute(select(U).where(U.session_id == sid_pk))).scalars().all()
    assert utt == []
    # 스토리지 디렉토리 제거됨
    assert not d.exists()


async def test_delete_session_404_for_unknown(client):
    from uuid import uuid4
    resp = await client.delete(f"/api/v1/reports/{uuid4()}/session")
    assert resp.status_code == 404
