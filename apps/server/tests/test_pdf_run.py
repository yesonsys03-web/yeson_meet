from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
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


async def _seed_job(db_session, admin_user, *, status="queued",
                    provider=None) -> PdfJob:
    eid = uuid4()
    src = pdf_job_dir(eid) / "source.pdf"
    _make_storyboard_pdf(src)
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="sb.pdf", status=status, source_path=str(src),
                 translate_provider=provider)
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


async def test_run_pdf_job_skips_degenerate_overlay_rect_with_warning(
        db_session, admin_user, monkeypatch, caplog):
    """2026-07-30 리뷰 Finding 1(b) 커버 테스트 — place()가 퇴화된(폭·높이가
    0 이하이거나 페이지 밖인) rect를 반환해도 그 블록만 건너뛰고 잡 전체는
    'done'으로 완료돼야 한다(+ 경고 로그). 방어선이 없으면
    add_freetext_annot이 'rect is infinite or empty'로 터져 이미 끝낸
    번역(dialog+action 둘 다)까지 통째로 날아간다."""
    from apps.server.domain.pdf_translate.profiles.base import Overlay
    from apps.server.domain.pdf_translate.profiles.storyboard import StoryboardProfile

    _orig_place = StoryboardProfile.place

    def _degenerate_for_dialog(self, block, ko_text, page_size):
        if block.kind == "dialog":
            # 리뷰어 실측 재현: y1 == y0(퇴화) — 방어선 없으면
            # add_freetext_annot이 여기서 크래시한다.
            return Overlay(page=block.page,
                           rect=(block.bbox[0], 613.0, block.bbox[2], 613.0),
                           text=ko_text, fontsize=8.0)
        return _orig_place(self, block, ko_text, page_size)

    monkeypatch.setattr(StoryboardProfile, "place", _degenerate_for_dialog)

    job = await _seed_job(db_session, admin_user)
    job_id = job.id

    with caplog.at_level("WARNING", logger="yeson.pdf.pipeline"):
        await pdf_run.run_pdf_job(job.external_id)

    assert any("유효하지" in r.message for r in caplog.records)

    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "done"  # 한 블록의 퇴화 rect가 잡 전체를 날리지 않았다

    import fitz
    out = Path(row.translated_path)
    d = fitz.open(out)
    contents = [a.info.get("content", "") for a in d[0].annots()]
    d.close()
    # dialog는 건너뛰었으니 action의 KO 주석 1개만 남아야 한다
    assert len(contents) == 1
    assert contents[0].startswith("KO:")


async def test_run_pdf_job_logs_kept_as_source_warning_on_partial_failure(
        db_session, admin_user, monkeypatch, caplog):
    """2026-07-30 리뷰 Finding 3(a) 커버 테스트 — 일부 그룹만 번역 실패로
    원문 그대로 남으면(부분 실패), 잡은 여전히 'done'이지만 몇 그룹이
    원문 그대로 남았는지 경고 로그로 남아야 한다(청크 병렬화로 CLI 콜
    수가 늘어난 뒤 부분 실패가 조용히 묻히기 쉬워서). Task 17부터 판정
    단위가 블록→그룹으로 바뀌었다(이 합성 PDF는 큐 패턴이 없어 그룹 수 ==
    블록 수 == 2이지만, 로그 문구는 "groups"로 바뀐다)."""

    class PartialFailTranslator:
        async def translate_batch(self, texts):
            # 첫 그룹만 "번역 실패"를 흉내(원문 그대로 반환), 나머지는 정상.
            return [texts[0]] + [f"KO:{t}" for t in texts[1:]]

    monkeypatch.setattr(
        pdf_run, "create_translator",
        lambda provider, cli_model, prompt_builder: PartialFailTranslator())

    job = await _seed_job(db_session, admin_user)
    job_id = job.id

    with caplog.at_level("WARNING", logger="yeson.pdf.pipeline"):
        await pdf_run.run_pdf_job(job.external_id)

    assert any(
        "1/2 groups kept as source" in r.message for r in caplog.records)

    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "done"  # 부분 실패는 전체 실패가 아니다(effective > 0)


def _make_chained_utterance_pdf(dest: Path) -> None:
    """97 JOSEPH 큐가 3페이지에 걸쳐 이어지는 합성 스토리보드 — Task 17
    통합 테스트용(각 페이지는 (CONT.) 조각, 실제 대사는 1·2페이지에만)."""
    import fitz
    doc = fitz.open()
    texts = [
        "97 JOSEPH You know,",
        "97 JOSEPH (Cont.) I was thinking",
        "97 JOSEPH (Cont.)",
    ]
    for text in texts:
        page = doc.new_page(width=1008, height=612)
        page.insert_text((680, 460), "Dialog", fontsize=8)
        page.insert_text((680, 478), text, fontsize=10)
        page.insert_text((72, 560), "Action Notes", fontsize=8)
        page.insert_text((72, 578), "Joseph gestures.", fontsize=10)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dest)
    doc.close()


