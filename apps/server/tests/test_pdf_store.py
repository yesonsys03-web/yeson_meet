from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from apps.server.db.models import PdfJob
from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir, pdf_jobs_root


def test_pdf_job_dir_under_storage_root(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    eid = uuid4()
    assert pdf_jobs_root() == tmp_path / "pdf_jobs"
    assert pdf_job_dir(eid) == tmp_path / "pdf_jobs" / str(eid)


async def test_pdf_job_row_roundtrip(db_session, admin_user):
    job = PdfJob(external_id=uuid4(), owner_user_id=admin_user.id,
                 title="GABE01_A1", source_ref="GABE01_A1.pdf", status="queued")
    db_session.add(job)
    await db_session.commit()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job.id))).scalar_one()
    assert row.status == "queued" and row.progress == 0
    assert row.format is None and row.translated_path is None
