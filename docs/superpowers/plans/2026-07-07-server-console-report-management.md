# 서버 콘솔 보고서 관리·리뷰 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 서버 콘솔(apps/server_desktop)에 "보고서 관리" 탭을 추가하여 운영자가 전체 회의 보고서를 목록·리뷰·익스포트·삭제할 수 있게 한다.

**Architecture:** 기존 operator 인증 엔드포인트를 건드리지 않고, `apps/server`에 무인증 loopback 라우터 `/reports`를 신규 추가한다(video-jobs 컨벤션과 동일). 보고서 생성 로직은 기존 `domain/report_*.py` 빌더를 재사용한다. 프론트는 `videoJobsAdmin.ts`/`VideoJobsPanel.tsx` 3종 세트를 복제한다.

**Tech Stack:** FastAPI + SQLAlchemy async (백엔드), React + TypeScript + Vite + Tauri (서버 콘솔), pytest (백엔드 테스트), vitest (프론트 테스트).

## Global Constraints

- 새 라우터는 **무인증**(`Depends(get_session)`만). operator/admin 인증 붙이지 말 것.
- 정적 경로(`/storage`)는 동적 경로(`/{external_id}`) **앞에** 선언(안 그러면 UUID 파싱 422).
- 보고서 파일 경로: `report_path(root, sid, fmt)` = `{root}/{sid}/report.{fmt}`. 요약 파일 경로: `summary_path(root, sid, fmt)` = `{root}/{sid}/summary.{fmt}` (주의: `report.summary.*`가 **아니라** `summary.*`).
- `sid`는 `str(meeting.external_id)`(UUID 문자열). 스토리지 디렉토리 이름도 `external_id`.
- Utterance→Session FK는 정수 PK `Session.id`(`session_id`), `ondelete="CASCADE"`. API 조회는 `external_id`(UUID). 변환은 로드한 `meeting.id` 사용.
- FTS 테이블 상수 `session_search_fts`. 단일 세션 삭제 전용 헬퍼는 없음 → `DELETE FROM session_search_fts WHERE session_id = :sid`(sid = `str(meeting.id)`), 단 `await fts5_available(db)`가 True일 때만. caller가 commit 소유.
- PDF는 별도 빌더 없음: docx bytes → `convert_docx_to_pdf(bytes) -> bytes | None`.
- `_storage_root()` = `os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")`.
- 백엔드 테스트 실행: `pytest apps/server/tests/<file> -v`. 프론트 테스트: `cd apps/server_desktop && pnpm test`.
- 소스 변경 후 실제 앱 반영에는 frozen-bundle 재동결 필요(Task 10).

## File Structure

- Create: `apps/server/api/v1/reports.py` — 무인증 loopback 보고서 관리 라우터
- Create: `apps/server/tests/test_api_reports_admin.py` — 라우터 테스트
- Modify: `apps/server/main.py` — 라우터 등록
- Create: `apps/server_desktop/src/reportsAdmin.ts` — loopback API 클라이언트
- Create: `apps/server_desktop/src/reportsAdmin.test.ts` — 클라이언트 테스트
- Create: `apps/server_desktop/src/ReportsPanel.tsx` — 보고서 관리 패널 UI
- Modify: `apps/server_desktop/src/ServerConsole.tsx` — 탭/네비/패널 배선

---

### Task 1: `/reports` 라우터 + 목록 엔드포인트 + 등록

**Files:**
- Create: `apps/server/api/v1/reports.py`
- Modify: `apps/server/main.py`
- Test: `apps/server/tests/test_api_reports_admin.py`

**Interfaces:**
- Produces:
  - `router = APIRouter(prefix="/reports", tags=["reports-admin"])` mounted at `/api/v1`
  - `GET /api/v1/reports?with_sizes=bool` → `{"items": [ReportRow]}` where `ReportRow = {session_id: str, title: str, status: str, started_at: str|None, ended_at: str|None, report_ready: bool, summary_ready: bool, size_bytes?: int}`
  - Helpers: `_report_dir(sid: str) -> Path`, `_dir_size(sid: str) -> int`, `_get_session_or_404(db, external_id) -> Session`

- [ ] **Step 1: Write the failing test**

Create `apps/server/tests/test_api_reports_admin.py`:

```python
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from apps.server.db.models import Session, Utterance


async def _make_session(db_session, admin_user, *, status="ended", title="회의A"):
    s = Session(
        external_id=uuid4(),
        owner_user_id=admin_user.id,
        title=title,
        status=status,
    )
    db_session.add(s)
    await db_session.flush()
    db_session.add(
        Utterance(
            session_id=s.id, seq=1, text_en="hello", text_ko="안녕",
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/server/tests/test_api_reports_admin.py -v`
Expected: FAIL — 404 (route not registered) / collection succeeds but assertions fail.