async def _seed_chained_job(db_session, admin_user) -> PdfJob:
    eid = uuid4()
    src = pdf_job_dir(eid) / "source.pdf"
    _make_chained_utterance_pdf(src)
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="sb.pdf", status="queued", source_path=str(src))
    db_session.add(job)
    await db_session.commit()
    return job


async def test_run_pdf_job_fans_out_merged_utterance_to_every_chained_page(
        db_session, admin_user):
    """Task 17 통합 테스트 — 3페이지에 걸친 97 JOSEPH 체인은 그룹 하나로
    병합 번역되고, 그 KO 전문이 체인의 세 페이지 dialog 주석에 동일하게
    반복 기재돼야 한다(사람 번역본 관례)."""
    job = await _seed_chained_job(db_session, admin_user)
    job_id = job.id
    await pdf_run.run_pdf_job(job.external_id)
    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "done"

    import fitz
    d = fitz.open(row.translated_path)
    expected = "KO:97 JOSEPH You know, I was thinking"
    for page_idx in range(3):
        contents = [a.info.get("content", "") for a in d[page_idx].annots()]
        assert expected in contents  # 세 페이지 모두 같은 전문 KO 주석
    d.close()


async def test_run_pdf_job_chained_group_failure_skips_all_member_pages(
        db_session, admin_user, monkeypatch, caplog):
    """실패 팬아웃 — 번역기가 그룹의 merged_text를 그대로 반환하면(번역
    실패 폴백) 체인의 세 페이지 전부 dialog 주석이 0개여야 하고,
    kept_as_source 경고가 그룹 단위로 1건 남아야 한다. 조각(블록) 단위로
    비교했다면 (CONT.)-헤더만 있던 조각은 merged_text와 달라 보여 잘못
    '성공'으로 오판됐을 것이다."""

    class EchoDialogTranslator:
        async def translate_batch(self, texts):
            out = []
            for t in texts:
                if t.startswith("97 JOSEPH"):
                    out.append(t)  # 체인 그룹은 원문 그대로(번역 실패 흉내)
                else:
                    out.append(f"KO:{t}")
            return out

    monkeypatch.setattr(
        pdf_run, "create_translator",
        lambda provider, cli_model, prompt_builder: EchoDialogTranslator())

    job = await _seed_chained_job(db_session, admin_user)
    job_id = job.id

    with caplog.at_level("WARNING", logger="yeson.pdf.pipeline"):
        await pdf_run.run_pdf_job(job.external_id)

    kept_warnings = [r for r in caplog.records
                     if "groups kept as source" in r.message]
    assert len(kept_warnings) == 1
    assert "1/4 groups kept as source" in kept_warnings[0].message

    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "done"  # action 3개는 성공했으니 전체 실패는 아님

    import fitz
    d = fitz.open(row.translated_path)
    for page_idx in range(3):
        contents = [a.info.get("content", "") for a in d[page_idx].annots()]
        # dialog 체인 주석은 세 페이지 모두 0개(그룹 실패로 팬아웃 생략),
        # action(Joseph gestures.) 주석만 페이지마다 1개 남는다.
        assert len(contents) == 1
        assert contents[0] == "KO:Joseph gestures."
    d.close()


async def test_english_lhs_glossary_override_cannot_defeat_all_failed_guard(
        db_session, admin_user, monkeypatch, tmp_path):
    """전브랜치 리뷰 I-1 — 운영자가 콘솔(PUT /api/v1/glossary/{name})로 넣을 수
    있는 **영문 좌변** 교정 한 줄이 이 파일의 안전망 두 개를 동시에 무력화할 수
    있었다.

    후처리(apply_ko_corrections)가 번역 실패 폴백값(=영문 원문)을 바꿔버리면
    translate_blocks의 폴백 식별이 실패하고 → 여기 kept_as_source가 그 그룹을
    "번역 성공"으로 세고 → effective == 0 가드가 안 걸려 → 영문 원문이 한국어
    주석인 척 납품 PDF에 박힌 채 status=done이 된다.

    이 테스트는 오버라이드를 **실제 운영 경로**(STORAGE_ROOT/glossary_ko.txt)에
    심는다. 수정 전에는 status=done, 수정 후에는 error다.
    """
    class EchoTranslator:
        async def translate_batch(self, texts):
            return list(texts)  # 번역 엔진 전량 실패 흉내

    monkeypatch.setattr(
        pdf_run, "create_translator",
        lambda provider, cli_model, prompt_builder: EchoTranslator())
    # _env 픽스처가 STORAGE_ROOT=tmp_path로 잡아둔다 — 운영자 오버라이드 파일의
    # 기본 경로가 정확히 여기다(glossary.py: STORAGE_ROOT/glossary_ko.txt).
    (tmp_path / "glossary_ko.txt").write_text("door => 문\n", encoding="utf-8")

    job = await _seed_job(db_session, admin_user)
    job_id = job.id

    await pdf_run.run_pdf_job(job.external_id)

    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "error"
    assert "모든 블록 번역에 실패" in (row.error or "")
    assert row.translated_path is None


