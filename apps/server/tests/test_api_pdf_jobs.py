from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from apps.server.api.v1 import pdf_jobs as api_pdf
from apps.server.db.models import PdfJob
from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(api_pdf, "_start_pdf_pipeline", lambda eid: None)
    yield


def _tiny_pdf_bytes() -> bytes:
    import fitz
    doc = fitz.open()
    doc.new_page(width=1008, height=612)
    data = doc.tobytes()
    doc.close()
    return data


async def test_upload_creates_job_and_saves_file(client, admin_user, db_session):
    resp = await client.post(
        "/api/v1/pdf-jobs/upload",
        data={"title": "콘티", "translate_provider": "gemini"},
        files={"file": ("GABE01_A1.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 201
    # SQLite adapt: Uuid(as_uuid=True) 바인드 프로세서는 순수 문자열이 아니라
    # uuid.UUID 인스턴스를 기대한다(문자열이면 .hex 접근에서 AttributeError —
    # test_api_video_jobs.py의 기존 6개 SQLite 실패와 동일한 근본 원인).
    # Postgres(asyncpg)는 문자열도 관대히 받아 이 차이가 안 드러난다.
    row = (await db_session.execute(select(PdfJob).where(
        PdfJob.external_id == UUID(resp.json()["job_id"])))).scalar_one()
    assert row.title == "콘티" and row.source_ref == "GABE01_A1.pdf"
    assert row.status == "queued"
    assert Path(row.source_path).exists()


async def test_upload_rejects_non_pdf(client, admin_user):
    resp = await client.post(
        "/api/v1/pdf-jobs/upload",
        files={"file": ("clip.mp4", b"xx", "video/mp4")},
    )
    assert resp.status_code == 422


async def test_upload_rejects_unknown_provider(client, admin_user):
    resp = await client.post(
        "/api/v1/pdf-jobs/upload",
        data={"translate_provider": "no-such-engine"},
        files={"file": ("a.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 422


async def test_list_and_detail(client, admin_user, db_session):
    job = PdfJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                 source_ref="a.pdf", status="done", progress=100,
                 format="storyboard", page_count=3, block_count=7)
    db_session.add(job)
    await db_session.commit()
    items = (await client.get("/api/v1/pdf-jobs")).json()["items"]
    assert items[0]["job_id"] == str(job.external_id)
    detail = (await client.get(f"/api/v1/pdf-jobs/{job.external_id}")).json()
    assert detail["format"] == "storyboard" and detail["page_count"] == 3


async def test_page_png_source_variant(client, admin_user, db_session):
    eid = uuid4()
    src = pdf_job_dir(eid) / "source.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(_tiny_pdf_bytes())
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="a.pdf", status="queued", source_path=str(src))
    db_session.add(job)
    await db_session.commit()
    resp = await client.get(f"/api/v1/pdf-jobs/{eid}/page/0?variant=source")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (await client.get(
        f"/api/v1/pdf-jobs/{eid}/page/9?variant=source")).status_code == 404
    assert (await client.get(
        f"/api/v1/pdf-jobs/{eid}/page/0?variant=translated")).status_code == 404


async def test_download_requires_done(client, admin_user, db_session, tmp_path):
    eid = uuid4()
    out = pdf_job_dir(eid) / "translated.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_tiny_pdf_bytes())
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="GABE01_A1.pdf", status="done", progress=100,
                 translated_path=str(out))
    db_session.add(job)
    await db_session.commit()
    resp = await client.get(f"/api/v1/pdf-jobs/{eid}/download")
    assert resp.status_code == 200
    assert "GABE01_A1_%EB%B2%88%EC%97%AD.pdf" in resp.headers.get(
        "content-disposition", "") or "_번역.pdf" in resp.headers.get(
        "content-disposition", "")


async def test_cancel_and_delete(client, admin_user, db_session):
    job = PdfJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                 source_ref="a.pdf", status="translating", progress=40)
    db_session.add(job)
    await db_session.commit()
    # 값들을 미리 로컬로 뽑아둔다 — expire_all() 이후 job.id/job.external_id를
    # 다시 읽으면 만료된 속성의 암묵적 재조회가 await 밖(평범한 속성 접근)에서
    # 시도되어 MissingGreenlet으로 죽는다(비동기 ORM 세션의 일반 제약, 백엔드
    # 무관 — SQLite 전용 문제 아님).
    job_id, job_pk = job.external_id, job.id
    assert (await client.post(
        f"/api/v1/pdf-jobs/{job_id}/cancel")).status_code == 200
    db_session.expire_all()
    row = (await db_session.execute(select(PdfJob).where(
        PdfJob.id == job_pk))).scalar_one()
    assert row.status == "cancelled"
    assert (await client.post(
        f"/api/v1/pdf-jobs/{job_id}/cancel")).status_code == 409
    assert (await client.delete(
        f"/api/v1/pdf-jobs/{job_id}")).status_code == 204
