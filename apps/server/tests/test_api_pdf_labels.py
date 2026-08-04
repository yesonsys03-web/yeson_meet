"""S4 게이트 — 편집 API.

지켜야 할 성질 셋: **수동 라벨의 rect는 저장되지 않는다**(주소만 저장한다),
**진행 중에는 아무도 편집하지 못한다**(파이프라인이 계획을 다시 쓴다),
**정리 버튼은 사람의 라벨을 건드리지 않는다**.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from apps.server.api.v1 import pdf_jobs as api_pdf
from apps.server.db.models import PdfJob
from apps.server.domain.pdf_translate import overlay_plan, pdf_run
from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir


def _make_three_panel_pdf(dest: Path) -> None:
    """3단 판넬 + 필드 라벨 — 판넬 주소와 계획이 둘 다 필요한 테스트용."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1008, height=612)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 629, 354), False)
    pix.clear_with(220)
    for x0 in (38.1, 353.5, 668.9):
        page.insert_image(fitz.Rect(x0, 110.9, x0 + 302.1, 279.2), pixmap=pix)
    page.insert_text((72, 460), "Dialog", fontsize=8)
    page.insert_text((72, 478), "If you wanna go, then go.", fontsize=10)
    page.insert_text((72, 560), "Action Notes", fontsize=8)
    page.insert_text((72, 578), "HANK walks to the door.", fontsize=10)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dest)
    doc.close()


class FakeTranslator:
    async def translate_batch(self, texts):
        return [f"번역{i}" for i, _ in enumerate(texts)]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(pdf_run, "create_translator",
                        lambda provider, cli_model, prompt_builder: FakeTranslator())
    monkeypatch.setattr(api_pdf, "_prune_old_jobs", lambda: None)
    yield


async def _baked_job(db_session, admin_user):
    """실제로 한 번 구워 계획이 있는 완료 작업."""
    eid = uuid4()
    src = pdf_job_dir(eid) / "source.pdf"
    _make_three_panel_pdf(src)
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="sb.pdf", status="queued", source_path=str(src))
    db_session.add(job)
    await db_session.commit()
    await pdf_run.run_pdf_job(eid)
    db_session.expire_all()
    return eid


def _edits_raw(eid) -> dict:
    return json.loads((pdf_job_dir(eid) / "label_edits.json").read_text())


async def _add_label(client, eid, *, version=0, page=0, panel=0, text="들어온다"):
    return await client.post(f"/api/v1/pdf-jobs/{eid}/labels", json={
        "page": page, "panel_index": panel, "rel": [0.1, 0.1],
        "source_text": "IN", "text": text, "edits_version": version})


# ── 왕복 보존 + stale ─────────────────────────────────────────────────────────

async def test_manual_label_roundtrip_and_marks_stale(client, db_session, admin_user):
    eid = await _baked_job(db_session, admin_user)
    before = await client.get(f"/api/v1/pdf-jobs/{eid}/labels")
    assert before.status_code == 200
    assert before.json()["stale"] is False, "막 구웠으니 최신이다"

    resp = await _add_label(client, eid)
    assert resp.status_code == 201, resp.text
    assert resp.json()["edits_version"] == 1

    after = await client.get(f"/api/v1/pdf-jobs/{eid}/labels")
    body = after.json()
    manual = [i for i in body["items"] if i["origin"] == "manual"]
    assert len(manual) == 1
    assert manual[0]["text"] == "들어온다"
    assert manual[0]["panel_index"] == 0
    # 편집이 아직 구워지지 않았으므로 번역본은 뒤처져 있다.
    assert body["stale"] is True
    assert body["edits_version"] == 1


# ── 409 두 종류 ──────────────────────────────────────────────────────────────

async def test_stale_edits_version_conflicts(client, db_session, admin_user):
    eid = await _baked_job(db_session, admin_user)
    assert (await _add_label(client, eid, version=0)).status_code == 201
    late = await _add_label(client, eid, version=0)   # 옛 버전으로 다시
    assert late.status_code == 409
    assert "먼저 저장" in late.json()["detail"]