# ── 리텐션 프루닝 (전브랜치 리뷰 I-2) ─────────────────────────────────────
# video 잡 리텐션(maintenance.prune_old_video_jobs)의 규칙을 그대로 미러한다:
# 최근 keep개 유지, in-flight는 절대 삭제 금지, DELETE 시점 상태 재확인,
# startup 경로는 '다른 인스턴스가 서빙 중' 가드. 사용자 산출물을 지우는
# 코드라 "무엇이 살아남는지"를 행·파일 단위로 단언한다.

async def _make_dated_pdf_job(db_session, admin_user, created_at, *,
                              status="done"):
    eid = uuid4()
    d = pdf_job_dir(eid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "source.pdf").write_bytes(b"x")
    (d / "translated.pdf").write_bytes(b"y")
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="sb.pdf", status=status,
                 source_path=str(d / "source.pdf"), created_at=created_at)
    db_session.add(job)
    await db_session.commit()
    return job


async def test_prune_keeps_most_recent_n(db_session, admin_user):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    jobs = [await _make_dated_pdf_job(db_session, admin_user,
                                      base + timedelta(minutes=i))
            for i in range(12)]  # index 0 = 가장 오래됨, 11 = 최신
    ext = [j.external_id for j in jobs]

    removed = await pdf_run.prune_old_pdf_jobs(keep=10)

    assert removed == 2
    db_session.expire_all()
    surviving = {r.external_id for r in (await db_session.execute(
        select(PdfJob))).scalars()}
    # 최신 10개만 남고, 오래된 2개는 DB 행과 디스크 폴더 둘 다 사라진다
    assert ext[0] not in surviving and ext[1] not in surviving
    assert not pdf_job_dir(ext[0]).exists()
    assert not pdf_job_dir(ext[1]).exists()
    for e in ext[2:]:
        assert e in surviving
        assert (pdf_job_dir(e) / "translated.pdf").exists()


@pytest.mark.parametrize("inflight", ["queued", "extracting", "translating",
                                      "overlaying"])
