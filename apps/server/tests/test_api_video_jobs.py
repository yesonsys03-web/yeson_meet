from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from apps.server.api.v1 import video_jobs as api_vj
from apps.server.db.models import VideoJob, VideoSegment
from apps.server.domain.video_captions import pipeline as pl


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    # 파이프라인 자동 시작 차단 — API 계층만 검증
    monkeypatch.setattr(api_vj, "_start_pipeline", lambda external_id: None)
    # 리텐션 프루닝도 차단 — 프루닝 로직은 test_video_pipeline에서 직접 검증한다.
    monkeypatch.setattr(api_vj, "_prune_old_jobs", lambda: None)
    yield


async def _install_model(name: str = "small"):
    from apps.server.domain.video_captions.whisper_models import model_dir
    d = model_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.bin").write_bytes(b"x")


async def test_storage_usage_offloads_blocking_walk(client, monkeypatch):
    """디스크 트리 walk가 이벤트 루프를 막지 않아야 한다 — blocking _tree_size를
    asyncio.to_thread로 오프로드하므로 워커 스레드(≠루프 스레드)에서 실행된다.
    회귀(async 핸들러에서 직접 rglob)면 루프가 막혀 폴링 요청들이 DB 커넥션을 쥔 채
    정지→QueuePool 고갈→앱 얼음(실기 Windows 로그의 근본원인)."""
    import threading
    main_ident = threading.get_ident()
    captured: dict = {}

    def _spy_tree(_root):
        captured["ident"] = threading.get_ident()
        return 4242

    monkeypatch.setattr(api_vj, "_tree_size", _spy_tree)
    resp = await client.get("/api/v1/video-jobs/storage")
    assert resp.status_code == 200
    assert resp.json()["total_bytes"] == 4242
    # 루프 스레드가 아니라 워커 스레드에서 walk가 돌았는지 — 오프로드 증명.
    assert captured["ident"] != main_ident


async def test_create_youtube_job(client, admin_user, db_session):
    await _install_model()
    resp = await client.post("/api/v1/video-jobs",
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "small"})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    row = (await db_session.execute(
        select(VideoJob).where(VideoJob.external_id == job_id))).scalar_one()
    assert row.source_type == "youtube"
    assert row.status == "queued"


async def test_create_job_rejects_missing_model(client):
    resp = await client.post("/api/v1/video-jobs",
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "medium"})
    assert resp.status_code == 409
    assert "다운로드" in resp.json()["detail"]


async def test_upload_job_saves_file(client, admin_user, db_session, tmp_path):
    await _install_model()
    resp = await client.post(
        "/api/v1/video-jobs/upload",
        data={"whisper_model": "small", "title": "클립"},
        files={"file": ("clip.mp4", b"fake-video-bytes", "video/mp4")},
    )
    assert resp.status_code == 201
    row = (await db_session.execute(select(VideoJob).where(
        VideoJob.external_id == resp.json()["job_id"]))).scalar_one()
    assert row.media_path and Path(row.media_path).read_bytes() == b"fake-video-bytes"
    assert row.title == "클립"


async def test_detail_includes_segments_and_patch_edits(client, db_session, admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.flush()
    job_id = job.id
    db_session.add(VideoSegment(job_id=job_id, seq=1, start_ms=0, end_ms=900,
                                text_en="Hi", text_ko="안녕"))
    await db_session.commit()

    detail = await client.get(f"/api/v1/video-jobs/{job.external_id}")
    assert detail.status_code == 200
    assert detail.json()["segments"][0]["text_ko"] == "안녕"

    patched = await client.patch(
        f"/api/v1/video-jobs/{job.external_id}/segments",
        json={"edits": [{"seq": 1, "text_ko": "안녕하세요!"}]})
    assert patched.status_code == 200
    db_session.expire_all()
    seg = (await db_session.execute(select(VideoSegment).where(
        VideoSegment.job_id == job_id))).scalar_one()
    assert seg.text_ko == "안녕하세요!"


async def test_burn_requires_review_status(client, db_session,
                                           admin_user, monkeypatch):
    started = {}
    monkeypatch.setattr(api_vj, "_start_burn",
                        lambda eid, p, m, f, c: started.setdefault("eid", eid))
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="transcribing")
    db_session.add(job)
    await db_session.commit()

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/burn",
                             json={"position": "bottom", "margin_v": 40,
                                   "font_size": 18})
    assert resp.status_code == 409

    job.status = "review"
    await db_session.commit()
    job_id = job.id
    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/burn",
                             json={"position": "bottom", "margin_v": 40,
                                   "font_size": 18})
    assert resp.status_code == 202
    assert started["eid"] == job.external_id
    db_session.expire_all()
    burning = (await db_session.execute(select(VideoJob).where(
        VideoJob.id == job_id))).scalar_one()
    assert burning.status == "burning"
    assert burning.progress == 0


async def test_burn_forwards_color_to_start_burn(client, db_session,
                                                 admin_user, monkeypatch):
    started = {}
    monkeypatch.setattr(
        api_vj, "_start_burn",
        lambda eid, p, m, f, c: started.update(eid=eid, color=c))
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.commit()

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/burn",
                             json={"position": "bottom", "margin_v": 40,
                                   "font_size": 18, "color": "#FF0000"})
    assert resp.status_code == 202
    assert started["eid"] == job.external_id
    assert started["color"] == "#FF0000"


async def test_burn_rejects_invalid_color(client, db_session, admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.commit()

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/burn",
                             json={"position": "bottom", "margin_v": 40,
                                   "font_size": 18, "color": "red"})
    assert resp.status_code == 422


async def test_media_is_capability_url_no_auth(client, db_session, admin_user,
                                               tmp_path):
    media = tmp_path / "preview.mp4"
    media.write_bytes(b"stream-me")
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4", whisper_model="small",
                   status="review", preview_path=str(media))
    db_session.add(job)
    await db_session.commit()

    resp = await client.get(f"/api/v1/video-jobs/{job.external_id}/media")
    assert resp.status_code == 200
    assert resp.content == b"stream-me"


