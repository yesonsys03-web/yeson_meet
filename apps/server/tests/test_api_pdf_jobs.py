from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from apps.server.api.v1 import pdf_jobs as api_pdf
from apps.server.db.models import PdfJob
from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir, pdf_jobs_root


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(api_pdf, "_start_pdf_pipeline", lambda eid: None)
    # 리텐션 프루닝은 fire-and-forget 태스크라 테스트 이벤트 루프/세션 수명
    # 밖에서 돌면 잡음이 된다 — 배선 자체는 아래 전용 테스트가 잠근다
    # (test_api_video_jobs.py:21과 같은 처리).
    monkeypatch.setattr(api_pdf, "_prune_old_jobs", lambda: None)
    yield


async def test_upload_triggers_retention_prune(client, admin_user, monkeypatch):
    """업로드 직후 프루닝이 호출돼야 한다 — _env가 이 심을 무력화하므로,
    배선이 빠져도 다른 테스트는 아무도 안 깨진다(그래서 전용 테스트)."""
    called: list[bool] = []
    monkeypatch.setattr(api_pdf, "_prune_old_jobs", lambda: called.append(True))
    resp = await client.post(
        "/api/v1/pdf-jobs/upload",
        files={"file": ("a.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 201
    assert called == [True]


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
        data={"title": "콘티", "translate_provider": "claude"},
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


async def test_features_reports_enabled_formats(client, admin_user, monkeypatch):
    """서버가 권위 — 콘솔이 끈 포맷을 클라이언트가 조회한다."""
    # 개발 셸에 스위치가 켜져 있어도 기준선은 "둘 다 켜짐"이어야 한다.
    monkeypatch.delenv("YESON_PDF_STORYBOARD_ENABLED", raising=False)
    monkeypatch.delenv("YESON_PDF_XSHEET_ENABLED", raising=False)
    resp = await client.get("/api/v1/pdf-jobs/features")
    assert resp.status_code == 200
    assert resp.json() == {"formats": {"storyboard": True, "xsheet": True},
                           "default_provider": "claude",
                           "blocked_providers": ["gemini"]}
    monkeypatch.setenv("YESON_PDF_XSHEET_ENABLED", "0")
    assert (await client.get("/api/v1/pdf-jobs/features")).json()["formats"] == {
        "storyboard": True, "xsheet": False}


async def test_upload_rejects_disabled_format_only(client, admin_user,
                                                   monkeypatch):
    monkeypatch.setenv("YESON_PDF_XSHEET_ENABLED", "0")
    resp = await client.post(
        "/api/v1/pdf-jobs/upload",
        data={"format_hint": "xsheet"},
        files={"file": ("a.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 403
    assert "비활성화" in resp.json()["detail"]
    # 다른 포맷은 그대로 동작해야 한다(전체 잠금 아님)
    ok = await client.post(
        "/api/v1/pdf-jobs/upload",
        data={"format_hint": "storyboard"},
        files={"file": ("b.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert ok.status_code == 201


async def test_rebake_rejects_disabled_format(client, admin_user, db_session,
                                              monkeypatch):
    """기존 잡의 조회·다운로드는 허용하되 재굽기(=생산)는 막는다."""
    monkeypatch.setenv("YESON_PDF_XSHEET_ENABLED", "0")
    job = PdfJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                 source_ref="a.pdf", status="done", progress=100,
                 format="xsheet")
    db_session.add(job)
    await db_session.commit()
    resp = await client.post(f"/api/v1/pdf-jobs/{job.external_id}/rebake")
    assert resp.status_code == 403
    assert "비활성화" in resp.json()["detail"]


async def test_retranslate_rejects_disabled_format_and_keeps_done(
        client, admin_user, db_session, monkeypatch):
    """retranslate는 파이프라인을 다시 띄우므로 rebake와 같은 문턱이 필요하다.
    문턱이 없으면 queued로 바꾼 뒤 파이프라인이 오류로 끝내 — download·rebake·
    retranslate가 전부 done을 요구하므로 — 잡이 영구 409가 된다."""
    monkeypatch.setenv("YESON_PDF_XSHEET_ENABLED", "0")
    job = PdfJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                 source_ref="a.pdf", status="done", progress=100,
                 format="xsheet")
    db_session.add(job)
    await db_session.commit()
    resp = await client.post(f"/api/v1/pdf-jobs/{job.external_id}/retranslate")
    assert resp.status_code == 403
    assert "비활성화" in resp.json()["detail"]
    await db_session.refresh(job)
    assert job.status == "done"


async def test_upload_rejects_gemini_provider(client, admin_user, db_session):
    """gemini는 API 과금이라 PDF 번역에서 제외 — 업로드 단계에서 거절한다.
    거절은 디스크 저장·행 생성보다 앞이어야 한다(고아 source.pdf 방지)."""
    resp = await client.post(
        "/api/v1/pdf-jobs/upload",
        data={"translate_provider": "gemini"},
        files={"file": ("a.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 422
    assert "Gemini" in resp.json()["detail"]
    assert (await db_session.execute(select(PdfJob))).scalars().all() == []
    assert not list(pdf_jobs_root().glob("*/source.pdf"))


async def test_upload_records_resolved_provider(client, admin_user, db_session):
    """엔진 미지정이면 기본(claude)을 행에 남긴다 — 목록이 실제 쓴 엔진을
    보여야 하고, 파이프라인 env 기본(gemini)으로 새면 안 된다."""
    resp = await client.post(
        "/api/v1/pdf-jobs/upload",
        files={"file": ("a.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 201
    row = (await db_session.execute(select(PdfJob).where(
        PdfJob.external_id == UUID(resp.json()["job_id"])))).scalar_one()
    assert row.translate_provider == "claude"

    other = await client.post(
        "/api/v1/pdf-jobs/upload",
        data={"translate_provider": "codex"},
        files={"file": ("b.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert other.status_code == 201
    row2 = (await db_session.execute(select(PdfJob).where(
        PdfJob.external_id == UUID(other.json()["job_id"])))).scalar_one()
    assert row2.translate_provider == "codex"


async def test_retranslate_coerces_gemini_provider(client, admin_user,
                                                   db_session):
    """옛 gemini 잡은 거절이 아니라 기본 엔진으로 바꿔 진행한다 —
    재업로드가 필요 없고 API 비용도 0이다(사용자 결정)."""
    eid = uuid4()
    src = pdf_job_dir(eid) / "source.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(_tiny_pdf_bytes())
    job = PdfJob(external_id=eid, owner_user_id=admin_user.id, title="t",
                 source_ref="a.pdf", status="done", progress=100,
                 format="storyboard", translate_provider="gemini",
                 source_path=str(src))
    db_session.add(job)
    await db_session.commit()
    job_pk = job.id  # expire_all() 뒤 속성 재조회 우회 (위 주석 참고)

    resp = await client.post(f"/api/v1/pdf-jobs/{eid}/retranslate")

    assert resp.status_code == 200 and resp.json() == {"status": "queued"}
    db_session.expire_all()
    row = (await db_session.execute(select(PdfJob).where(
        PdfJob.id == job_pk))).scalar_one()
    assert row.translate_provider == "claude"


async def test_features_reports_provider_policy(client, admin_user):
    """엔진 정책도 서버가 권위 — 클라이언트가 목록에서 gemini를 빼는 근거."""
    body = (await client.get("/api/v1/pdf-jobs/features")).json()
    assert body["default_provider"] == "claude"
    assert body["blocked_providers"] == ["gemini"]
