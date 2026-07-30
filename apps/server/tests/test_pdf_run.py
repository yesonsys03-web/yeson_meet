from __future__ import annotations

import asyncio
import shutil
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


async def test_fail_inflight_at_startup(db_session, admin_user, monkeypatch):
    monkeypatch.setattr(pdf_run, "_another_instance_is_serving", lambda: False)
    job = await _seed_job(db_session, admin_user, status="translating")
    job_id = job.id  # expire_all() 뒤 job.id 접근 우회 (위 주석 참고)
    await pdf_run.fail_inflight_pdf_jobs_at_startup()
    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "error"


async def test_fail_inflight_at_startup_skipped_when_another_instance_serving(
        db_session, admin_user, monkeypatch):
    # 이중 기동 방지: 소켓 프로브가 '이미 서빙 중'이라 보고하면 살아있는
    # 인스턴스의 진행 중 작업을 오판해 쓸어버리면 안 된다 (video 스윕과 동일 가드).
    monkeypatch.setattr(pdf_run, "_another_instance_is_serving", lambda: True)
    job = await _seed_job(db_session, admin_user, status="translating")
    job_id = job.id
    await pdf_run.fail_inflight_pdf_jobs_at_startup()
    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "translating"


async def test_run_pdf_job_cancelled_mid_translate_releases_semaphore(
        db_session, admin_user, monkeypatch):
    """Finding 1 커버 테스트 — 번역 도중(제너레이션이 바뀌어) 취소가 걸리면:
    1) CancelledError가 run_pdf_job 밖으로 전파되고
    2) 세마포어는 (고아 스레드 유무와 무관하게) 반드시 반납되며
    3) 제너레이션 불일치 분기라 에러 상태를 덮어쓰지 않는다(상태는 'translating'에 머문다).
    """
    job = await _seed_job(db_session, admin_user)
    eid = job.external_id
    job_id = job.id

    class CancelMidFlightTranslator:
        async def translate_batch(self, texts):
            # 실제 취소(cancel_pdf_task)가 이 배치 처리 도중 도착한 것을 흉내:
            # 세대를 바깥에서 올려버린다. 이 배치 자체는 정상 반환하지만,
            # translate_texts가 그 뒤 progress_cb를 부를 때 세대 불일치를 본다.
            _bump = pdf_run._bump_generation
            _bump(eid)
            return [f"KO:{t}" for t in texts]

    monkeypatch.setattr(
        pdf_run, "create_translator",
        lambda provider, cli_model, prompt_builder: CancelMidFlightTranslator())

    with pytest.raises(asyncio.CancelledError):
        await pdf_run.run_pdf_job(eid)

    assert pdf_run._PDF_SEMAPHORE._value == 1  # 반납됨(막힌 채로 남지 않음)
    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "translating"  # 에러로 덮어써지지 않았다
    assert row.error is None


async def test_run_pdf_job_semaphore_released_when_close_raises_cancelled(
        db_session, admin_user, monkeypatch):
    """Round-2 커버 테스트(NEW FINDING) — finally에서 doc.close()를 기다리는
    도중 두 번째 취소가 도착한 상황을 흉내(닫기 자체가 CancelledError를 던짐).

    닫기 실패(또는 재취소)가 나더라도 `_PDF_SEMAPHORE.release()`는 반드시
    실행돼야 한다 — 안 그러면 모듈 전역 세마포어(값 1)가 영구 고갈돼 이후
    모든 PDF 작업이 서버 재시작 전까지 조용히 멈춘다. 이미 성공적으로 끝난
    작업(상태 done)의 결과도 cleanup 실패로 훼손되면 안 된다.
    """
    from apps.server.domain.pdf_translate.backend_mupdf import MuPdfDocument

    def _boom_close(self) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(MuPdfDocument, "close", _boom_close)

    job = await _seed_job(db_session, admin_user)
    job_id = job.id

    # 실제 파이프라인(추출→번역→오버레이→done)은 정상 완료되고, 오직 마지막
    # cleanup의 doc.close()만 (두 번째 취소를 흉내내) 실패한다 — 그 실패가
    # 예외로 새어나오면 안 된다(이미 끝난 작업이므로 정상 반환돼야 한다).
    await pdf_run.run_pdf_job(job.external_id)

    assert pdf_run._PDF_SEMAPHORE._value == 1  # 반납됨(영구 고갈 아님)
    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "done"  # cleanup 실패가 이미 커밋된 결과를 훼손하지 않음


async def test_run_pdf_job_all_translations_failed_sets_error(
        db_session, admin_user, monkeypatch):
    """FINDING 1 커버 테스트 — 번역 엔진이 전량 실패(원문을 그대로 반환)하면
    done으로 조용히 넘기지 말고 명시적으로 error 처리해야 한다. 한글 블록은
    추출 단계에서 이미 걸러지므로, 정상 실행에서 유효 번역 0은 나올 수 없다.
    """
    class EchoTranslator:
        async def translate_batch(self, texts):
            return list(texts)  # 번역 실패를 흉내: 원문을 그대로 반환

    monkeypatch.setattr(
        pdf_run, "create_translator",
        lambda provider, cli_model, prompt_builder: EchoTranslator())

    job = await _seed_job(db_session, admin_user)
    job_id = job.id

    await pdf_run.run_pdf_job(job.external_id)

    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "error"
    assert "모든 블록 번역에 실패" in (row.error or "")
    assert row.translated_path is None


async def test_run_pdf_job_skips_save_when_dir_deleted_during_overlay(
        db_session, admin_user, monkeypatch):
    """FINDING 2 커버 테스트 — 오버레이 배치 도중(= doc.save() 직전) DELETE가
    도착한 상황을 흉내(실제 delete_pdf_job과 동일하게 세대를 올리고 작업
    폴더를 지운다). doc.save()의 mkdir(parents=True)이 방금 지워진 폴더를
    되살려 DB 행 없는 고아 translated.pdf를 남기면 안 된다.
    """
    from apps.server.domain.pdf_translate.profiles.storyboard import (
        StoryboardProfile,
    )

    job = await _seed_job(db_session, admin_user)
    eid = job.external_id
    job_id = job.id
    job_dir = pdf_job_dir(eid)

    _orig_place = StoryboardProfile.place

    def _place_then_delete(self, block, ko_text, page_size):
        # delete_pdf_job이 실제로 하는 일: cancel_pdf_task(세대 bump) 후
        # 작업 폴더를 rmtree — 이 시점 이후 doc.save()가 실행되면 안 된다.
        pdf_run._bump_generation(eid)
        shutil.rmtree(job_dir, ignore_errors=True)
        return _orig_place(self, block, ko_text, page_size)

    monkeypatch.setattr(StoryboardProfile, "place", _place_then_delete)

    with pytest.raises(asyncio.CancelledError):
        await pdf_run.run_pdf_job(eid)

    assert not job_dir.exists()  # rmtree 이후 doc.save()의 mkdir이 되살리지 않았다
    assert not (job_dir / "translated.pdf").exists()
    assert pdf_run._PDF_SEMAPHORE._value == 1  # 세마포어도 정상 반납

    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status != "done"
    assert row.translated_path is None