- [ ] **Step 3: Create the router**

Create `apps/server/api/v1/reports.py`:

```python
"""보고서 관리 라우터 — 서버 콘솔 전용 무인증 loopback REST.

video_jobs.py와 동일한 control-plane 모델(127.0.0.1, 인증 없음). 보고서는 이미
서버 파일시스템 자산({STORAGE_ROOT}/{session_external_id}/report.*)이므로 서버
콘솔이 관리 주체가 된다. 보고서 생성 로직은 domain/report_*.py 빌더를 재사용한다.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.db.models import Session, Utterance
from apps.server.db.session import get_session
from apps.server.domain.reports import report_path, summary_path

router = APIRouter(prefix="/reports", tags=["reports-admin"])

_REPORT_FORMATS = ("md", "html", "docx", "pdf")


def _storage_root() -> str:
    return os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")


def _report_dir(sid: str) -> Path:
    return Path(_storage_root()) / sid


def _dir_size(sid: str) -> int:
    d = _report_dir(sid)
    total = 0
    if d.exists():
        for path in d.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
    return total


async def _get_session_or_404(db: AsyncSession, external_id: UUID) -> Session:
    meeting = (
        await db.execute(select(Session).where(Session.external_id == external_id))
    ).scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "세션을 찾을 수 없습니다")
    return meeting


async def _session_utterances(db: AsyncSession, session_pk: int) -> list[Utterance]:
    return list(
        (
            await db.execute(
                select(Utterance)
                .where(Utterance.session_id == session_pk)
                .order_by(Utterance.started_at.asc(), Utterance.seq.asc())
            )
        ).scalars().all()
    )


def _report_ready(meeting: Session) -> bool:
    sid = str(meeting.external_id)
    if report_path(_storage_root(), sid, "md").exists():
        return True
    return meeting.status == "ended"


def _summary_ready(meeting: Session) -> bool:
    return summary_path(_storage_root(), str(meeting.external_id), "md").exists()


def _row(meeting: Session, *, with_sizes: bool) -> dict:
    sid = str(meeting.external_id)
    out = {
        "session_id": sid,
        "title": meeting.title,
        "status": meeting.status,
        "started_at": meeting.started_at.isoformat() if meeting.started_at else None,
        "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
        "report_ready": _report_ready(meeting),
        "summary_ready": _summary_ready(meeting),
    }
    if with_sizes:
        out["size_bytes"] = _dir_size(sid)
    return out


@router.get("")
async def list_reports(
    db: Annotated[AsyncSession, Depends(get_session)],
    with_sizes: Annotated[bool, Query()] = False,
) -> dict:
    sessions = (
        await db.execute(select(Session).order_by(Session.started_at.desc()).limit(200))
    ).scalars().all()
    return {"items": [_row(s, with_sizes=with_sizes) for s in sessions]}
```

- [ ] **Step 4: Register the router**

In `apps/server/main.py`, add the import next to the other v1 router imports (near line 25):

```python
from apps.server.api.v1.reports import router as reports_router
```

And register it next to `video_jobs_router` (near line 185):

```python
app.include_router(reports_router, prefix="/api/v1")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest apps/server/tests/test_api_reports_admin.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add apps/server/api/v1/reports.py apps/server/main.py apps/server/tests/test_api_reports_admin.py
git commit -m "feat(server): 보고서 관리 라우터 + 목록 엔드포인트"
```

---

### Task 2: `/reports/storage` 엔드포인트

**Files:**
- Modify: `apps/server/api/v1/reports.py`
- Test: `apps/server/tests/test_api_reports_admin.py`

**Interfaces:**
- Produces: `GET /api/v1/reports/storage` → `{"total_bytes": int, "session_count": int}`

- [ ] **Step 1: Write the failing test**