async def test_srt_download(client, db_session, admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.flush()
    db_session.add(VideoSegment(job_id=job.id, seq=1, start_ms=0, end_ms=1000,
                                text_en="Hi", text_ko="안녕"))
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/video-jobs/{job.external_id}/download?kind=srt")
    assert resp.status_code == 200
    assert "안녕" in resp.text
    assert "00:00:00,000 --> 00:00:01,000" in resp.text


async def test_upload_cleans_up_on_failure(client, monkeypatch):
    await _install_model()

    async def boom(upload, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"partial")
        raise RuntimeError("disk full")

    monkeypatch.setattr(api_vj, "save_upload", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        await client.post(
            "/api/v1/video-jobs/upload",
            data={"whisper_model": "small"},
            files={"file": ("clip.mp4", b"x", "video/mp4")},
        )
    jobs_root = Path(os.environ["STORAGE_ROOT"]) / "video_jobs"
    assert not jobs_root.exists() or not any(jobs_root.iterdir())


async def test_delete_cancels_running_pipeline(client, db_session, admin_user, monkeypatch):
    cancelled = {}
    monkeypatch.setattr(api_vj, "cancel_job_task",
                        lambda ext: cancelled.setdefault("ext", ext) or True)
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4", whisper_model="small",
                   status="transcribing")
    db_session.add(job)
    await db_session.commit()

    resp = await client.delete(f"/api/v1/video-jobs/{job.external_id}")
    assert resp.status_code == 204
    assert cancelled["ext"] == job.external_id  # running task cancelled on delete


async def test_no_auth_required_for_list(client, admin_user):
    resp = await client.get("/api/v1/video-jobs")
    assert resp.status_code == 200


async def test_create_youtube_job_saves_translate_provider(client, admin_user, db_session):
    await _install_model()
    resp = await client.post("/api/v1/video-jobs",
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "small",
                                   "translate_provider": "claude"})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    row = (await db_session.execute(
        select(VideoJob).where(VideoJob.external_id == job_id))).scalar_one()
    assert row.translate_provider == "claude"

    listed = await client.get("/api/v1/video-jobs")
    item = next(j for j in listed.json()["items"] if j["job_id"] == job_id)
    assert item["translate_provider"] == "claude"


async def test_create_youtube_job_accepts_apple_translate_provider(client, admin_user, db_session):
    # 배선 회귀 가드: translate-engines가 "apple"을 노출하는데도 생성 엔드포인트의
    # 패턴 검증이 이를 빠뜨리면 UI에서 apple 선택 시 422가 난다(E2E에서 발견).
    await _install_model()
    resp = await client.post("/api/v1/video-jobs",
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "small",
                                   "translate_provider": "apple"})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    row = (await db_session.execute(
        select(VideoJob).where(VideoJob.external_id == job_id))).scalar_one()
    assert row.translate_provider == "apple"


async def test_create_youtube_job_accepts_apple_hifi_translate_provider(client, admin_user, db_session):
    # apple_hifi (Apple 고품질·느림 전략)도 apple과 같은 배선 회귀 가드가 필요하다.
    await _install_model()
    resp = await client.post("/api/v1/video-jobs",
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "small",
                                   "translate_provider": "apple_hifi"})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    row = (await db_session.execute(
        select(VideoJob).where(VideoJob.external_id == job_id))).scalar_one()
    assert row.translate_provider == "apple_hifi"


async def test_create_youtube_job_accepts_qwen_hifi_translate_provider(client, admin_user, db_session):
    # qwen 계열(MLX 로컬)도 apple과 같은 배선 회귀 가드가 필요하다. PR#50이 엔진목록엔
    # qwen을 넣었지만 생성 패턴 갱신을 빠뜨려 실리콘맥에서 선택 시 422가 났다(2026-07-14).
    await _install_model()
    resp = await client.post("/api/v1/video-jobs",
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "small",
                                   "translate_provider": "qwen_hifi"})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]
    row = (await db_session.execute(
        select(VideoJob).where(VideoJob.external_id == job_id))).scalar_one()
    assert row.translate_provider == "qwen_hifi"


def test_create_pattern_accepts_every_listed_engine_value():
    # 포괄 드리프트 가드: 생성 검증 패턴이 translate-engines의 모든 값을 수용해야 한다.
    # 패턴을 하드코딩해 엔진 추가 때 빠뜨리면 UI 선택 시 422 — 이 테스트가 그걸 막는다.
    import re

    from apps.server.api.v1.video_jobs import _TRANSLATE_PROVIDER_PATTERN
    from apps.server.domain.video_captions.translate_cli import list_translate_engines

    rx = re.compile(_TRANSLATE_PROVIDER_PATTERN)
    for engine in list_translate_engines():
        assert rx.fullmatch(engine["value"]), (
            f"provider {engine['value']}가 생성 검증 패턴에서 거부됨")
    assert rx.fullmatch("bogus_provider") is None


async def test_translate_engines_endpoint(client, admin_user):
    resp = await client.get("/api/v1/video-jobs/translate-engines")
    assert resp.status_code == 200
    engines = resp.json()["engines"]
    assert len(engines) == 10
    assert {e["value"] for e in engines} == {
        "gemini", "claude", "codex", "agy", "opencode", "apple", "apple_hifi",
        "qwen", "qwen_lite", "qwen_hifi"}
    for engine in engines:
        assert "label" in engine
        assert isinstance(engine["available"], bool)


async def test_translate_engines_route_does_not_shadow_detail_route(client, db_session, admin_user):
    # "translate-engines"가 /{external_id} 동적 라우트보다 먼저 선언돼야 UUID로
    # 오인 파싱되지 않는다 — test_detail_includes_segments_and_patch_edits가
    # 상세 조회 자체는 이미 검증하므로, 여기서는 정적 라우트가 200을 반환하는지만 확인.
    resp = await client.get("/api/v1/video-jobs/translate-engines")
    assert resp.status_code == 200


async def test_list_with_sizes_adds_per_job_bytes(client, db_session, admin_user):
    from apps.server.domain.video_captions.pipeline import job_dir
    ext = uuid4()
    d = job_dir(ext)  # STORAGE_ROOT is tmp_path via the _env fixture
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"y" * 250)
    job = VideoJob(external_id=ext, owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4", whisper_model="small",
                   status="done")
    db_session.add(job)
    await db_session.commit()

    # default: no size_bytes (client hot-poll path stays cheap)
    plain = await client.get("/api/v1/video-jobs")
    assert plain.status_code == 200
    assert "size_bytes" not in plain.json()["items"][0]

    # opt-in: server console asks for per-job folder sizes
    sized = await client.get("/api/v1/video-jobs?with_sizes=true")
    assert sized.status_code == 200
    item = next(j for j in sized.json()["items"] if j["job_id"] == str(ext))
    assert item["size_bytes"] >= 250