async def test_mutation_blocked_while_job_in_flight(
        client, db_session, admin_user):
    """진행 중에는 편집을 막는다 — 파이프라인이 계획을 다시 쓰는 구간이다."""
    eid = await _baked_job(db_session, admin_user)
    from sqlalchemy import select
    row = (await db_session.execute(
        select(PdfJob).where(PdfJob.external_id == eid))).scalar_one()
    row.status = "translating"
    await db_session.commit()

    resp = await _add_label(client, eid)
    assert resp.status_code == 409
    assert "진행 중" in resp.json()["detail"]


# ── 수동 라벨의 rect는 저장되지 않는다 ────────────────────────────────────────

async def test_patch_rect_stores_relative_address_not_absolute(
        client, db_session, admin_user):
    """AC11의 근간 — 절대 좌표를 저장하면 재번역 후에도 옛 자리에 찍힌다."""
    eid = await _baked_job(db_session, admin_user)
    created = await _add_label(client, eid)
    item_id = created.json()["id"]

    # 1번 판넬은 x 38.1~340.2, y 110.9~279.2 — 그 안의 한 점으로 옮긴다.
    resp = await client.patch(
        f"/api/v1/pdf-jobs/{eid}/labels/{item_id}",
        json={"rect": [200.0, 200.0, 280.0, 220.0], "edits_version": 1})
    assert resp.status_code == 200, resp.text

    raw = _edits_raw(eid)["manual"][0]
    assert "rect" not in raw, "절대 좌표가 저장되면 안 된다"
    assert 0.0 <= raw["rel"][0] <= 1.0 and 0.0 <= raw["rel"][1] <= 1.0
    assert raw["rel"] != [0.1, 0.1], "새 위치가 반영돼야 한다"


async def test_patch_rect_outside_panel_is_rejected(
        client, db_session, admin_user):
    eid = await _baked_job(db_session, admin_user)
    item_id = (await _add_label(client, eid)).json()["id"]
    resp = await client.patch(
        f"/api/v1/pdf-jobs/{eid}/labels/{item_id}",
        json={"rect": [900.0, 500.0, 980.0, 520.0], "edits_version": 1})
    assert resp.status_code == 422
    assert "판넬 밖" in resp.json()["detail"]


async def test_patch_non_editable_kind_is_rejected(
        client, db_session, admin_user):
    """v1의 편집 대상은 판넬 라벨뿐 — dialog/action은 목록에 읽기 전용으로 보인다."""
    eid = await _baked_job(db_session, admin_user)
    plan = overlay_plan.load_plan(pdf_job_dir(eid))
    target = next(i for i in plan.items if i.kind in ("dialog", "action"))
    resp = await client.patch(
        f"/api/v1/pdf-jobs/{eid}/labels/{target.id}",
        json={"text": "바꾸기", "edits_version": 0})
    assert resp.status_code == 422
    assert "편집할 수 없" in resp.json()["detail"]


# ── 해독 미리보기 ────────────────────────────────────────────────────────────

async def test_decode_preview_success_and_failure(client):
    ok = await client.post("/api/v1/pdf-jobs/decode-panel-label",
                           json={"texts": ["IN"]})
    assert ok.status_code == 200
    assert ok.json()["lines"], "판넬 약어는 해독돼야 한다"

    nope = await client.post("/api/v1/pdf-jobs/decode-panel-label",
                             json={"texts": ["CAMERA FIELD GUIDE"]})
    assert nope.status_code == 200
    assert nope.json()["lines"] is None, "해독 못 하면 null — 사람이 직접 친다"


# ── 정리 버튼은 사람의 라벨을 건드리지 않는다 ─────────────────────────────────

async def test_purge_dangling_never_touches_manual_labels(
        client, db_session, admin_user):
    """무효 항목 정리는 **override 전용**이다.

    주소를 잃은 수동 라벨까지 지우면, 판넬이 되돌아오면 자동 복귀할 예정이던
    사람의 라벨이 영구 소멸한다(버전 파일 누적은 Non-Goal이라 복구 수단이 없다).
    """
    eid = await _baked_job(db_session, admin_user)
    await _add_label(client, eid, version=0)
    # 주소를 잃은 수동 라벨 + 계획에 없는 override를 함께 만든다.
    job_dir = pdf_job_dir(eid)
    edits = overlay_plan.load_edits(job_dir, job_id=str(eid))
    edits = overlay_plan.upsert_override(edits, "존재하지않는id", page=0,
                                         text="유령")
    edits = overlay_plan.repoint_manual(edits, edits.manual[0].id, page=0,
                                        panel_index=99)
    overlay_plan.save_edits(job_dir, edits)

    resp = await client.post(f"/api/v1/pdf-jobs/{eid}/labels/purge-dangling",
                             json={"edits_version": edits.edits_version})
    assert resp.status_code == 200, resp.text
    assert resp.json()["manual_count"] == 1, "수동 라벨은 살아남는다"

    after = overlay_plan.load_edits(job_dir, job_id=str(eid))
    assert [m.id for m in after.manual] == [edits.manual[0].id]
    assert after.overrides == [], "dangling override만 사라진다"