async def test_prune_never_deletes_inflight_job(db_session, admin_user, inflight):
    """진행 중 작업은 keep 밖으로 밀려나도 절대 지우지 않는다 — 실행 중인
    run_pdf_job의 입력 파일(source.pdf)을 없애면 안 된다. 네 in-flight 상태를
    전부 확인해 상태 목록이 한 곳에서만 갱신되는 사고를 막는다."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    running = await _make_dated_pdf_job(db_session, admin_user, base,
                                        status=inflight)
    older_done = await _make_dated_pdf_job(
        db_session, admin_user, base + timedelta(minutes=1), status="done")
    recent = [await _make_dated_pdf_job(
        db_session, admin_user, base + timedelta(minutes=2 + i))
        for i in range(10)]
    running_ext, older_ext = running.external_id, older_done.external_id
    recent_ext = [j.external_id for j in recent]

    removed = await pdf_run.prune_old_pdf_jobs(keep=10)

    assert removed == 1  # older_done만 — 진행 중 작업은 보호됨
    db_session.expire_all()
    surviving = {r.external_id for r in (await db_session.execute(
        select(PdfJob))).scalars()}
    assert running_ext in surviving
    assert (pdf_job_dir(running_ext) / "source.pdf").exists()
    assert older_ext not in surviving
    assert not pdf_job_dir(older_ext).exists()
    assert all(e in surviving for e in recent_ext)


async def test_prune_reasserts_status_at_delete_time(db_session, admin_user,
                                                     monkeypatch):
    """SELECT와 DELETE 사이에 in-flight로 전이한 작업은 지우면 안 된다 —
    스냅샷에선 done(정당한 후보)이었다가 그 사이 같은 문서를 다시 돌리기
    시작한 경우. 상태 재확인 가드가 이를 살려야 한다."""
    from sqlalchemy import update as sa_update

    base = datetime(2026, 1, 1, tzinfo=UTC)
    stale = await _make_dated_pdf_job(db_session, admin_user, base, status="done")
    stale_ext, stale_pk = stale.external_id, stale.id
    for i in range(10):
        await _make_dated_pdf_job(db_session, admin_user,
                                  base + timedelta(minutes=1 + i))

    async def flip_to_translating(candidate_ids):
        assert stale_pk in candidate_ids  # 스냅샷 시점엔 분명히 후보였다
        async with pdf_run.AsyncSessionLocal() as db:
            await db.execute(sa_update(PdfJob).where(PdfJob.id == stale_pk)
                             .values(status="translating"))
            await db.commit()

    monkeypatch.setattr(pdf_run, "_prune_pre_delete_hook", flip_to_translating)

    removed = await pdf_run.prune_old_pdf_jobs(keep=10)

    assert removed == 0
    db_session.expire_all()
    surviving = {r.external_id for r in (await db_session.execute(
        select(PdfJob))).scalars()}
    assert stale_ext in surviving
    assert (pdf_job_dir(stale_ext) / "source.pdf").exists()


async def test_prune_at_startup_skipped_when_another_instance_serving(
        db_session, admin_user, monkeypatch):
    """이중 기동된 비소유 프로세스는 살아있는 인스턴스의 작업을 지우면 안 된다."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(12):
        await _make_dated_pdf_job(db_session, admin_user,
                                  base + timedelta(minutes=i))
    monkeypatch.setattr(pdf_run, "_another_instance_is_serving", lambda: True)

    removed = await pdf_run.prune_old_pdf_jobs_at_startup()

    assert removed == 0
    db_session.expire_all()
    assert len((await db_session.execute(select(PdfJob))).scalars().all()) == 12


async def test_prune_default_keep_is_smaller_than_video_retention():
    """PDF는 작업 1건이 원본+번역본 ~300MB라 영상(30)보다 상한이 작아야 한다 —
    같은 30이면 9GB가 된다."""
    from apps.server.domain.video_captions import maintenance
    assert pdf_run.RETENTION_KEEP < maintenance.RETENTION_KEEP


async def test_run_pdf_job_disabled_format_sets_error(db_session, admin_user,
                                                      monkeypatch):
    """format_hint 없이 올라온(구버전 클라·자동 감지) 업로드도 막혀야 한다 —
    API 게이트만 두면 이 경로로 샌다."""
    monkeypatch.setenv("YESON_PDF_STORYBOARD_ENABLED", "0")
    job = await _seed_job(db_session, admin_user)
    job_id = job.id  # expire_all() 뒤 job.id 접근 우회 (위 주석 참고)
    await pdf_run.run_pdf_job(job.external_id)
    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.id == job_id))).scalar_one()
    assert row.status == "error" and "비활성화" in (row.error or "")


def test_resolve_pdf_provider_blocks_gemini():
    """PDF 번역 엔진 정책 — 미지정·gemini는 기본(claude), 나머지는 그대로."""
    assert pdf_run.resolve_pdf_provider(None) == "claude"
    assert pdf_run.resolve_pdf_provider("") == "claude"
    assert pdf_run.resolve_pdf_provider("gemini") == "claude"
    assert pdf_run.resolve_pdf_provider("GEMINI ") == "claude"
    assert pdf_run.resolve_pdf_provider("codex") == "codex"


@pytest.mark.parametrize("stored", [None, "gemini"])
async def test_run_pdf_job_never_translates_with_gemini(
        db_session, admin_user, monkeypatch, stored):
    """gemini는 API 과금이라 PDF 파이프라인이 절대 쓰면 안 된다 —
    create_translator의 env 기본이 gemini라, 엔진 미지정 잡은 여기서 막지
    않으면 조용히 과금으로 흐른다."""
    used: list[str] = []

    def _record(provider, cli_model, prompt_builder):
        used.append(provider)
        return FakeTranslator()

    monkeypatch.setattr(pdf_run, "create_translator", _record)
    job = await _seed_job(db_session, admin_user, provider=stored)

    await pdf_run.run_pdf_job(job.external_id)

    assert used == ["claude"]
    # 목록·비용 감사가 실제 쓴 엔진을 보도록 행에도 되써야 한다(옛 gemini 행이
    # 재시작 복구로 다시 돌면 retranslate 경로를 안 거친다).
    await db_session.refresh(job)
    assert job.translate_provider == "claude"