async def test_storage_endpoint_reports_usage(client, db_session, admin_user):
    from apps.server.domain.video_captions.pipeline import job_dir
    ext = uuid4()
    d = job_dir(ext)  # STORAGE_ROOT is tmp_path via the _env fixture
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x" * 100)
    job = VideoJob(external_id=ext, owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4", whisper_model="small",
                   status="done")
    db_session.add(job)
    await db_session.commit()

    resp = await client.get("/api/v1/video-jobs/storage")
    assert resp.status_code == 200  # static route not shadowed by /{external_id}
    body = resp.json()
    assert body["total_bytes"] >= 100
    assert body["job_count"] == 1
    assert body["keep"] == 30


async def test_create_job_rejects_invalid_translate_provider(client, admin_user):
    await _install_model()
    resp = await client.post("/api/v1/video-jobs",
                             json={"youtube_url": "https://youtu.be/abc",
                                   "whisper_model": "small",
                                   "translate_provider": "not-a-real-engine"})
    assert resp.status_code == 422


async def test_rebuild_resets_job_and_requeues(client, db_session, admin_user,
                                               monkeypatch, tmp_path):
    started = {}
    monkeypatch.setattr(api_vj, "_start_pipeline",
                        lambda eid: started.setdefault("eid", eid))
    src = tmp_path / "source.mov"
    src.write_bytes(b"x" * 10)
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mov",
                   whisper_model="base", translate_provider="claude",
                   status="done", progress=100, media_path=str(src),
                   burned_path="/tmp/old-burned.mp4", error=None)
    db_session.add(job)
    await db_session.commit()
    job_id = job.id

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/rebuild")
    assert resp.status_code == 202
    assert started["eid"] == job.external_id
    db_session.expire_all()
    row = (await db_session.execute(select(VideoJob).where(
        VideoJob.id == job_id))).scalar_one()
    assert row.status == "queued"
    assert row.progress == 0
    assert row.burned_path is None
    # 옵션은 보존 — 같은 소스·같은 설정으로 재실행
    assert row.whisper_model == "base"
    assert row.translate_provider == "claude"


async def test_rebuild_rejects_inflight_job(client, db_session, admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mov",
                   whisper_model="base", status="transcribing")
    db_session.add(job)
    await db_session.commit()
    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/rebuild")
    assert resp.status_code == 409


async def test_rebuild_rejects_upload_without_source_file(client, db_session,
                                                          admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mov",
                   whisper_model="base", status="done",
                   media_path="/nope/missing.mov")
    db_session.add(job)
    await db_session.commit()
    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/rebuild")
    assert resp.status_code == 409


async def test_cancel_inflight_job_marks_cancelled(client, db_session, admin_user):
    """취소는 실패(error)와 구분되는 '취소됨(cancelled)' 상태로 초기화된다."""
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mov",
                   whisper_model="base", status="translating", progress=40)
    db_session.add(job)
    await db_session.commit()
    job_id = job.id

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/cancel")
    assert resp.status_code == 202
    db_session.expire_all()
    row = (await db_session.execute(select(VideoJob).where(
        VideoJob.id == job_id))).scalar_one()
    assert row.status == "cancelled"
    assert row.progress == 0
    assert "취소" in (row.error or "")


async def test_cancel_rejects_finished_job(client, db_session, admin_user):
    for status in ("done", "cancelled"):
        job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                       source_type="upload", source_ref="c.mov",
                       whisper_model="base", status=status)
        db_session.add(job)
        await db_session.commit()
        resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/cancel")
        assert resp.status_code == 409


async def test_rebuild_from_cancelled_job_requeues(client, db_session, admin_user,
                                                   monkeypatch, tmp_path):
    """취소된 작업은 재생성 버튼의 대상 — cancelled에서 rebuild가 동작해야 한다."""
    started = {}
    monkeypatch.setattr(api_vj, "_start_pipeline",
                        lambda eid: started.setdefault("eid", eid))
    src = tmp_path / "source.mov"
    src.write_bytes(b"x" * 10)
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mov",
                   whisper_model="base", status="cancelled", progress=0,
                   media_path=str(src), error="사용자가 작업을 취소했습니다.")
    db_session.add(job)
    await db_session.commit()
    job_id = job.id

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/rebuild")
    assert resp.status_code == 202
    assert started["eid"] == job.external_id
    db_session.expire_all()
    row = (await db_session.execute(select(VideoJob).where(
        VideoJob.id == job_id))).scalar_one()
    assert row.status == "queued"
    assert row.error is None


async def test_cancel_all_queued_and_active(client, db_session, admin_user, monkeypatch):
    """대기열 전체 취소: queued를 먼저 선취소한 뒤 활성 작업을 취소한다(순서
    보장 — 세마포어가 반납되며 큐잉된 다음 작업이 새치기해서 취소를 피하는
    것을 막기 위해). cancel_job_task 자체는 pipeline 단위테스트에서 검증되므로
    여기서는 훅 호출 여부/순서/카운트만 monkeypatch로 확인한다."""
    calls: list = []
    monkeypatch.setattr(api_vj, "cancel_job_task", lambda ext: calls.append(ext) or True)

    queued_jobs = []
    for i in range(3):
        j = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title=f"q{i}",
                    source_type="upload", source_ref="c.mp4", whisper_model="base",
                    status="queued", progress=0)
        db_session.add(j)
        queued_jobs.append(j)
    active_job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="active",
                          source_type="upload", source_ref="c.mp4", whisper_model="base",
                          status="transcribing", progress=55)
    db_session.add(active_job)
    done_job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="done",
                        source_type="upload", source_ref="c.mp4", whisper_model="base",
                        status="done")
    db_session.add(done_job)
    await db_session.commit()

    resp = await client.post("/api/v1/video-jobs/cancel-all")
    assert resp.status_code == 200
    assert resp.json() == {"cancelled_queued": 3, "cancelled_active": 1}

    # 순서 보장: queued 세 건이 모두 먼저, active는 마지막
    queued_exts = {j.external_id for j in queued_jobs}
    assert set(calls[:3]) == queued_exts
    assert calls[3] == active_job.external_id

    db_session.expire_all()
    rows = {r.external_id: r for r in (await db_session.execute(
        select(VideoJob))).scalars()}
    for j in queued_jobs:
        row = rows[j.external_id]
        assert row.status == "cancelled"
        assert row.progress == 0
        assert "취소" in (row.error or "")
    active_row = rows[active_job.external_id]
    assert active_row.status == "cancelled"
    assert active_row.progress == 0
    assert "취소" in (active_row.error or "")
    assert rows[done_job.external_id].status == "done"  # 종료 상태는 건드리지 않음