Append to `apps/server/tests/test_api_reports_admin.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/server/tests/test_api_reports_admin.py::test_storage_usage -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Add the endpoint**

In `apps/server/api/v1/reports.py`, add **before** any future `/{external_id}` route (immediately after `list_reports`):

```python
@router.get("/storage")
async def storage_usage(db: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    root = Path(_storage_root())
    total = 0
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
    count = (await db.execute(select(func.count()).select_from(Session))).scalar_one()
    return {"total_bytes": total, "session_count": count}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/server/tests/test_api_reports_admin.py::test_storage_usage -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/server/api/v1/reports.py apps/server/tests/test_api_reports_admin.py
git commit -m "feat(server): 보고서 스토리지 사용량 엔드포인트"
```

---

### Task 3: 리뷰용 HTML 뷰 엔드포인트 (보고서 + 요약)

**Files:**
- Modify: `apps/server/api/v1/reports.py`
- Test: `apps/server/tests/test_api_reports_admin.py`

**Interfaces:**
- Produces:
  - `GET /api/v1/reports/{external_id}/view` → `text/html` (보고서 본문 HTML)
  - `GET /api/v1/reports/{external_id}/summary/view` → `text/html` (요약 HTML; 요약 없으면 안내 HTML)
  - Helper: `async _load_summary_text(db, meeting) -> str | None` — `summary.md` 읽고, 없으면 `regenerate_report_with_summary`로 1회 생성 후 재읽기

- [ ] **Step 1: Write the failing test**

Append to `apps/server/tests/test_api_reports_admin.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/server/tests/test_api_reports_admin.py -k view -v`
Expected: FAIL — 404 (routes missing).

- [ ] **Step 3: Add the endpoints**

In `apps/server/api/v1/reports.py`, extend imports at top:

```python
from fastapi.responses import HTMLResponse
from apps.server.domain.report_html import build_session_report_html, build_summary_html
from apps.server.domain.reports import regenerate_report_with_summary
```

Add a summary-text helper and the two view routes (place the `/{external_id}/...` routes after `/storage`):

```python
async def _load_summary_text(db: AsyncSession, meeting: Session) -> str | None:
    p = summary_path(_storage_root(), str(meeting.external_id), "md")
    if p.exists():
        return p.read_text(encoding="utf-8").strip() or None
    utterances = await _session_utterances(db, meeting.id)
    await regenerate_report_with_summary(_storage_root(), meeting, utterances)
    if p.exists():
        return p.read_text(encoding="utf-8").strip() or None
    return None


@router.get("/{external_id}/view", response_class=HTMLResponse)
async def report_view(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    meeting = await _get_session_or_404(db, external_id)
    utterances = await _session_utterances(db, meeting.id)
    html = build_session_report_html(meeting, utterances)
    return HTMLResponse(content=html)


@router.get("/{external_id}/summary/view", response_class=HTMLResponse)
async def summary_view(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    meeting = await _get_session_or_404(db, external_id)
    summary = await _load_summary_text(db, meeting)
    if not summary:
        return HTMLResponse(content="<p>요약이 아직 없습니다.</p>")
    return HTMLResponse(content=build_summary_html(meeting, summary))
```

Note: `summary.md` header from `write_session_exports` is `f"# 요약 — {title}\n\n" + summary`. Reading the raw file text is fine for `build_summary_html`, which wraps it.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/server/tests/test_api_reports_admin.py -k view -v`
Expected: PASS (3 tests). `test_summary_view_when_present` reads the pre-written file (no LLM call).

- [ ] **Step 5: Commit**

```bash
git add apps/server/api/v1/reports.py apps/server/tests/test_api_reports_admin.py
git commit -m "feat(server): 보고서/요약 리뷰 HTML 뷰 엔드포인트"
```

---

### Task 4: 익스포트 다운로드 엔드포인트 (보고서 + 요약, md/html/docx/pdf)

**Files:**
- Modify: `apps/server/api/v1/reports.py`
- Test: `apps/server/tests/test_api_reports_admin.py`

**Interfaces:**
- Produces:
  - `GET /api/v1/reports/{external_id}/download?fmt=md|html|docx|pdf` → bytes (해당 MIME)
  - `GET /api/v1/reports/{external_id}/summary/download?fmt=md|html|docx|pdf` → bytes
  - Helper: `_media_type(fmt) -> str`

- [ ] **Step 1: Write the failing test**

Append to `apps/server/tests/test_api_reports_admin.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/server/tests/test_api_reports_admin.py -k download -v`
Expected: FAIL — 404 (routes missing).

- [ ] **Step 3: Add the endpoints**

In `apps/server/api/v1/reports.py`, extend imports:

```python
from fastapi.responses import Response
from apps.server.domain.reports import build_session_report
from apps.server.domain.report_docx import build_session_report_docx, build_summary_docx
from apps.server.domain.report_pdf import convert_docx_to_pdf
```

Add helper + routes:

```python
_MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


def _check_fmt(fmt: str) -> None:
    if fmt not in _REPORT_FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"지원하지 않는 형식: {fmt}")


@router.get("/{external_id}/download")
async def report_download(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
    fmt: Annotated[str, Query()] = "md",
) -> Response:
    _check_fmt(fmt)
    meeting = await _get_session_or_404(db, external_id)
    utterances = await _session_utterances(db, meeting.id)
    if fmt == "md":
        data = build_session_report(meeting, utterances).encode("utf-8")
    elif fmt == "html":
        data = build_session_report_html(meeting, utterances).encode("utf-8")
    elif fmt == "docx":
        data = build_session_report_docx(meeting, utterances)
    else:  # pdf
        pdf = convert_docx_to_pdf(build_session_report_docx(meeting, utterances))
        if pdf is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "PDF 변환 엔진 없음")
        data = pdf
    return Response(content=data, media_type=_MEDIA_TYPES[fmt])


@router.get("/{external_id}/summary/download")
async def summary_download(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
    fmt: Annotated[str, Query()] = "md",
) -> Response:
    _check_fmt(fmt)
    meeting = await _get_session_or_404(db, external_id)
    summary = await _load_summary_text(db, meeting)
    if not summary:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "요약이 없습니다")
    if fmt == "md":
        data = summary.encode("utf-8")
    elif fmt == "html":
        data = build_summary_html(meeting, summary).encode("utf-8")
    elif fmt == "docx":
        data = build_summary_docx(meeting, summary)
    else:  # pdf
        pdf = convert_docx_to_pdf(build_summary_docx(meeting, summary))
        if pdf is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "PDF 변환 엔진 없음")
        data = pdf
    return Response(content=data, media_type=_MEDIA_TYPES[fmt])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/server/tests/test_api_reports_admin.py -k download -v`
Expected: PASS (3 tests). (docx uses python-docx already vendored per existing test_report_docx.py.)

- [ ] **Step 5: Commit**

```bash
git add apps/server/api/v1/reports.py apps/server/tests/test_api_reports_admin.py
git commit -m "feat(server): 보고서/요약 익스포트 다운로드 엔드포인트"
```

---

### Task 5: 보고서 파일만 삭제 엔드포인트

**Files:**
- Modify: `apps/server/api/v1/reports.py`
- Test: `apps/server/tests/test_api_reports_admin.py`

**Interfaces:**
- Produces: `DELETE /api/v1/reports/{external_id}/files` → 204. `report.*` + `summary.*` unlink. DB/자막 보존.

- [ ] **Step 1: Write the failing test**

Append:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/server/tests/test_api_reports_admin.py::test_delete_files_only -v`
Expected: FAIL — 404/405 (route missing).

- [ ] **Step 3: Add the endpoint**

In `apps/server/api/v1/reports.py`, add:

```python
@router.delete("/{external_id}/files", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report_files(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    meeting = await _get_session_or_404(db, external_id)
    sid = str(meeting.external_id)
    for fmt in _REPORT_FORMATS:
        report_path(_storage_root(), sid, fmt).unlink(missing_ok=True)
        summary_path(_storage_root(), sid, fmt).unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/server/tests/test_api_reports_admin.py::test_delete_files_only -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/server/api/v1/reports.py apps/server/tests/test_api_reports_admin.py
git commit -m "feat(server): 보고서 파일만 삭제 엔드포인트"
```

---

### Task 6: 세션 전체 삭제 엔드포인트 (FTS 정리 + CASCADE + rmtree)

**Files:**
- Modify: `apps/server/api/v1/reports.py`
- Test: `apps/server/tests/test_api_reports_admin.py`

**Interfaces:**
- Produces: `DELETE /api/v1/reports/{external_id}/session` → 204. FTS 행 제거 + DB Session 삭제(Utterance CASCADE) + 스토리지 디렉토리 rmtree.

- [ ] **Step 1: Write the failing test**

Append:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/server/tests/test_api_reports_admin.py -k delete_whole_session -v`
Expected: FAIL — 404/405 (route missing).

- [ ] **Step 3: Add the endpoint**

In `apps/server/api/v1/reports.py`, extend imports:

```python
from sqlalchemy import text
from apps.server.db.search import FTS_TABLE, fts5_available
```

Add the route:

```python
@router.delete("/{external_id}/session", status_code=status.HTTP_204_NO_CONTENT)
async def delete_whole_session(
    external_id: UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    meeting = await _get_session_or_404(db, external_id)
    session_pk = meeting.id
    ext = str(meeting.external_id)
    # 1) FTS 인덱스에서 세션 행 제거 (fts5 사용 가능할 때만)
    if await fts5_available(db):
        await db.execute(
            text(f"DELETE FROM {FTS_TABLE} WHERE session_id = :sid"),
            {"sid": str(session_pk)},
        )
    # 2) DB 행 삭제 (Utterance는 FK CASCADE)
    await db.delete(meeting)
    await db.commit()
    # 3) 스토리지 디렉토리 제거
    shutil.rmtree(_report_dir(ext), ignore_errors=True)
```

Note: `reindex_session_fts` writes FTS `session_id` as `str(session_pk)`, so deletion must match with `str(session_pk)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/server/tests/test_api_reports_admin.py -k delete -v`
Expected: PASS (files-only + whole-session + 404).

- [ ] **Step 5: Run the full backend test file**

Run: `pytest apps/server/tests/test_api_reports_admin.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/server/api/v1/reports.py apps/server/tests/test_api_reports_admin.py
git commit -m "feat(server): 세션 전체 삭제 엔드포인트 (FTS 정리 + CASCADE + rmtree)"
```

---

### Task 7: 프론트 loopback API 클라이언트 `reportsAdmin.ts`

**Files:**
- Create: `apps/server_desktop/src/reportsAdmin.ts`
- Test: `apps/server_desktop/src/reportsAdmin.test.ts`

**Interfaces:**
- Produces:
  - `type ReportRow = { session_id, title, status, started_at, ended_at, report_ready, summary_ready, size_bytes? }`
  - `type ReportStorage = { total_bytes: number; session_count: number }`
  - `listReports(port): Promise<ReportRow[]>`
  - `getReportStorage(port): Promise<ReportStorage>`
  - `reportViewUrl(port, id, kind: "report"|"summary"): string`
  - `fetchReportBytes(port, id, kind, fmt): Promise<Uint8Array>`
  - `deleteReportFiles(port, id): Promise<void>`
  - `deleteReportSession(port, id): Promise<void>`

- [ ] **Step 1: Write the failing test**

Create `apps/server_desktop/src/reportsAdmin.test.ts` (mirror `videoJobsAdmin.test.ts`):

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  listReports,
  getReportStorage,
  reportViewUrl,
  deleteReportFiles,
  deleteReportSession,
} from "./reportsAdmin";

afterEach(() => vi.restoreAllMocks());

describe("reportsAdmin", () => {
  it("listReports calls loopback with with_sizes", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{ session_id: "a", title: "t", status: "ended", started_at: null, ended_at: null, report_ready: true, summary_ready: false }] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const rows = await listReports(8000);
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/v1/reports?with_sizes=true");
    expect(rows[0].session_id).toBe("a");
  });

  it("getReportStorage hits /reports/storage", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ total_bytes: 5, session_count: 1 }) });
    vi.stubGlobal("fetch", fetchMock);
    const st = await getReportStorage(8000);
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/v1/reports/storage");
    expect(st.session_count).toBe(1);
  });

  it("reportViewUrl builds the summary view url", () => {
    expect(reportViewUrl(8000, "xyz", "summary")).toBe("http://127.0.0.1:8000/api/v1/reports/xyz/summary/view");
    expect(reportViewUrl(8000, "xyz", "report")).toBe("http://127.0.0.1:8000/api/v1/reports/xyz/view");
  });

  it("deleteReportFiles calls DELETE /files", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);
    await deleteReportFiles(8000, "id1");
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/v1/reports/id1/files", { method: "DELETE" });
  });

  it("deleteReportSession calls DELETE /session", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);
    await deleteReportSession(8000, "id1");
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/v1/reports/id1/session", { method: "DELETE" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server_desktop && pnpm test -- reportsAdmin`
Expected: FAIL — module `./reportsAdmin` not found.

