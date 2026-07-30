from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from apps.server.db.models import PdfJob
from apps.server.domain.pdf_translate import pdf_run
from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir


def _make_storyboard_pdf(dest: Path) -> None:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    page.insert_text((680, 460), "Dialog", fontsize=8)
    page.insert_text((680, 478), "If you wanna go, then go.", fontsize=10)
    page.insert_text((72, 560), "Action Notes", fontsize=8)
    page.insert_text((72, 578), "HANK walks to the door.", fontsize=10)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dest)
    doc.close()


class FakeTranslator:
    async def translate_batch(self, texts):
        return [f"KO:{t}" for t in texts]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(pdf_run, "create_translator",
                        lambda provider, cli_model, prompt_builder: FakeTranslator())
    yield


async def _seed_job(db_session, admin_user, *, status="queued") -> PdfJob:
    eid = uuid4()
    src = pdf_job_dir(eid) / "source.pdf"
    _make_storyboard_pdf(src)
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="sb.pdf", status=status, source_path=str(src))
    db_session.add(job)
    await db_session.commit()
    return job


async def test_run_pdf_job_happy_path(db_session, admin_user):
    job = await _seed_job(db_session, admin_user)
    job_id = job.id  # expire_all() 뒤 job.id 접근은 만료 속성 동기 재로드로
    # aiosqlite에서 MissingGreenlet을 일으킨다 — 만료 전에 캡처해 우회.
    await pdf_run.run_pdf_job(job.external_id)
    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "done" and row.progress == 100
    assert row.format == "storyboard" and row.page_count == 1
    assert row.block_count == 2
    out = Path(row.translated_path)
    assert out.exists()
    import fitz
    d = fitz.open(out)
    contents = [a.info.get("content", "") for a in d[0].annots()]
    d.close()
    assert any(c.startswith("KO:") for c in contents)


async def test_run_pdf_job_unsupported_format_sets_error(db_session, admin_user,
                                                         tmp_path):
    import fitz
    eid = uuid4()
    src = pdf_job_dir(eid) / "source.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(); doc.new_page(width=612, height=792); doc.save(src); doc.close()
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="x.pdf", status="queued", source_path=str(src))
    db_session.add(job)
    await db_session.commit()
    job_id = job.id  # expire_all() 뒤 job.id 접근 우회 (위 주석 참고)
    await pdf_run.run_pdf_job(eid)
    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "error" and "포맷" in (row.error or "")


async def test_fail_inflight_at_startup(db_session, admin_user):
    job = await _seed_job(db_session, admin_user, status="translating")
    job_id = job.id  # expire_all() 뒤 job.id 접근 우회 (위 주석 참고)
    await pdf_run.fail_inflight_pdf_jobs_at_startup()
    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "error"