async def test_cancel_all_when_only_queued(client, db_session, admin_user):
    for i in range(2):
        db_session.add(VideoJob(external_id=uuid4(), owner_user_id=admin_user.id,
                                title=f"q{i}", source_type="upload", source_ref="c.mp4",
                                whisper_model="base", status="queued"))
    await db_session.commit()

    resp = await client.post("/api/v1/video-jobs/cancel-all")
    assert resp.status_code == 200
    assert resp.json() == {"cancelled_queued": 2, "cancelled_active": 0}


async def test_cancel_all_noop_when_nothing_inflight(client, db_session, admin_user):
    db_session.add(VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="d",
                            source_type="upload", source_ref="c.mp4", whisper_model="base",
                            status="done"))
    await db_session.commit()

    resp = await client.post("/api/v1/video-jobs/cancel-all")
    assert resp.status_code == 200
    assert resp.json() == {"cancelled_queued": 0, "cancelled_active": 0}


async def test_list_orders_same_second_jobs_by_id_desc(client, db_session, admin_user):
    # 일괄 업로드는 여러 작업이 같은 초(created_at)에 생긴다 — id 타이브레이커로
    # 생성 역순이 안정적으로 유지되는지 검증(정렬 뒤섞임 회귀 가드).
    from datetime import datetime

    ts = datetime(2026, 7, 8, 6, 0, 0)
    for i in range(3):
        db_session.add(VideoJob(external_id=uuid4(), owner_user_id=admin_user.id,
                                title=f"clip-{i}", source_type="upload",
                                source_ref=f"c{i}.mp4", whisper_model="small",
                                status="queued", created_at=ts))
    await db_session.commit()

    resp = await client.get("/api/v1/video-jobs")
    titles = [j["title"] for j in resp.json()["items"]]
    assert titles[:3] == ["clip-2", "clip-1", "clip-0"]


async def test_job_created_at_serialized_with_utc_offset(client, db_session, admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="queued")
    db_session.add(job)
    await db_session.commit()
    resp = await client.get("/api/v1/video-jobs")
    created = resp.json()["items"][0]["created_at"]
    assert created.endswith("+00:00") or created.endswith("Z")


async def test_retranslate_only_touches_untranslated(client, db_session, admin_user,
                                                     monkeypatch):
    """핵심 안전 속성: 수동 편집 줄은 절대 덮어쓰지 않는다."""
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.flush()
    job_id = job.id
    db_session.add(VideoSegment(job_id=job_id, seq=1, start_ms=0, end_ms=900,
                                text_en="Hello there", text_ko="Hello there"))  # 영문 잔존
    db_session.add(VideoSegment(job_id=job_id, seq=2, start_ms=900, end_ms=1800,
                                text_en="Good morning", text_ko="좋은 아침"))    # 사용자 편집(한글)
    # ★판별 테스트: 사용자가 일부러 영문으로 남긴 편집. 이 행이 있어야
    # 대상 선정을 is_untranslated로 바꿔치기했을 때 테스트가 실패한다
    # (is_english_leak("Margarita")=True라 잘못 대상에 포함됨).
    # seq=2가 순한글이라 두 선택자가 우연히 같은 답을 내서, 그것만으로는
    # 이 회귀를 못 잡는다 — 실제로 Task 4 리뷰가 이 구멍을 적발했다.
    db_session.add(VideoSegment(job_id=job_id, seq=3, start_ms=1800, end_ms=2700,
                                text_en="Margarita vibes", text_ko="Margarita"))
    await db_session.commit()

    class FakeTranslator:
        async def translate_batch(self, texts):
            return ["안녕하세요"] * len(texts)

    monkeypatch.setattr(api_vj, "create_translator", lambda p, m: FakeTranslator())
    monkeypatch.setattr(api_vj, "list_translate_engines",
                        lambda: [{"value": "claude", "label": "Claude 구독",
                                  "available": True}])

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/retranslate",
                             json={"provider": "claude"})
    assert resp.status_code == 200
    # seq=1만 대상. seq=3이 대상에 섞이면 total=2가 되어 여기서 먼저 실패한다.
    assert resp.json() == {"total": 1, "retranslated": 1, "remaining": 0}

    db_session.expire_all()
    rows = (await db_session.execute(
        select(VideoSegment).where(VideoSegment.job_id == job_id)
        .order_by(VideoSegment.seq))).scalars().all()
    assert rows[0].text_ko == "안녕하세요"   # 영문 잔존만 갱신
    assert rows[1].text_ko == "좋은 아침"     # 한글 편집 보존
    # ★이 단언이 회귀 잠금이다: 대상 선정을 is_untranslated로 바꾸면 이 줄이
    # "안녕하세요"로 덮여 실패한다.
    assert rows[2].text_ko == "Margarita"     # 의도적 영문 편집 보존


async def test_retranslate_rejects_unavailable_engine(client, db_session, admin_user,
                                                      monkeypatch):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.commit()
    monkeypatch.setattr(api_vj, "list_translate_engines",
                        lambda: [{"value": "claude", "label": "Claude 구독",
                                  "available": False}])
    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/retranslate",
                             json={"provider": "claude"})
    assert resp.status_code == 409


async def test_retranslate_rejects_running_job(client, db_session, admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="transcribing")
    db_session.add(job)
    await db_session.commit()
    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/retranslate",
                             json={"provider": "claude"})
    assert resp.status_code == 409


async def test_retranslate_reports_remaining(client, db_session, admin_user,
                                             monkeypatch):
    """재번역해도 여전히 영문이면 remaining으로 보고한다."""
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.flush()
    db_session.add(VideoSegment(job_id=job.id, seq=1, start_ms=0, end_ms=900,
                                text_en="Hello there", text_ko="Hello there"))
    await db_session.commit()

    class EchoTranslator:
        async def translate_batch(self, texts):
            return list(texts)   # 영문 그대로 반환

    monkeypatch.setattr(api_vj, "create_translator", lambda p, m: EchoTranslator())
    monkeypatch.setattr(api_vj, "list_translate_engines",
                        lambda: [{"value": "claude", "label": "Claude 구독",
                                  "available": True}])

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/retranslate",
                             json={"provider": "claude"})
    assert resp.json() == {"total": 1, "retranslated": 0, "remaining": 1}


async def _new_scene_job(db_session, admin_user, status="done"):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status=status)
    db_session.add(job)
    await db_session.commit()
    return job