- [ ] **Step 3: Create the client**

Create `apps/server_desktop/src/reportsAdmin.ts`:

```typescript
const API = "/api/v1";

export type ReportRow = {
  session_id: string;
  title: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  report_ready: boolean;
  summary_ready: boolean;
  size_bytes?: number;
};

export type ReportStorage = {
  total_bytes: number;
  session_count: number;
};

export type ReportKind = "report" | "summary";
export type ReportFmt = "md" | "html" | "docx" | "pdf";

function base(port: number): string {
  return `http://127.0.0.1:${port}`;
}

export async function listReports(port: number): Promise<ReportRow[]> {
  const r = await fetch(`${base(port)}${API}/reports?with_sizes=true`);
  if (!r.ok) throw new Error(`보고서 목록 조회 실패 (HTTP ${r.status})`);
  return ((await r.json()) as { items: ReportRow[] }).items;
}

export async function getReportStorage(port: number): Promise<ReportStorage> {
  const r = await fetch(`${base(port)}${API}/reports/storage`);
  if (!r.ok) throw new Error(`스토리지 정보 조회 실패 (HTTP ${r.status})`);
  return (await r.json()) as ReportStorage;
}

export function reportViewUrl(port: number, id: string, kind: ReportKind): string {
  const suffix = kind === "summary" ? "/summary/view" : "/view";
  return `${base(port)}${API}/reports/${id}${suffix}`;
}

