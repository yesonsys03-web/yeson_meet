"""S1 게이트 — 오버레이 계획 산출·영속.

핵심 성질 하나만 기억하면 된다: **계획은 지워지지 않는다.** 굽는 도중 취소가
와도 계획 파일은 남고 `baked_edits_version`만 무효(-1)가 된다. 지우는 설계였다면
그 순간 "멀쩡한 옛 PDF + 멀쩡한 편집 + 계획 없음"이 남아 사용자의 수동 라벨이
목록에서 통째로 사라졌을 것이다.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from apps.server.db.models import PdfJob
from apps.server.domain.pdf_translate import overlay_plan, pdf_run
from apps.server.domain.pdf_translate.overlay_plan import (
    UNBAKED,
    LabelEdits,
    ManualLabel,
    compose,
    load_plan,
    save_edits,
)
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
        return [f"번역{i}" for i, _ in enumerate(texts)]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(pdf_run, "create_translator",
                        lambda provider, cli_model, prompt_builder: FakeTranslator())
    yield


async def _seed_job(db_session, admin_user) -> PdfJob:
    eid = uuid4()
    src = pdf_job_dir(eid) / "source.pdf"
    _make_storyboard_pdf(src)
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="sb.pdf", status="queued", source_path=str(src))
    db_session.add(job)
    await db_session.commit()
    return job


def _annot_count(path: Path) -> int:
    import fitz
    doc = fitz.open(path)
    try:
        return sum(len(list(doc[p].annots())) for p in range(doc.page_count))
    finally:
        doc.close()


# ── (a) build_plan 기하가 profile.place 직접 호출과 일치 ──────────────────────

async def test_build_plan_geometry_matches_direct_place(db_session, admin_user):
    """계획의 rect·fontsize는 `profile.place`가 낸 값 그대로여야 한다 —
    계획은 배치 규칙을 **새로 만드는 게 아니라 기록**하는 것이다."""
    from apps.server.domain.pdf_translate.backend import open_pdf
    from apps.server.domain.pdf_translate.overlay_plan import _rect2, build_plan
    from apps.server.domain.pdf_translate.profiles import detect_profile

    job = await _seed_job(db_session, admin_user)
    doc = open_pdf(Path(job.source_path))
    try:
        profile = detect_profile(doc)
        blocks = profile.extract(doc)
        ko = [f"번역{i}" for i in range(len(blocks))]
        plan = build_plan(doc, profile, blocks, ko, job_id=str(job.external_id))
        assert plan.items, "블록이 있으면 계획 항목도 있어야 한다"
        for item, block, text in zip(plan.items, blocks, ko):
            ov = profile.place(block, text, doc.page_size(block.page))
            assert item.rect == _rect2(ov.rect)
            assert item.fontsize == ov.fontsize
            assert item.text == ov.text
    finally:
        doc.close()


# ── (b) 실행 후 계획 존재 + annots == placed ──────────────────────────────────

async def test_run_writes_plan_and_annots_match_placed(db_session, admin_user):
    job = await _seed_job(db_session, admin_user)
    job_dir = pdf_job_dir(job.external_id)
    await pdf_run.run_pdf_job(job.external_id)

    plan = load_plan(job_dir)
    assert plan is not None, "실행이 끝나면 계획이 남아야 한다"
    assert plan.items
    assert _annot_count(job_dir / "translated.pdf") == len(plan.items)
    # 굽기가 끝났으므로 무효 표식이 풀려 있어야 한다(= stale 아님).
    assert plan.baked_edits_version == 0
    # 소형 상태 파일도 함께 — 목록 라우트가 계획 파일을 열지 않게 하는 장치다.
    status = overlay_plan.load_plan_status(job_dir)
    assert status is not None
    assert status["item_count"] == len(plan.items)
    assert status["baked_edits_version"] == 0
    # 잘린 산출물이 남지 않는다(tmp → os.replace).
    assert not (job_dir / "translated.pdf.tmp").exists()


# ── (c) 취소해도 계획이 남고 무효 표식만 남는다 ───────────────────────────────

async def test_cancel_during_overlay_keeps_plan_and_marks_unbaked(
        db_session, admin_user, monkeypatch):
    """계획을 **지우지 않는** 설계의 핵심 단언.

    지우는 설계였다면 이 상황에서 목록이 `plan_missing`이 되어 사용자의 수동
    라벨이 통째로 사라지고, rebake·retranslate가 전부 409로 막혔을 것이다.
    """
    job = await _seed_job(db_session, admin_user)
    job_dir = pdf_job_dir(job.external_id)
    # 1회차: 정상 실행으로 계획을 만들어 둔다.
    await pdf_run.run_pdf_job(job.external_id)
    first = load_plan(job_dir)
    assert first is not None and first.baked_edits_version == 0

    # 2회차: 계획을 다 만든 **직후**부터 세대가 어긋나게 해 취소를 흉내 낸다.
    real_build = overlay_plan.build_plan
    flipped: list[bool] = []

    def build_then_flip(*args, **kwargs):
        plan = real_build(*args, **kwargs)
        flipped.append(True)
        return plan

    real_current = pdf_run._current_generation
    monkeypatch.setattr(pdf_run, "build_plan", build_then_flip)
    monkeypatch.setattr(pdf_run, "_current_generation",
                        lambda eid: (-999 if flipped else real_current(eid)))

    job.status = "queued"
    await db_session.commit()
    # CancelledError는 BaseException 상속이라 Exception으로는 잡히지 않는다.
    with pytest.raises(asyncio.CancelledError):
        await pdf_run.run_pdf_job(job.external_id)

    after = load_plan(job_dir)
    assert after is not None, "취소돼도 계획 파일은 남아야 한다"
    assert after.baked_edits_version == UNBAKED, "무효 표식이 남아야 stale=true"
    assert not (job_dir / "translated.pdf.tmp").exists(), "잘린 산출물 잔여 금지"


# ── (d) 편집 파일이 없어도 정상 동작 ──────────────────────────────────────────

async def test_run_works_without_edits_file(db_session, admin_user):
    job = await _seed_job(db_session, admin_user)
    job_dir = pdf_job_dir(job.external_id)
    assert not (job_dir / "label_edits.json").exists()
    await pdf_run.run_pdf_job(job.external_id)
    assert load_plan(job_dir) is not None
    # 파이프라인은 편집 파일의 저자가 아니다 — 없다고 만들어 두지도 않는다.
    assert not (job_dir / "label_edits.json").exists()


# ── (e) refine_ko는 사람이 친 텍스트에 적용되지 않는다 ────────────────────────

def test_compose_does_not_refine_manual_text():
    """`refine_ko`의 판정식은 `[A-Za-z]{2,}`라 사람이 일부러 친 라틴 라벨
    (`CAM`·`BG`)을 통째로 지운다 — 자동 경로(`build_plan`)에만 남겨야 한다."""
    edits = LabelEdits(job_id="j", edits_version=1, manual=[ManualLabel(
        id="m1", page=0, panel_index=0, rel=(0.1, 0.1), size=(80.0, 20.0),
        fontsize=10.0, source_text="CAM GUIDE", text="CAM 가이드")])
    plan = overlay_plan.OverlayPlan(
        job_id="j", profile="storyboard", page_count=1,
        page_sizes=[(1008.0, 612.0)], items=[])
    result = compose(plan, edits, lambda page: ((0.0, 0.0, 100.0, 100.0),))
    assert [p.text for p in result.placed] == ["CAM 가이드"]


def test_compose_reports_unresolved_manual_without_guessing():
    """주소를 잃은 수동 라벨은 **다른 판넬로 추정하지 않고** 목록으로 돌려준다.
    조용한 오배치가 조용한 소실보다 낫지 않다."""
    edits = LabelEdits(job_id="j", edits_version=1, manual=[ManualLabel(
        id="m1", page=0, panel_index=5, rel=(0.1, 0.1), size=(80.0, 20.0),
        fontsize=10.0, source_text="IN", text="들어온다")])
    plan = overlay_plan.OverlayPlan(
        job_id="j", profile="storyboard", page_count=1,
        page_sizes=[(1008.0, 612.0)], items=[])
    result = compose(plan, edits, lambda page: ((0.0, 0.0, 100.0, 100.0),))
    assert result.placed == []
    assert [m.id for m in result.unresolved] == ["m1"]


# ── (f) 같은 문서를 두 번 구우면 item id가 서로 다르다 ────────────────────────

async def test_rebuilding_plan_issues_fresh_item_ids(db_session, admin_user):
    """계획 item id는 **난수**다(내용 해시가 아니다).

    결정적 해시로 구현하면 변하지 않은 페이지의 자동 라벨 수정이 재번역을 넘어
    살아남아, S6 게이트("override는 전량 dangling")가 거짓 실패한다. 두 규칙이
    사용자에게 보이는 동작을 가르므로 여기서 못박는다.
    """
    job = await _seed_job(db_session, admin_user)
    job_dir = pdf_job_dir(job.external_id)
    await pdf_run.run_pdf_job(job.external_id)
    first = {it.id for it in load_plan(job_dir).items}

    job.status = "queued"
    await db_session.commit()
    await pdf_run.run_pdf_job(job.external_id)
    second_plan = load_plan(job_dir)
    second = {it.id for it in second_plan.items}

    assert first and second
    assert first.isdisjoint(second), "id가 재사용되면 난수 규칙이 깨진 것이다"
    # 계획 생성 세대는 올라간다(rebake는 올리지 않는다 — S2에서 검증).
    assert second_plan.plan_version == 2


# ── 수동 라벨이 주소로 재부착된다(구조적 성질의 최소 확인) ────────────────────

async def test_manual_label_survives_rebuild_by_address(db_session, admin_user):
    """파이프라인은 편집 파일을 읽기만 하므로 수동 라벨은 재실행을 그냥 넘긴다 —
    신원 키 휴리스틱이 0개인 이유다."""
    job = await _seed_job(db_session, admin_user)
    job_dir = pdf_job_dir(job.external_id)
    await pdf_run.run_pdf_job(job.external_id)
    before = _annot_count(job_dir / "translated.pdf")

    save_edits(job_dir, LabelEdits(
        job_id=str(job.external_id), edits_version=3,
        manual=[ManualLabel(id="m1", page=0, panel_index=0, rel=(0.1, 0.1),
                            size=(80.0, 20.0), fontsize=10.0,
                            source_text="IN", text="들어온다")]))

    job.status = "queued"
    await db_session.commit()
    await pdf_run.run_pdf_job(job.external_id)

    plan = load_plan(job_dir)
    assert plan.baked_edits_version == 3, "합성에 쓴 편집 스냅샷 버전이 새겨져야 한다"
    # 합성 페이지에는 판넬 이미지가 없어 수동 라벨은 해석 불가로 보존된다 —
    # **삭제되지 않는다**는 것이 여기서 확인할 성질이다.
    edits = overlay_plan.load_edits(job_dir)
    assert [m.id for m in edits.manual] == ["m1"]
    assert edits.edits_version == 3, "파이프라인이 편집 파일을 쓰지 않았다"
    assert _annot_count(job_dir / "translated.pdf") == before


# ── S2 게이트 — 합성 재굽기·잡 슬롯 공유·상태 원복 ────────────────────────────

async def test_rebake_applies_edited_plan_without_translating(
        db_session, admin_user, monkeypatch):
    """계획을 직접 고쳐 rebake → 주석 텍스트가 바뀐다. 번역기는 부르지 않는다."""
    job = await _seed_job(db_session, admin_user)
    job_dir = pdf_job_dir(job.external_id)
    await pdf_run.run_pdf_job(job.external_id)

    plan = load_plan(job_dir)
    edited = overlay_plan.OverlayPlan(
        job_id=plan.job_id, profile=plan.profile, page_count=plan.page_count,
        page_sizes=plan.page_sizes,
        items=[overlay_plan.PlanItem(
            id=it.id, kind=it.kind, page=it.page, panel_index=it.panel_index,
            rect=it.rect, fontsize=it.fontsize, source_text=it.source_text,
            text="편집된주석") for it in plan.items],
        plan_version=plan.plan_version, baked_edits_version=plan.baked_edits_version)
    overlay_plan.save_plan(job_dir, edited)

    def _boom(*a, **kw):  # 번역기를 부르면 즉시 실패시킨다
        raise AssertionError("rebake는 번역기를 부르면 안 된다")

    monkeypatch.setattr(pdf_run, "create_translator", _boom)
    job.status = "done"
    await db_session.commit()
    await pdf_run.rebake_pdf_job(job.external_id)

    import fitz
    doc = fitz.open(job_dir / "translated.pdf")
    try:
        contents = [a.info.get("content", "")
                    for p in range(doc.page_count) for a in doc[p].annots()]
    finally:
        doc.close()
    assert contents and all(c == "편집된주석" for c in contents), contents


async def test_rebake_deletes_annotation_via_override(db_session, admin_user):
    """`deleted` override는 재굽기 결과에서 그 주석을 없앤다 —
    이미 구운 PDF의 주석을 지우는 게 아니라 **다시 굽는다**(AC12)."""
    job = await _seed_job(db_session, admin_user)
    job_dir = pdf_job_dir(job.external_id)
    await pdf_run.run_pdf_job(job.external_id)
    plan = load_plan(job_dir)
    before = _annot_count(job_dir / "translated.pdf")

    save_edits(job_dir, LabelEdits(
        job_id=str(job.external_id), edits_version=1,
        overrides=[overlay_plan.Override(
            target=plan.items[0].id, page=plan.items[0].page, deleted=True)]))
    job.status = "done"
    await db_session.commit()
    await pdf_run.rebake_pdf_job(job.external_id)

    assert _annot_count(job_dir / "translated.pdf") == before - 1
    assert load_plan(job_dir).baked_edits_version == 1


async def test_semaphore_is_released_exactly_once_after_cancel(
        db_session, admin_user, monkeypatch):
    """세마포어 값이 1로 돌아온다 — **과다 릴리스도 함께 잡는다**(2가 되면 실패).

    `acquire`를 `try` 안으로 넣으면 값이 2가 되어 직렬화가 깨지고, `finally`를
    빠뜨리면 0으로 고갈돼 재시작 전까지 모든 작업이 조용히 멈춘다. 양방향
    함정이라 한 단언으로 둘 다 지킨다.
    """
    from apps.server.domain.pdf_translate.pdf_tasks import _PDF_SEMAPHORE

    job = await _seed_job(db_session, admin_user)
    before = _PDF_SEMAPHORE._value
    await pdf_run.run_pdf_job(job.external_id)
    assert _PDF_SEMAPHORE._value == before

    real_build = overlay_plan.build_plan
    flipped: list[bool] = []

    def build_then_flip(*args, **kwargs):
        plan = real_build(*args, **kwargs)
        flipped.append(True)
        return plan

    real_current = pdf_run._current_generation
    monkeypatch.setattr(pdf_run, "build_plan", build_then_flip)
    monkeypatch.setattr(pdf_run, "_current_generation",
                        lambda eid: (-999 if flipped else real_current(eid)))
    job.status = "queued"
    await db_session.commit()
    with pytest.raises(asyncio.CancelledError):
        await pdf_run.run_pdf_job(job.external_id)
    assert _PDF_SEMAPHORE._value == before, "취소 경로에서도 정확히 한 번만 반납"


async def test_late_task_write_is_suppressed_by_generation_guard(
        db_session, admin_user):
    """취소 라우트가 확정한 상태를 뒤늦게 끝난 태스크가 덮어쓰지 않는다.

    순서에 의존하지 않는 단언이다 — 세대를 먼저 밀어 두면 태스크의 쓰기는
    언제 도착하든 억제된다.
    """
    from apps.server.domain.pdf_translate.pdf_tasks import (
        _bump_generation,
        _set_status_if_current,
    )

    job = await _seed_job(db_session, admin_user)
    # ⚠ expire_all() 뒤에 ORM 속성을 읽으면 만료 속성의 동기 재로드가 일어나
    # aiosqlite에서 MissingGreenlet이 난다(test_pdf_run.py:56 주석) — 미리 캡처.
    eid = job.external_id
    stale_generation = _bump_generation(eid)
    _bump_generation(eid)                      # 취소가 세대를 한 번 더 민다

    wrote = await _set_status_if_current(
        eid, stale_generation, "done", error="늦은 쓰기")
    assert wrote is False
    db_session.expire_all()
    from sqlalchemy import select
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.external_id == eid))).scalar_one()
    assert row.status == "queued", "억제됐어야 한다"


async def test_startup_sweep_recovers_job_with_intact_output(
        db_session, admin_user):
    """중단된 재굽기: 멀쩡한 번역본이 있으면 `done`으로 되살린다.

    `error`로 굳히면 /download가 영구 409가 되고 편집·rebake가 막히며, 그 상태는
    in-flight가 아니라서 다음 업로드의 프루닝이 폴더를 통째로 지운다.
    """
    job = await _seed_job(db_session, admin_user)
    eid = job.external_id       # expire_all() 뒤 속성 접근 회피(위 주석 참조)
    await pdf_run.run_pdf_job(eid)
    db_session.expire_all()
    from sqlalchemy import select
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.external_id == eid))).scalar_one()
    translated = row.translated_path
    row.status = "overlaying"          # 굽는 중에 앱이 죽은 상태
    await db_session.commit()

    await pdf_run.fail_inflight_pdf_jobs_at_startup()

    db_session.expire_all()
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.external_id == eid))).scalar_one()
    assert row.status == "done" and row.progress == 100
    assert "다시 굽기" in (row.error or "")
    assert Path(translated).exists()


async def test_startup_sweep_does_not_promote_truncated_output(
        db_session, admin_user):
    """`doc.save` 도중 죽은 상황: 잘린 산출물은 `.tmp`로만 존재하므로
    승격되지 않고, 스윕이 그 tmp를 정리한다.

    이 성질이 성립하는 이유는 두 저장 경로(최초 번역·재굽기)가 **모두**
    tmp+os.replace를 쓰기 때문이다. 제자리 저장이 하나라도 남으면 잘린 PDF가
    `done`으로 승격돼 다운로드 200이 나간다.
    """
    job = await _seed_job(db_session, admin_user)
    eid = job.external_id       # expire_all() 뒤 속성 접근 회피(위 주석 참조)
    job_dir = pdf_job_dir(eid)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "translated.pdf.tmp").write_bytes(b"%PDF-1.7 truncated")
    job.status = "overlaying"
    await db_session.commit()

    await pdf_run.fail_inflight_pdf_jobs_at_startup()

    db_session.expire_all()
    from sqlalchemy import select
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.external_id == eid))).scalar_one()
    assert row.status == "error", "완결된 번역본이 없으므로 승격 금지"
    assert not (job_dir / "translated.pdf.tmp").exists(), "tmp는 정리된다"