async def test_rule_min_ms_auto_scales_with_interval(client, db_session, admin_user):
    """회귀(실기): min_ms=2000 고정이 0.25초 스캔에서 잡은 짧은 씬(0.75초)을
    전부 흡수했다. 흡수량은 샘플 간격에 비례해야 한다 — 2초 간격이면 1샘플
    튐=2초라 큰 값이 필요하지만, 0.25초면 튐=0.25초라 작아야 짧은 씬이 살아남는다.
    min_ms 미지정(None) 시 간격에 비례한 값을 자동 적용한다."""
    job = await _new_scene_job(db_session, admin_user, status="done")
    # 0.25초 간격, 0.75초짜리 짧은 씬(0020) 포함
    frames = []
    labels = (["HH_010_0010"] * 8 + ["HH_010_0020"] * 3 + ["HH_010_0030"] * 8)
    for i, lb in enumerate(labels):
        frames.append({"t_ms": i * 250, "text": lb})
    pl.save_scenes(job.external_id, {"scanning": False, "interval_ms": 250,
                                     "frames": frames})
    r = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/rule",
                          json={"seq_tokens": [1], "scene_tokens": [2]})
    assert r.status_code == 200
    scenes = [s["label"] for s in r.json()["segments_scene"]]
    assert "HH_010_0020" in scenes, \
        f"0.75초 짧은 씬이 흡수되면 안 된다: {scenes}"


async def test_scan_accepts_interval_and_decouples_thumbs(client, db_session,
                                                         admin_user, monkeypatch):
    """짧은 씬(2초 미만)을 잡으려면 스캔 간격을 촘촘하게 줄 수 있어야 한다. 다만
    썸네일까지 촘촘하면 필름스트립이 수천 칸이 되므로, 썸네일 간격은 분리해
    성기게 유지한다(scan 0.25s여도 thumb는 2s)."""
    captured = {}
    monkeypatch.setattr(api_vj, "_start_scene_scan",
                        lambda eid, interval_s: captured.update(interval=interval_s))
    job = await _new_scene_job(db_session, admin_user, status="done")
    d = pl.job_dir(job.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x")
    job.burned_path = str(d / "burned.mp4")
    await db_session.commit()
    r = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/scan",
                          json={"interval_s": 0.25})
    assert r.status_code == 202
    assert captured["interval"] == 0.25


async def test_scan_interval_out_of_range_rejected(client, db_session, admin_user):
    job = await _new_scene_job(db_session, admin_user, status="done")
    d = pl.job_dir(job.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x")
    job.burned_path = str(d / "burned.mp4")
    await db_session.commit()
    r = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/scan",
                          json={"interval_s": 0.01})
    assert r.status_code == 422


async def test_scan_scenes_requires_done_status(client, db_session, admin_user):
    job = await _new_scene_job(db_session, admin_user, status="review")
    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/scan")
    assert resp.status_code == 409