async def test_repoint_recovers_orphan_manual_label(
        client, db_session, admin_user):
    """주소를 잃은 라벨의 유일한 조치가 '삭제'면 안 된다 — 재지정이 있어야 한다."""
    eid = await _baked_job(db_session, admin_user)
    item_id = (await _add_label(client, eid)).json()["id"]
    job_dir = pdf_job_dir(eid)
    edits = overlay_plan.repoint_manual(
        overlay_plan.load_edits(job_dir, job_id=str(eid)), item_id,
        page=0, panel_index=99)
    overlay_plan.save_edits(job_dir, edits)

    body = (await client.get(f"/api/v1/pdf-jobs/{eid}/labels")).json()
    assert [u["id"] for u in body["unresolved"]] == [item_id]

    resp = await client.patch(
        f"/api/v1/pdf-jobs/{eid}/labels/{item_id}/panel",
        json={"page": 0, "panel_index": 2,
              "edits_version": body["edits_version"]})
    assert resp.status_code == 200, resp.text

    body = (await client.get(f"/api/v1/pdf-jobs/{eid}/labels")).json()
    assert body["unresolved"] == [], "복구됐어야 한다"
    manual = [i for i in body["items"] if i["origin"] == "manual"]
    assert manual[0]["panel_index"] == 2


# ── 프루닝이 사람의 편집을 지우지 않는다 ──────────────────────────────────────

async def test_retention_pins_jobs_with_manual_edits(
        client, db_session, admin_user):
    """업로드 시·시작 시 프루닝의 **유일 경로**를 한 번에 잠근다.

    막지 않으면 스토리보드를 묶음 업로드하는 순간(또는 재시작 한 번에) 두 시간
    걸려 넣은 라벨이 경고 없이 폴더째 사라진다.
    """
    eid = await _baked_job(db_session, admin_user)
    await _add_label(client, eid, version=0)

    deleted = await pdf_run.prune_old_pdf_jobs(keep=0)
    assert deleted == 0, "편집이 있는 작업은 고정된다"
    assert (pdf_job_dir(eid) / "label_edits.json").exists()

    from sqlalchemy import select
    db_session.expire_all()
    assert (await db_session.execute(
        select(PdfJob).where(PdfJob.external_id == eid))).scalar_one_or_none()


async def test_retention_still_prunes_jobs_without_edits(
        client, db_session, admin_user):
    """고정은 **내용 기준**이다 — 편집이 없으면 평소대로 정리된다."""
    eid = await _baked_job(db_session, admin_user)
    assert await pdf_run.prune_old_pdf_jobs(keep=0) == 1
    assert not pdf_job_dir(eid).exists()


# ── 목록 라우트는 계획 파일을 열지 않는다 ─────────────────────────────────────

async def test_job_list_never_opens_overlay_plan(
        client, db_session, admin_user, monkeypatch):
    """1.5초 폴링 경로에서 400KB급 계획을 파싱하면 이벤트 루프가 멎는다.

    이 라우트는 소형 `plan_status.json`과 `label_edits.json`만 읽어야 한다.
    """
    eid = await _baked_job(db_session, admin_user)
    await _add_label(client, eid, version=0)

    def _boom(*a, **kw):
        raise AssertionError("목록 라우트가 계획 파일을 열었다")

    monkeypatch.setattr(api_pdf, "load_plan", _boom)
    resp = await client.get("/api/v1/pdf-jobs")
    assert resp.status_code == 200
    item = next(i for i in resp.json()["items"] if i["job_id"] == str(eid))
    assert item["has_edits"] is True
    assert item["stale"] is True