export async function fetchReportBytes(
  port: number,
  id: string,
  kind: ReportKind,
  fmt: ReportFmt,
): Promise<Uint8Array> {
  const seg = kind === "summary" ? "/summary/download" : "/download";
  const r = await fetch(`${base(port)}${API}/reports/${id}${seg}?fmt=${fmt}`);
  if (!r.ok) throw new Error(`다운로드 실패 (HTTP ${r.status})`);
  return new Uint8Array(await r.arrayBuffer());
}

export async function deleteReportFiles(port: number, id: string): Promise<void> {
  const r = await fetch(`${base(port)}${API}/reports/${id}/files`, { method: "DELETE" });
  if (!r.ok && r.status !== 204) throw new Error(`보고서 파일 삭제 실패 (HTTP ${r.status})`);
}

export async function deleteReportSession(port: number, id: string): Promise<void> {
  const r = await fetch(`${base(port)}${API}/reports/${id}/session`, { method: "DELETE" });
  if (!r.ok && r.status !== 204) throw new Error(`세션 삭제 실패 (HTTP ${r.status})`);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/server_desktop && pnpm test -- reportsAdmin`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/server_desktop/src/reportsAdmin.ts apps/server_desktop/src/reportsAdmin.test.ts
git commit -m "feat(server-console): 보고서 관리 loopback API 클라이언트"
```

---

### Task 8: `ReportsPanel.tsx` UI (목록 + 리뷰 뷰어 + 익스포트 + 2단계 삭제)

**Files:**
- Create: `apps/server_desktop/src/ReportsPanel.tsx`

**Interfaces:**
- Consumes: `reportsAdmin.ts` (Task 7), Tauri save dialog + fs (as used by existing panels).
- Produces: `export function ReportsPanel({ serverPort, running }: { serverPort: number | null; running: boolean })`

> This task is UI wiring (no unit test — verified via build + manual check in Task 10). Follow `VideoJobsPanel.tsx` structure exactly: `refresh()` via `Promise.all`, inline-confirm state, `formatBytes`, guards on `serverPort`/`running`.

- [ ] **Step 1: Look at the reference panel**

Read `apps/server_desktop/src/VideoJobsPanel.tsx` in full to copy its layout, class names, `formatBytes`, `errText` import, inline-confirm pattern, and Tauri save-dialog usage (for the export buttons).

- [ ] **Step 2: Create the panel**

Create `apps/server_desktop/src/ReportsPanel.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { save } from "@tauri-apps/plugin-dialog";
import { writeFile } from "@tauri-apps/plugin-fs";
import {
  listReports,
  getReportStorage,
  reportViewUrl,
  fetchReportBytes,
  deleteReportFiles,
  deleteReportSession,
  type ReportRow,
  type ReportStorage,
  type ReportKind,
  type ReportFmt,
} from "./reportsAdmin";
import { errText } from "./errors";

function formatBytes(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)} GB`;
  if (n >= 1_000_000) return `${Math.round(n / 1_000_000)} MB`;
  if (n >= 1000) return `${Math.round(n / 1000)} KB`;
  return `${n} B`;
}