async def test_scan_scenes_starts_task_when_done(client, db_session, admin_user,
                                                 monkeypatch):
    started = {}
    monkeypatch.setattr(api_vj, "_start_scene_scan",
                        lambda eid, interval_s: started.setdefault("eid", eid))
    job = await _new_scene_job(db_session, admin_user, status="done")
    # scan endpoint also requires burned.mp4 to exist on disk
    d = pl.job_dir(job.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x")
    job.burned_path = str(d / "burned.mp4")
    await db_session.commit()
    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/scan")
    assert resp.status_code == 202
    assert started["eid"] == job.external_id


async def test_scan_scenes_writes_initial_scanning_state(client, db_session,
                                                         admin_user, monkeypatch):
    """재스캔 폴링 레이스 방지: 202 직후 scenes.json에 scanning 상태가 동기
    기록돼야 한다 — 프레임 추출(수 분) 동안 옛 scanned 데이터가 보이면
    프론트 폴링이 '스캔 완료(옛 데이터)'로 오판한다."""
    monkeypatch.setattr(api_vj, "_start_scene_scan", lambda eid, interval_s: None)
    job = await _new_scene_job(db_session, admin_user, status="done")
    d = pl.job_dir(job.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x")
    job.burned_path = str(d / "burned.mp4")
    await db_session.commit()
    # 이전 스캔 완료 데이터가 있는 상태(재스캔 시나리오)
    pl.save_scenes(job.external_id, {"scanning": False, "interval_ms": 2000,
                                     "frames": [{"t_ms": 0, "text": "A_B_C"}]})
    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/scan")
    assert resp.status_code == 202
    body = (await client.get(
        f"/api/v1/video-jobs/{job.external_id}/scenes")).json()
    assert body["scanning"] is True
    assert body["scanned"] is False


async def test_scene_thumb_at_extracts_and_caches(client, db_session, admin_user,
                                                  monkeypatch, tmp_path):
    """경계 썸네일(임의 시각)은 요청 시 추출하고 디스크에 캐시한다 — 캐시 키가
    t_ms라 경계가 바뀌어도 무효화가 필요 없다(같은 시각=같은 프레임)."""
    calls: list[int] = []

    def fake_extract(ffmpeg, src, t_ms, dst, height=90, proc_key=None):
        calls.append(t_ms)
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"\xff\xd8jpg")

    monkeypatch.setattr(api_vj, "extract_thumbnail_at", fake_extract)
    monkeypatch.setattr(api_vj, "locate_ffmpeg", lambda: "ffmpeg")
    job = await _new_scene_job(db_session, admin_user, status="done")
    d = pl.job_dir(job.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x")
    job.burned_path = str(d / "burned.mp4")
    await db_session.commit()

    url = f"/api/v1/video-jobs/{job.external_id}/scenes/thumb-at"
    r1 = await client.get(url, params={"t_ms": 4968})
    assert r1.status_code == 200
    assert calls == [4968]
    r2 = await client.get(url, params={"t_ms": 4968})  # 캐시 히트
    assert r2.status_code == 200
    assert calls == [4968]


async def test_scene_thumb_at_404_without_burned(client, db_session, admin_user):
    job = await _new_scene_job(db_session, admin_user, status="done")
    r = await client.get(
        f"/api/v1/video-jobs/{job.external_id}/scenes/thumb-at",
        params={"t_ms": 1000})
    assert r.status_code == 404


async def test_cancel_scene_ops_stops_polling_states(client, db_session, admin_user,
                                                     monkeypatch):
    """긴 작업(스캔/정밀화)을 멈출 수단 — 지금까지는 앱을 죽여야 했다. 취소하면
    상태 파일의 진행 플래그를 내려 프론트 폴링이 멈춰야 한다."""
    killed = {}
    monkeypatch.setattr(api_vj, "cancel_job_task",
                        lambda eid: killed.setdefault("eid", eid))
    job = await _new_scene_job(db_session, admin_user, status="done")
    pl.save_refine_status(job.external_id, {"refining": True, "done": 3,
                                            "total": 414, "error": None})
    pl.save_scenes(job.external_id, {"scanning": True, "interval_ms": 2000,
                                     "frames": [], "ocr_region": {"x": 0, "y": 0,
                                                                  "w": 1, "h": 0.2}})
    r = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/cancel")
    assert r.status_code == 202
    assert killed["eid"] == job.external_id
    assert pl.load_refine_status(job.external_id)["refining"] is False
    body = (await client.get(
        f"/api/v1/video-jobs/{job.external_id}/scenes")).json()
    assert body["scanning"] is False
    assert body["scanned"] is False, "취소된 스캔을 완료로 오인하면 안 된다"
    assert body["ocr_region"] == {"x": 0, "y": 0, "w": 1, "h": 0.2}, \
        "취소해도 지정한 구역은 남아야 한다"


async def test_slate_template_crud(client, monkeypatch, tmp_path):
    """쇼 템플릿 CRUD — 구역과 토큰 규칙을 쇼 이름으로 저장해 다음 작품에서
    골라 쓴다. 잡에 속하지 않는 전역 목록이다."""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    payload = {"name": "HZBN307",
               "region": {"x": 0.02, "y": 0.03, "w": 0.5, "h": 0.08},
               "delimiters": ["_", "-"], "seq_tokens": [1], "scene_tokens": [2]}
    r = await client.post("/api/v1/video-jobs/slate-templates", json=payload)
    assert r.status_code == 200
    got = (await client.get("/api/v1/video-jobs/slate-templates")).json()
    assert [t["name"] for t in got["templates"]] == ["HZBN307"]
    assert got["templates"][0]["seq_tokens"] == [1]
    d = await client.delete("/api/v1/video-jobs/slate-templates/HZBN307")
    assert d.status_code == 200
    assert (await client.get(
        "/api/v1/video-jobs/slate-templates")).json()["templates"] == []


async def test_slate_template_delete_unknown_is_404(client, monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    r = await client.delete("/api/v1/video-jobs/slate-templates/none")
    assert r.status_code == 404


async def test_set_and_keep_ocr_region(client, db_session, admin_user):
    """OCR 영역(비율)은 잡에 저장되고, 재스캔해도 유지돼야 한다 — 쇼마다 슬레이트
    위치가 달라 이 지정이 곧 그 작품의 설정이다."""
    job = await _new_scene_job(db_session, admin_user, status="done")
    d = pl.job_dir(job.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x")
    job.burned_path = str(d / "burned.mp4")
    await db_session.commit()
    pl.save_scenes(job.external_id, {"scanning": False, "interval_ms": 2000,
                                     "frames": [{"t_ms": 0, "text": "A_B_C"}]})
    r = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/ocr-region",
        json={"x": 0.02, "y": 0.03, "w": 0.5, "h": 0.08})
    assert r.status_code == 200
    body = (await client.get(
        f"/api/v1/video-jobs/{job.external_id}/scenes")).json()
    assert body["ocr_region"] == {"x": 0.02, "y": 0.03, "w": 0.5, "h": 0.08}
    assert body["frames"], "영역 저장이 스캔 결과를 지우면 안 된다"
    assert pl.load_ocr_region(job.external_id) == (0.02, 0.03, 0.5, 0.08)


async def test_rule_computation_keeps_ocr_region(client, db_session, admin_user):
    """회귀(실기): 경계 계산이 scenes.json을 새 dict로 덮어써 사용자가 지정한
    OCR 구역이 사라졌다 — 다음 스캔/정밀화가 전체 프레임을 훑어 느려지고, 쇼에
    따라서는 판독 자체가 실패한다. 구역은 작업 산출물이 아니라 그 작품의 설정이다."""
    job = await _new_scene_job(db_session, admin_user, status="done")
    pl.save_scenes(job.external_id, {
        "scanning": False, "interval_ms": 2000,
        "frames": [{"t_ms": 0, "text": "HH_010_0010_AC"},
                   {"t_ms": 2000, "text": "HH_020_0010_AC"}],
        "ocr_region": {"x": 0.03, "y": 0.04, "w": 0.35, "h": 0.06},
    })
    r = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/rule",
                          json={"seq_tokens": [1], "scene_tokens": [2]})
    assert r.status_code == 200
    assert pl.load_ocr_region(job.external_id) == (0.03, 0.04, 0.35, 0.06)


async def test_ocr_region_rejects_out_of_range(client, db_session, admin_user):
    job = await _new_scene_job(db_session, admin_user, status="done")
    r = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/ocr-region",
        json={"x": 0.0, "y": 0.0, "w": 1.5, "h": 0.1})
    assert r.status_code == 422


async def test_ocr_test_read_returns_text(client, db_session, admin_user,
                                          monkeypatch):
    """드래그한 영역이 맞는지 25분 스캔 전에 확인하는 미리읽기."""
    monkeypatch.setattr(api_vj, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(api_vj, "extract_frame",
                        lambda *a, **k: Path(a[3]).write_bytes(b"x"))
    monkeypatch.setattr(api_vj, "read_slate_line",
                        lambda *a, **k: "HH0307_010_0010_AC_v01")
    job = await _new_scene_job(db_session, admin_user, status="done")
    d = pl.job_dir(job.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x")
    job.burned_path = str(d / "burned.mp4")
    await db_session.commit()
    r = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/ocr-test",
        json={"t_ms": 6000, "region": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.1}})
    assert r.status_code == 200
    assert r.json()["text"] == "HH0307_010_0010_AC_v01"


async def test_ocr_test_falls_back_to_rescaled_read(client, db_session,
                                                    admin_user, monkeypatch):
    """미리읽기는 스캔과 같은 폴백(리스케일 재판독)을 쓴다 — 미리읽기만 1차
    판독으로 끝내면 스캔은 읽어낼 프레임에 "판독 실패"가 떠 사용자가 멀쩡한
    구역을 다시 잡게 된다."""
    monkeypatch.setattr(api_vj, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(api_vj, "extract_frame",
                        lambda *a, **k: Path(a[3]).write_bytes(b"x"))
    monkeypatch.setattr(api_vj, "read_slate_line", lambda *a, **k: "")
    monkeypatch.setattr(api_vj, "read_slate_line_rescaled",
                        lambda *a, **k: "FL102 J002")
    job = await _new_scene_job(db_session, admin_user, status="done")
    d = pl.job_dir(job.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x")
    job.burned_path = str(d / "burned.mp4")
    await db_session.commit()
    r = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/ocr-test",
        json={"t_ms": 6000, "region": {"x": 0.7, "y": 0.9, "w": 0.2, "h": 0.09}})
    assert r.status_code == 200
    assert r.json()["text"] == "FL102 J002"


async def test_get_scenes_empty_before_scan(client, db_session, admin_user):
    job = await _new_scene_job(db_session, admin_user, status="done")
    resp = await client.get(f"/api/v1/video-jobs/{job.external_id}/scenes")
    assert resp.status_code == 200
    assert resp.json()["scanned"] is False


async def test_set_rule_computes_boundaries(client, db_session, admin_user):
    job = await _new_scene_job(db_session, admin_user, status="done")
    pl.save_scenes(job.external_id, {"interval_ms": 1000, "frames": [
        {"t_ms": 0, "text": "HH0307_020_0150_AC_v01"},
        {"t_ms": 1000, "text": "HH0307_020_0170_AC_v01"},
        {"t_ms": 2000, "text": "HH0307_021_0010_AC_v01"},
    ]})
    resp = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/rule",
        json={"seq_tokens": [1], "scene_tokens": [2], "min_ms": 0})
    assert resp.status_code == 200
    labels = [s["label"] for s in resp.json()["segments_scene"]]
    assert labels == ["HH0307_020_0150", "HH0307_020_0170", "HH0307_021_0010"]
    seq_labels = [s["label"] for s in resp.json()["segments_sequence"]]
    assert seq_labels == ["HH0307_020", "HH0307_021"]


async def test_export_starts_task(client, db_session, admin_user, monkeypatch):
    started = {}
    monkeypatch.setattr(api_vj, "_start_scene_export",
                        lambda eid, mode, out, idx=None: started.update(
                            eid=eid, mode=mode, idx=idx))
    job = await _new_scene_job(db_session, admin_user, status="done")
    pl.save_scenes(job.external_id, {"segments_scene": [
        {"label": "HH0307_020_0150", "start_ms": 0, "end_ms": 3000}]})
    resp = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/export",
        json={"mode": "scene"})
    assert resp.status_code == 202
    assert started["mode"] == "scene"
    assert started["idx"] is None  # 인덱스 미지정 = 전체 익스포트(기존 동작)


async def test_export_partial_passes_sorted_indices(client, db_session, admin_user,
                                                    monkeypatch):
    """개별 씬 익스포트 — 고른 씬(+이웃)만 다시 굽는다. count는 선택 개수여야
    프론트 진행바가 전체 개수로 오해되지 않는다."""
    started = {}
    monkeypatch.setattr(api_vj, "_start_scene_export",
                        lambda eid, mode, out, idx=None: started.update(idx=idx))
    job = await _new_scene_job(db_session, admin_user, status="done")
    pl.save_scenes(job.external_id, {"segments_scene": [
        {"label": "0010", "start_ms": 0, "end_ms": 1000},
        {"label": "0020", "start_ms": 1000, "end_ms": 2000},
        {"label": "0030", "start_ms": 2000, "end_ms": 3000},
    ]})
    resp = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/export",
        json={"mode": "scene", "indices": [2, 1, 1]})
    assert resp.status_code == 202
    assert resp.json()["count"] == 2
    assert started["idx"] == [1, 2]  # 중복 제거 + 정렬


async def test_export_rejects_indices_out_of_range(client, db_session, admin_user,
                                                   monkeypatch):
    """목록이 어긋난 채 엉뚱한 씬을 덮어쓰는 것이 최악의 결과 — 자르지 않고 거부한다."""
    monkeypatch.setattr(api_vj, "_start_scene_export",
                        lambda *a, **k: pytest.fail("범위 밖 인덱스로 익스포트가 시작됐다"))
    job = await _new_scene_job(db_session, admin_user, status="done")
    pl.save_scenes(job.external_id, {"segments_scene": [
        {"label": "0010", "start_ms": 0, "end_ms": 1000}]})
    for bad in ([1], [-1], []):
        resp = await client.post(
            f"/api/v1/video-jobs/{job.external_id}/scenes/export",
            json={"mode": "scene", "indices": bad})
        assert resp.status_code == 409, bad


async def test_export_rejects_without_segments(client, db_session, admin_user):
    job = await _new_scene_job(db_session, admin_user, status="done")
    resp = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/export",
        json={"mode": "scene"})
    assert resp.status_code == 409


async def test_get_scenes_reports_scan_progress(client, db_session, admin_user):
    job = await _new_scene_job(db_session, admin_user, status="done")
    from apps.server.domain.video_captions import pipeline as pl
    pl.save_scenes(job.external_id, {"scanning": True, "interval_ms": 2000,
                                     "total_frames": 658, "ocr_done": 240,
                                     "frames": []})
    r = await client.get(f"/api/v1/video-jobs/{job.external_id}/scenes")
    body = r.json()
    assert r.status_code == 200
    assert body["scanning"] is True and body["scanned"] is False
    assert body["ocr_done"] == 240 and body["total_frames"] == 658


async def test_get_scenes_reports_scan_error(client, db_session, admin_user):
    job = await _new_scene_job(db_session, admin_user, status="done")
    from apps.server.domain.video_captions import pipeline as pl
    pl.save_scenes(job.external_id, {"scanning": False, "frames": [],
                                     "error": "스캔에 실패했습니다."})
    r = await client.get(f"/api/v1/video-jobs/{job.external_id}/scenes")
    body = r.json()
    assert body["scanned"] is False and body["error"] == "스캔에 실패했습니다."


async def test_export_status_reports_progress(client, db_session, admin_user):
    job = await _new_scene_job(db_session, admin_user, status="done")
    from apps.server.domain.video_captions import pipeline as pl
    pl.save_export_status(job.external_id, {"exporting": True, "done": 12,
                                            "total": 33, "out_dir": "/tmp/out",
                                            "error": None})
    r = await client.get(f"/api/v1/video-jobs/{job.external_id}/scenes/export/status")
    body = r.json()
    assert r.status_code == 200
    assert body["exporting"] is True and body["done"] == 12 and body["total"] == 33


async def test_export_status_empty_when_never_run(client, db_session, admin_user):
    job = await _new_scene_job(db_session, admin_user, status="done")
    r = await client.get(f"/api/v1/video-jobs/{job.external_id}/scenes/export/status")
    assert r.json()["exporting"] is False