const FORMATS: ReportFmt[] = ["md", "html", "docx", "pdf"];

type Props = { serverPort: number | null; running: boolean };

export function ReportsPanel({ serverPort, running }: Props) {
  const [rows, setRows] = useState<ReportRow[]>([]);
  const [storage, setStorage] = useState<ReportStorage | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [viewKind, setViewKind] = useState<ReportKind>("report");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmFiles, setConfirmFiles] = useState<string | null>(null);
  const [confirmSession, setConfirmSession] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (serverPort == null) return;
    try {
      const [r, st] = await Promise.all([listReports(serverPort), getReportStorage(serverPort)]);
      setRows(r);
      setStorage(st);
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [serverPort]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onExport = useCallback(
    async (id: string, kind: ReportKind, fmt: ReportFmt) => {
      if (serverPort == null) return;
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const bytes = await fetchReportBytes(serverPort, id, kind, fmt);
        const path = await save({ defaultPath: `${kind}-${id}.${fmt}` });
        if (path) {
          await writeFile(path, bytes);
          setNotice("저장됨");
        }
      } catch (e) {
        setError(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [serverPort],
  );

  const onDeleteFiles = useCallback(
    async (id: string) => {
      if (serverPort == null) return;
      setConfirmFiles(null);
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        await deleteReportFiles(serverPort, id);
        setNotice("보고서 파일 삭제됨");
        await refresh();
      } catch (e) {
        setError(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [serverPort, refresh],
  );

  const onDeleteSession = useCallback(
    async (id: string) => {
      if (serverPort == null) return;
      setConfirmSession(null);
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        await deleteReportSession(serverPort, id);
        if (selected === id) setSelected(null);
        setNotice("세션 전체 삭제됨");
        await refresh();
      } catch (e) {
        setError(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [serverPort, refresh, selected],
  );

  if (!running || serverPort == null) {
    return <p className="muted">서버가 실행 중일 때 보고서를 관리할 수 있습니다.</p>;
  }

  return (
    <div className="reportsPanel">
      <div className="panelHeader">
        <button onClick={() => void refresh()} disabled={busy}>새로고침</button>
        {storage && (
          <span className="muted">
            {storage.session_count}개 세션 · {formatBytes(storage.total_bytes)}
          </span>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}

      <table className="reportsTable">
        <thead>
          <tr><th>제목</th><th>상태</th><th>시작</th><th>크기</th><th>동작</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.session_id} className={selected === r.session_id ? "selected" : ""}>
              <td>{r.title}</td>
              <td>{r.report_ready ? "준비됨" : "미준비"}</td>
              <td>{r.started_at ? new Date(r.started_at).toLocaleString() : "-"}</td>
              <td>{r.size_bytes != null ? formatBytes(r.size_bytes) : "-"}</td>
              <td>
                <button onClick={() => { setSelected(r.session_id); setViewKind("report"); }}>
                  리뷰
                </button>
                {confirmFiles === r.session_id ? (
                  <>
                    <span>보고서 파일 삭제?</span>
                    <button onClick={() => void onDeleteFiles(r.session_id)} disabled={busy}>예</button>
                    <button onClick={() => setConfirmFiles(null)}>아니오</button>
                  </>
                ) : (
                  <button onClick={() => setConfirmFiles(r.session_id)} disabled={busy}>파일 삭제</button>
                )}
                {confirmSession === r.session_id ? (
                  <>
                    <span className="danger">세션·자막까지 영구 삭제. 계속?</span>
                    <button className="danger" onClick={() => void onDeleteSession(r.session_id)} disabled={busy}>영구 삭제</button>
                    <button onClick={() => setConfirmSession(null)}>취소</button>
                  </>
                ) : (
                  <button className="danger" onClick={() => setConfirmSession(r.session_id)} disabled={busy}>세션 삭제</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {selected && (
        <div className="reviewer">
          <div className="reviewerTabs">
            <button className={viewKind === "report" ? "active" : ""} onClick={() => setViewKind("report")}>보고서</button>
            <button className={viewKind === "summary" ? "active" : ""} onClick={() => setViewKind("summary")}>요약</button>
            <span className="spacer" />
            {FORMATS.map((f) => (
              <button key={f} onClick={() => void onExport(selected, viewKind, f)} disabled={busy}>
                {f.toUpperCase()}
              </button>
            ))}
            <button onClick={() => setSelected(null)}>닫기</button>
          </div>
          <iframe
            title="report-view"
            className="reviewerFrame"
            sandbox=""
            src={reportViewUrl(serverPort, selected, viewKind)}
          />
        </div>
      )}
    </div>
  );
}
```

Note: `errText` import path — confirm it matches `VideoJobsPanel.tsx`'s import (Step 1). If that file imports from a different path (e.g. `./appLog` or inline), match it. If Tauri fs/dialog plugin import names differ from `VideoJobsPanel`/`reportExport` usage, match the versions those files use.

- [ ] **Step 3: Type-check**

Run: `cd apps/server_desktop && pnpm exec tsc --noEmit`
Expected: no errors in `ReportsPanel.tsx` (unused-var or import-path errors → fix inline to match repo conventions).

- [ ] **Step 4: Commit**

```bash
git add apps/server_desktop/src/ReportsPanel.tsx
git commit -m "feat(server-console): 보고서 관리 패널 UI (리뷰/익스포트/2단계 삭제)"
```

---

### Task 9: `ServerConsole.tsx`에 "보고서 관리" 탭 배선

**Files:**
- Modify: `apps/server_desktop/src/ServerConsole.tsx`

**Interfaces:**
- Consumes: `ReportsPanel` (Task 8).

- [ ] **Step 1: Add the import**

Near the other panel imports (top of `ServerConsole.tsx`, lines ~7-11):

```tsx
import { ReportsPanel } from "./ReportsPanel";
```

- [ ] **Step 2: Extend the View union**

At line ~123, add `"reports"`:

```tsx
type View = "logs" | "config" | "devices" | "backup" | "reports" | "video";
```

- [ ] **Step 3: Add the nav item**

In `navItems` (line ~379), insert before the `video` entry:

```tsx
{ view: "reports", label: "보고서 관리" },
```

- [ ] **Step 4: Add the panel section**

Next to the other `<section hidden=...>` blocks (near line ~585), add:

```tsx
<section hidden={activeView !== "reports"} className="viewSection">
  <ReportsPanel serverPort={status?.port ?? null} running={running} />
</section>
```

Match the exact `className` and the `serverPort`/`running` prop expressions used by the adjacent `VideoJobsPanel` section (copy them verbatim from that line).

- [ ] **Step 5: Type-check + build**

Run: `cd apps/server_desktop && pnpm build:vite`
Expected: `tsc --noEmit && vite build` both succeed.

- [ ] **Step 6: Run the full frontend test suite**

Run: `cd apps/server_desktop && pnpm test`
Expected: ALL PASS (existing + reportsAdmin).

- [ ] **Step 7: Commit**

```bash
git add apps/server_desktop/src/ServerConsole.tsx
git commit -m "feat(server-console): 보고서 관리 탭 사이드바 배선"
```

---

### Task 10: frozen-bundle 재동결 + 수동 E2E 검증

**Files:** (no source edits — build + verify)

> `apps/server`에 라우터를 추가했으므로 번들 서버를 재동결하지 않으면 실제 서버앱에서 405가 난다(메모리 기록된 함정). tauri:dev도 번들 바이너리를 사용한다.

- [ ] **Step 1: Re-freeze the server bundle**

Run: `./build-server.sh` (repo 루트의 서버 동결 스크립트; 정확한 경로/이름은 리포 루트에서 확인).
Expected: 번들 재생성 성공.

- [ ] **Step 2: Restart the server app and open the console**

Run: `cd apps/server_desktop && pnpm tauri:dev`
Then in the console window, click the new **"보고서 관리"** tab.

- [ ] **Step 3: Manual verification checklist**

- [ ] 탭 클릭 시 세션 목록이 뜬다(제목·상태·시작·크기).
- [ ] "리뷰" 클릭 → 보고서 HTML이 iframe에 렌더된다. [요약] 탭 전환 시 요약(또는 "요약이 아직 없습니다")이 뜬다.
- [ ] MD/HTML/DOCX/PDF 버튼 → Tauri 저장 다이얼로그로 파일 저장된다.
- [ ] "파일 삭제" → 인라인 확인 → 삭제 후 목록 갱신. 세션은 남아있다.
- [ ] "세션 삭제" → 2단계 위험 확인 → 세션이 목록·(지식저장고 검색)에서 사라진다.

- [ ] **Step 4: Update docs**

지식저장고/서버 콘솔 매뉴얼이나 ROADMAP에 "보고서 관리" 탭 항목이 있으면 체크박스/설명을 같은 커밋에서 갱신(feedback_docs_after_slice 규칙).

- [ ] **Step 5: Commit any doc updates**

```bash
git add -A
git commit -m "docs(server-console): 보고서 관리 탭 매뉴얼 반영"
```

---

## Self-Review

**Spec coverage:**
- 목록 → Task 1. 스토리지 → Task 2. 리뷰(보고서+요약 탭) → Task 3 + Task 8. 익스포트(md/html/docx/pdf) → Task 4 + Task 8. 파일만 삭제 → Task 5. 세션 전체 삭제(FTS 정리) → Task 6. 탭 배선 → Task 9. 재동결 함정 → Task 10. ✓ 전 항목 커버.
- HTML 재사용 결정 → Task 3 `build_session_report_html`/`build_summary_html`. ✓
- FTS 정리 포함 결정 → Task 6 `DELETE FROM session_search_fts`. ✓

**Placeholder scan:** 모든 코드 스텝에 실제 코드 포함. Task 8/9는 참조 파일 관례 매칭 지시가 있으나(errText 경로, Tauri 플러그인 import, className), 이는 리포 관례 준수 지시이지 placeholder가 아님 — 대체 코드는 완전히 제시됨. ✓

**Type consistency:** `ReportRow`/`ReportStorage`/`ReportKind`/`ReportFmt`가 Task 7에서 정의되고 Task 8에서 그대로 사용. 백엔드 `session_id`(str) ↔ 프론트 `session_id` 일치. 삭제 함수명 `deleteReportFiles`/`deleteReportSession` Task 5·6 라우트(`/files`, `/session`)와 매칭. FTS 삭제 키 `str(session_pk)`가 인덱싱 시 키와 일치. ✓