# ── 지문 컷 감지 method 분기 ─────────────────────────────────────────────────

async def test_scan_method_fingerprint_starts_fp_task(client, db_session,
                                                      admin_user, monkeypatch):
    """method=fingerprint면 지문 스캔 시임으로 분기하고(간격 시임 미호출),
    초기 동기 기록에도 method가 실려 재진입 폴링이 방식을 안다."""
    started = {}
    monkeypatch.setattr(api_vj, "_start_scene_scan_fingerprint",
                        lambda eid: started.setdefault("fp", eid))
    monkeypatch.setattr(api_vj, "_start_scene_scan",
                        lambda eid, interval_s: started.setdefault("interval", eid))
    job = await _new_scene_job(db_session, admin_user, status="done")
    d = pl.job_dir(job.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x")
    job.burned_path = str(d / "burned.mp4")
    await db_session.commit()
    r = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/scan",
                          json={"method": "fingerprint"})
    assert r.status_code == 202
    assert started == {"fp": job.external_id}
    body = (await client.get(
        f"/api/v1/video-jobs/{job.external_id}/scenes")).json()
    assert body["scanning"] is True
    assert body["method"] == "fingerprint"


async def test_scan_method_default_is_interval(client, db_session, admin_user,
                                               monkeypatch):
    """method 미지정은 기존 간격 스캔 그대로 — 하위 호환."""
    started = {}
    monkeypatch.setattr(api_vj, "_start_scene_scan_fingerprint",
                        lambda eid: started.setdefault("fp", eid))
    monkeypatch.setattr(api_vj, "_start_scene_scan",
                        lambda eid, interval_s: started.setdefault("interval", eid))
    job = await _new_scene_job(db_session, admin_user, status="done")
    d = pl.job_dir(job.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x")
    job.burned_path = str(d / "burned.mp4")
    await db_session.commit()
    r = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/scan")
    assert r.status_code == 202
    assert started == {"interval": job.external_id}


async def test_scan_method_invalid_rejected(client, db_session, admin_user):
    job = await _new_scene_job(db_session, admin_user, status="done")
    d = pl.job_dir(job.external_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "burned.mp4").write_bytes(b"x")
    job.burned_path = str(d / "burned.mp4")
    await db_session.commit()
    r = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/scan",
                          json={"method": "psychic"})
    assert r.status_code == 422


async def test_rule_on_fingerprint_data_groups_runs(client, db_session,
                                                    admin_user):
    """지문 스캔 데이터에 규칙을 확정하면 런을 키로 병합해 양 모드 세그먼트를
    만든다 — min_ms 흡수·중앙정렬 없이 런 경계(프레임 정확) 그대로."""
    job = await _new_scene_job(db_session, admin_user, status="done")
    runs = [
        {"start_ms": 21, "end_ms": 250, "text": "HH_010_0010_AC"},
        {"start_ms": 250, "end_ms": 400, "text": "HH_010_0010_AC"},  # 가짜 컷
        {"start_ms": 400, "end_ms": 900, "text": "HH_010_0020_AC"},
        {"start_ms": 900, "end_ms": 1400, "text": "HH_020_0010_AC"},
    ]
    pl.save_scenes(job.external_id, {
        "scanning": False, "method": "fingerprint", "total_ms": 1400,
        "runs": runs,
        "frames": [{"t_ms": r["start_ms"], "text": r["text"]} for r in runs],
        "ocr_region": {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.1},
    })
    r = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/rule",
                          json={"seq_tokens": [1], "scene_tokens": [2]})
    assert r.status_code == 200
    body = r.json()
    assert [(s["label"], s["start_ms"], s["end_ms"])
            for s in body["segments_scene"]] == [
        ("HH_010_0010", 21, 400), ("HH_010_0020", 400, 900),
        ("HH_020_0010", 900, 1400)]
    assert [s["label"] for s in body["segments_sequence"]] == [
        "HH_010", "HH_020"]
    # 저장본에도 반영되고 method·runs·구역은 보존된다(재계산 가능해야 하므로).
    saved = pl.load_scenes(job.external_id)
    assert saved["method"] == "fingerprint"
    assert saved["runs"] == runs
    assert saved["ocr_region"] == {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.1}
    got = (await client.get(
        f"/api/v1/video-jobs/{job.external_id}/scenes")).json()
    assert got["method"] == "fingerprint"
    assert got["total_ms"] == 1400


async def test_refine_rejected_on_fingerprint_data(client, db_session,
                                                   admin_user):
    """지문 경계는 이미 프레임 정확 — 정밀화 요청은 409(수십 분 낭비 방지)."""
    job = await _new_scene_job(db_session, admin_user, status="done")
    pl.save_scenes(job.external_id, {
        "scanning": False, "method": "fingerprint",
        "rule": {"seq_tokens": [1], "scene_tokens": [2]},
        "frames": [{"t_ms": 0, "text": "HH_010_0010"}],
        "segments_scene": [
            {"label": "HH_010_0010", "start_ms": 0, "end_ms": 500},
            {"label": "HH_010_0020", "start_ms": 500, "end_ms": 900}],
        "segments_sequence": [],
    })
    r = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/refine",
                          json={"mode": "scene"})
    assert r.status_code == 409


async def test_cancel_scene_ops_preserves_method(client, db_session, admin_user):
    """스캔 취소가 방식 선택을 지우면 다음 GET이 interval로 오판한다."""
    job = await _new_scene_job(db_session, admin_user, status="done")
    pl.save_scenes(job.external_id, {"scanning": True, "method": "fingerprint",
                                     "total_frames": 0, "ocr_done": 0,
                                     "frames": []})
    r = await client.post(f"/api/v1/video-jobs/{job.external_id}/scenes/cancel")
    assert r.status_code == 202
    got = (await client.get(
        f"/api/v1/video-jobs/{job.external_id}/scenes")).json()
    assert got["scanning"] is False
    assert got["method"] == "fingerprint"


async def test_slate_template_accepts_method(client, monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    payload = {"name": "HZBN307",
               "region": {"x": 0.02, "y": 0.03, "w": 0.5, "h": 0.08},
               "delimiters": ["_", "-"], "seq_tokens": [1], "scene_tokens": [2],
               "method": "fingerprint"}
    r = await client.post("/api/v1/video-jobs/slate-templates", json=payload)
    assert r.status_code == 200
    got = (await client.get("/api/v1/video-jobs/slate-templates")).json()
    assert got["templates"][0]["method"] == "fingerprint"
