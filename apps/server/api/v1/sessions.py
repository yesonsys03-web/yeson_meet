# === ANCHOR: SESSIONS_START ===
"""Sessions router stub. Body implemented in S1-L1 (POST /sessions)."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, field_serializer
from sqlalchemy import func, select, text as sql_text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.deps import require_operator
from apps.server.db.models import AppUser, Session, SessionToken, Utterance
from apps.server.db.search import fts5_available, reindex_session_fts
from apps.server.db.session import AsyncSessionLocal, get_session
from apps.server.domain.events import SessionEnded, serialize
from apps.server.domain.report_docx import build_session_report_docx, build_summary_docx
from apps.server.domain.report_html import build_session_report_html, build_summary_html
from apps.server.domain.report_pdf import convert_docx_to_pdf
from apps.server.domain.reports import (
    regenerate_report_with_summary,
    report_path,
    summary_path,
    write_session_report,
)
from apps.server.ws.bus import bus

router = APIRouter(tags=["sessions"], prefix="/sessions")
logger = logging.getLogger(__name__)


# === ANCHOR: SESSIONS__VIEWER_BASE_START ===
def _viewer_base() -> str:
    """Resolve the viewer base URL with precedence: runtime file > env > default.

    The desktop's Go Live flow writes ``{STORAGE_ROOT}/viewer_base.txt`` to
    publish a public viewer base WITHOUT restarting the server, and deletes it on
    "stop public" to revert to the env/LAN value. The file is read fresh each
    call (never cached) and wins when present + non-empty. Reading is best-effort:
    any failure (missing/unreadable/empty) falls through to env ``VIEWER_BASE``
    then the localhost default — it must never raise.
    """
    try:
        from pathlib import Path

        override = Path(_storage_root()) / "viewer_base.txt"
        if override.exists():
            value = override.read_text(encoding="utf-8").strip()
            if value:
                return value
    except Exception:  # noqa: BLE001 — best-effort; fall through to env/default
        pass
    return os.environ.get("VIEWER_BASE", "http://localhost:5173")
# === ANCHOR: SESSIONS__VIEWER_BASE_END ===


# === ANCHOR: SESSIONS__STORAGE_ROOT_START ===
def _storage_root() -> str:
    return os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")
# === ANCHOR: SESSIONS__STORAGE_ROOT_END ===


# === ANCHOR: SESSIONS_SESSIONCREATEIN_START ===
class SessionCreateIn(BaseModel):
    title: str
    client_label: str | None = None
    visibility: str = "org"
# === ANCHOR: SESSIONS_SESSIONCREATEIN_END ===


# === ANCHOR: SESSIONS_SESSIONCREATEOUT_START ===
class SessionCreateOut(BaseModel):
    session_id: UUID
    viewer_url: str
# === ANCHOR: SESSIONS_SESSIONCREATEOUT_END ===


# === ANCHOR: SESSIONS_SESSIONENDOUT_START ===
class SessionEndOut(BaseModel):
    session_id: UUID
    status: str
    ended_at: datetime
    report_path: str

    @field_serializer("ended_at")
    def _serialize_utc(self, value: datetime) -> str:
        """Emit UTC-aware ISO so the client localizes correctly.

        Stored timestamps are NAIVE UTC; without a tz suffix the client's
        ``new Date(iso)`` reads them as LOCAL and shows the meeting end off by
        the UTC offset. Mirrors ``SessionListItem._serialize_utc``.
        """
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
# === ANCHOR: SESSIONS_SESSIONENDOUT_END ===


# === ANCHOR: SESSIONS_SESSIONLISTITEM_START ===
class SessionListItem(BaseModel):
    external_id: str
    title: str
    client_label: str | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    owner_user_id: int
    visibility: str
    utterance_count: int
    report_ready: bool
    # Present only when a search query (q) was supplied; omitted/empty otherwise.
    snippets: list[str] = []

    @field_serializer("started_at", "ended_at")
    def _serialize_utc(self, value: datetime | None) -> str | None:
        """Emit UTC-aware ISO (e.g. ``...T06:52:00+00:00``) for the timestamps.

        Stored timestamps are NAIVE UTC (the report path's ``_to_local``
        convention). Without a tz suffix the client's ``new Date(iso)`` reads
        them as LOCAL time and shows the meeting off by the UTC offset. Attaching
        ``timezone.utc`` when naive makes the serialized string unambiguous so
        the client converts to local correctly. Already-aware values pass
        through. Pure stdlib (bundle-safe).
        """
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
# === ANCHOR: SESSIONS_SESSIONLISTITEM_END ===


# === ANCHOR: SESSIONS_SESSIONLISTOUT_START ===
class SessionListOut(BaseModel):
    items: list[SessionListItem]
    has_more: bool
# === ANCHOR: SESSIONS_SESSIONLISTOUT_END ===


@router.post("", response_model=SessionCreateOut, status_code=status.HTTP_201_CREATED)
# === ANCHOR: SESSIONS_CREATE_SESSION_START ===
async def create_session(
    body: SessionCreateIn,
    user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
# === ANCHOR: SESSIONS_CREATE_SESSION_END ===
) -> SessionCreateOut:
    meeting = Session(
        external_id=uuid4(),
        owner_user_id=user.id,
        title=body.title,
        client_label=body.client_label,
        visibility=body.visibility,
        status="live",
    )
    db.add(meeting)
    await db.flush()

    viewer_token = secrets.token_urlsafe(32)
    token_row = SessionToken(
        session_id=meeting.id,
        token=viewer_token,
        kind="viewer",
    )
    db.add(token_row)
    await db.commit()

    return SessionCreateOut(
        session_id=meeting.external_id,
        viewer_url=f"{_viewer_base()}/v/{viewer_token}",
    )


# === ANCHOR: SESSIONS__GET_OPERATOR_SESSION_OR_404_START ===
async def _get_operator_session_or_404(
    db: AsyncSession,
    external_id: UUID,
# === ANCHOR: SESSIONS__GET_OPERATOR_SESSION_OR_404_END ===
) -> Session:
    meeting = (
        await db.execute(select(Session).where(Session.external_id == external_id))
    ).scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return meeting


# === ANCHOR: SESSIONS__SESSION_UTTERANCES_START ===
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
# === ANCHOR: SESSIONS__SESSION_UTTERANCES_END ===


def _snap_meeting(meeting: Session) -> object:
    """Return a plain-object snapshot of the fields used by report builders.

    BackgroundTasks run after the request DB session is closed, so ORM objects
    become detached.  Copying only the fields we need avoids DetachedInstanceError.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        title=meeting.title,
        external_id=meeting.external_id,
        status=meeting.status,
        started_at=meeting.started_at,
        ended_at=meeting.ended_at,
        client_label=meeting.client_label,
    )


def _snap_utterances(utterances: list[Utterance]) -> list[object]:
    """Return plain-object snapshots of utterances used by report builders."""
    from types import SimpleNamespace

    return [
        SimpleNamespace(
            seq=u.seq,
            speaker=u.speaker,
            text_en=u.text_en,
            text_ko=u.text_ko,
            started_at=u.started_at,
            ended_at=u.ended_at,
        )
        for u in utterances
    ]


# === ANCHOR: SESSIONS_INDEX_SEARCH_FTS_START ===
async def _index_session_search_fts(external_id: UUID, summary: str | None) -> None:
    """Re-index one ended session's transcript + summary into the FTS5 table.

    Dedicated background task with its OWN ``AsyncSessionLocal`` (the request
    ``db`` is already committed/closed by the time this runs — mirrors the
    own-session pattern in ws/sidecar.py). Idempotent: ``reindex_session_fts``
    deletes the session's rows then re-inserts. No-ops entirely when FTS5 is
    unavailable. MUST NOT run on the live caption path — only at meeting-end.
    """
    try:
        async with AsyncSessionLocal() as db:
            if not await fts5_available(db):
                return
            meeting = (
                await db.execute(
                    select(Session).where(Session.external_id == external_id)
                )
            ).scalar_one_or_none()
            if meeting is None:
                return
            rows = (
                await db.execute(
                    select(Utterance.text_ko, Utterance.text_en)
                    .where(
                        Utterance.session_id == meeting.id,
                        Utterance.is_final == True,  # noqa: E712
                    )
                    .order_by(Utterance.seq.asc())
                )
            ).all()
            utterances = [(r[0], r[1]) for r in rows]
            await reindex_session_fts(db, meeting.id, utterances, summary)
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort, never break end flow
        logger.warning("_index_session_search_fts failed for %s: %s", external_id, exc)
# === ANCHOR: SESSIONS_INDEX_SEARCH_FTS_END ===


# === ANCHOR: SESSIONS_FINALIZE_REPORT_AND_INDEX_START ===
async def _finalize_report_and_index(
    storage_root: str,
    snap_meeting: object,
    snap_utts: list[object],
    external_id: UUID,
) -> None:
    """Meeting-end background task: emit reports (+LLM summary) then index FTS.

    Runs ``regenerate_report_with_summary`` (the single report-emission point,
    returns the summary text) and then re-indexes the session's FTS rows with
    that summary. Both steps are off the request path and off the live caption
    path. Indexing failure never affects report emission.
    """
    summary = await regenerate_report_with_summary(storage_root, snap_meeting, snap_utts)
    await _index_session_search_fts(external_id, summary)
# === ANCHOR: SESSIONS_FINALIZE_REPORT_AND_INDEX_END ===


@router.post("/{external_id}/end", response_model=SessionEndOut)
# === ANCHOR: SESSIONS_END_SESSION_START ===
async def end_session(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
    background_tasks: BackgroundTasks,
# === ANCHOR: SESSIONS_END_SESSION_END ===
) -> SessionEndOut:
    meeting = await _get_operator_session_or_404(db, external_id)
    if meeting.status != "ended":
        meeting.status = "ended"
        meeting.ended_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(meeting)
    elif meeting.ended_at is None:
        meeting.ended_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(meeting)

    utterances = await _session_utterances(db, meeting.id)

    # Take ORM snapshots before DB session closes (detached-object safety)
    snap_meeting = _snap_meeting(meeting)
    snap_utts = _snap_utterances(utterances)

    # Report files (md/html/docx/pdf + summary) are emitted off the request path
    # by the background task below — ending stays fast and never blocks on the
    # LibreOffice PDF conversion or the LLM summary. GET /report* regenerates
    # on demand, so the path is valid even before the background write finishes.
    storage_root = _storage_root()
    path = report_path(storage_root, str(meeting.external_id), "md")

    # Background: emit all report formats (with LLM summary when available),
    # then re-index this session's transcript + summary into the FTS5 search
    # index (S1b). Both run off the request path and off the live caption path.
    background_tasks.add_task(
        _finalize_report_and_index,
        storage_root,
        snap_meeting,
        snap_utts,
        meeting.external_id,
    )

    await bus.publish(
        meeting.external_id,
        serialize(
            SessionEnded(
                session_id=meeting.external_id,
                occurred_at=meeting.ended_at,
                ended_at=meeting.ended_at,
            )
        ),
    )
    return SessionEndOut(
        session_id=meeting.external_id,
        status=meeting.status,
        ended_at=meeting.ended_at,
        report_path=str(path),
    )


# === ANCHOR: SESSIONS_LIST__SNIPPETS_PER_SESSION_START ===
# Max snippet rows surfaced per session in search results (across utterance+summary).
_MAX_SNIPPETS_PER_SESSION = 3
# Half-window (chars) on each side of a LIKE match for the Python-windowed snippet.
_LIKE_SNIPPET_HALF = 60
# === ANCHOR: SESSIONS_LIST__SNIPPETS_PER_SESSION_END ===


# === ANCHOR: SESSIONS_LIST__UTTERANCE_COUNTS_START ===
async def _utterance_counts(db: AsyncSession, session_pks: list[int]) -> dict[int, int]:
    """Return {session_pk: count of is_final utterances} for the given sessions."""
    if not session_pks:
        return {}
    rows = (
        await db.execute(
            select(Utterance.session_id, func.count())
            .where(
                Utterance.session_id.in_(session_pks),
                Utterance.is_final == True,  # noqa: E712
            )
            .group_by(Utterance.session_id)
        )
    ).all()
    return {pk: cnt for pk, cnt in rows}
# === ANCHOR: SESSIONS_LIST__UTTERANCE_COUNTS_END ===


# === ANCHOR: SESSIONS_LIST__TO_ITEM_START ===
def _to_list_item(
    meeting: Session,
    utterance_count: int,
    snippets: list[str] | None,
) -> SessionListItem:
    """Build a SessionListItem. report_ready is derived from status (NOT disk)."""
    return SessionListItem(
        external_id=str(meeting.external_id),
        title=meeting.title,
        client_label=meeting.client_label,
        status=meeting.status,
        started_at=meeting.started_at,
        ended_at=meeting.ended_at,
        owner_user_id=meeting.owner_user_id,
        visibility=meeting.visibility,
        utterance_count=utterance_count,
        report_ready=(meeting.status == "ended"),
        snippets=snippets or [],
    )
# === ANCHOR: SESSIONS_LIST__TO_ITEM_END ===


# === ANCHOR: SESSIONS_LIST__STATUS_FILTER_START ===
def _apply_status_scope(stmt, status_filter: str, scope: str, user_id: int):
    """Apply the status + scope filters to a Session select."""
    if status_filter == "ended":
        stmt = stmt.where(Session.status == "ended")
    elif status_filter == "live":
        stmt = stmt.where(Session.status != "ended")
    # status == "all" → no status filter.
    if scope == "mine":
        stmt = stmt.where(Session.owner_user_id == user_id)
    return stmt
# === ANCHOR: SESSIONS_LIST__STATUS_FILTER_END ===


# === ANCHOR: SESSIONS_LIST__FTS_MATCH_QUERY_START ===
def _build_fts_match_query(query: str) -> str:
    """Turn raw user input into a safe FTS5 MATCH expression.

    FTS5 treats ``- : " * AND OR NOT`` and parentheses as query syntax, so a
    perfectly ordinary phrase like ``action-item``, ``Q3:`` or ``R&D`` would
    raise an OperationalError (→ HTTP 500) if passed through raw. We neutralize
    that by tokenizing on whitespace and wrapping each token in double-quotes
    (doubling any embedded ``"``), then ANDing them with a space. The result is a
    literal phrase-per-token match — no operators leak through. Empty input
    yields an empty string (caller returns no results).
    """
    tokens = [f'"{t.replace(chr(34), chr(34) * 2)}"' for t in query.split() if t]
    return " ".join(tokens)
# === ANCHOR: SESSIONS_LIST__FTS_MATCH_QUERY_END ===


# === ANCHOR: SESSIONS_LIST__FTS_SEARCH_START ===
async def _fts_search_session_pks(
    db: AsyncSession, query: str
) -> list[tuple[int, list[str]]]:
    """FTS5 MATCH → ranked [(session_pk, snippets)] across utterance+summary rows.

    GROUP BY session_id (stored in the standalone table), rank by best bm25 per
    session, and collect up to _MAX_SNIPPETS_PER_SESSION highlighted snippets.
    """
    match_query = _build_fts_match_query(query)
    if not match_query:
        return []
    try:
        rows = (
            await db.execute(
                sql_text(
                    "SELECT session_id, "
                    "snippet(session_search_fts, 2, '[', ']', '…', 12) AS snip, "
                    "bm25(session_search_fts) AS rank "
                    "FROM session_search_fts "
                    "WHERE session_search_fts MATCH :q "
                    # TODO: bound per-keystroke work; raise/window if corpus grows.
                    "ORDER BY rank LIMIT 500"
                ),
                {"q": match_query},
            )
        ).all()
    except OperationalError:
        # Belt-and-suspenders: any residual FTS5 syntax error degrades to no hits
        # rather than a 500 (the safe-query builder above should prevent it).
        return []
    ordered_pks: list[int] = []
    snippets: dict[int, list[str]] = {}
    best_rank: dict[int, float] = {}
    for session_id_str, snip, rank in rows:
        pk = int(session_id_str)
        if pk not in snippets:
            snippets[pk] = []
            ordered_pks.append(pk)
            best_rank[pk] = rank
        if snip and len(snippets[pk]) < _MAX_SNIPPETS_PER_SESSION:
            snippets[pk].append(snip)
    # Re-order sessions by their best (lowest) bm25 rank.
    ordered_pks.sort(key=lambda pk: best_rank[pk])
    return [(pk, snippets[pk]) for pk in ordered_pks]
# === ANCHOR: SESSIONS_LIST__FTS_SEARCH_END ===


# === ANCHOR: SESSIONS_LIST__LIKE_SNIPPET_START ===
def _like_snippet(body: str, needle_lower: str) -> str | None:
    """Return a Python-windowed snippet around the first case-insensitive hit."""
    idx = body.lower().find(needle_lower)
    if idx < 0:
        return None
    start = max(0, idx - _LIKE_SNIPPET_HALF)
    end = min(len(body), idx + len(needle_lower) + _LIKE_SNIPPET_HALF)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return prefix + body[start:end].strip() + suffix
# === ANCHOR: SESSIONS_LIST__LIKE_SNIPPET_END ===


# === ANCHOR: SESSIONS_LIST__LIKE_SEARCH_START ===
async def _like_search_session_pks(
    db: AsyncSession, query: str, candidate_pks: list[int]
) -> list[tuple[int, list[str]]]:
    """LIKE fallback: scan is_final utterances (KO/EN) + on-disk summaries.

    Returns [(session_pk, snippets)] for sessions among ``candidate_pks`` that
    match, ordered by candidate order (which is started_at desc). Identical
    output shape to the FTS path so the client never branches.
    """
    if not candidate_pks:
        return []
    needle = query.lower()
    snippets: dict[int, list[str]] = {}

    # Transcript scan (DB-side LIKE narrows rows; Python builds the snippet).
    # Escape LIKE wildcards so a user query containing % or _ is matched
    # literally (otherwise `_` would match any char and over-match everything).
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{escaped}%"
    lowered = func.lower(like)
    utt_rows = (
        await db.execute(
            select(Utterance.session_id, Utterance.text_ko, Utterance.text_en)
            .where(
                Utterance.session_id.in_(candidate_pks),
                Utterance.is_final == True,  # noqa: E712
                func.lower(Utterance.text_ko).like(lowered, escape="\\")
                | func.lower(Utterance.text_en).like(lowered, escape="\\"),
            )
            .order_by(Utterance.session_id.asc(), Utterance.seq.asc())
        )
    ).all()
    for session_id, text_ko, text_en in utt_rows:
        body = "\n".join(p for p in (text_ko, text_en) if p)
        snip = _like_snippet(body, needle)
        bucket = snippets.setdefault(session_id, [])
        if snip and len(bucket) < _MAX_SNIPPETS_PER_SESSION:
            bucket.append(snip)

    # Summary scan (on-disk summary.md per candidate session).
    meta = (
        await db.execute(
            select(Session.id, Session.external_id).where(Session.id.in_(candidate_pks))
        )
    ).all()
    for pk, external_id in meta:
        bucket = snippets.get(pk, [])
        if len(bucket) >= _MAX_SNIPPETS_PER_SESSION:
            continue
        try:
            summary = _read_summary_text_or_404(str(external_id))
        except HTTPException:
            continue
        if needle in summary.lower():
            snip = _like_snippet(summary, needle)
            if snip:
                snippets.setdefault(pk, []).append(snip)

    # Preserve candidate (started_at desc) order; only matched sessions kept.
    return [(pk, snippets[pk]) for pk in candidate_pks if pk in snippets]
# === ANCHOR: SESSIONS_LIST__LIKE_SEARCH_END ===


@router.get("", response_model=SessionListOut)
# === ANCHOR: SESSIONS_LIST_SESSIONS_START ===
async def list_sessions(
    user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: Annotated[str, Query(alias="status")] = "ended",
    scope: str = "all",
# === ANCHOR: SESSIONS_LIST_SESSIONS_END ===
) -> SessionListOut:
    """List past meetings (no q) or full-text-search them (q set).

    No q: paginated list ordered started_at desc. With q: FTS5 ranked results
    with snippet() highlights over transcript + summary, or an identical-shape
    LIKE fallback when FTS5 is unavailable. report_ready derives from status.
    """
    fetch = limit + 1  # +1 sentinel row to compute has_more
    query = (q or "").strip()

    if not query:
        # ── plain list path (dialect-agnostic ORM) ──────────────────────────
        stmt = select(Session)
        stmt = _apply_status_scope(stmt, status_filter, scope, user.id)
        stmt = stmt.order_by(Session.started_at.desc()).offset(offset).limit(fetch)
        meetings = list((await db.execute(stmt)).scalars().all())
        has_more = len(meetings) > limit
        meetings = meetings[:limit]
        counts = await _utterance_counts(db, [m.id for m in meetings])
        items = [_to_list_item(m, counts.get(m.id, 0), None) for m in meetings]
        return SessionListOut(items=items, has_more=has_more)

    # ── search path ─────────────────────────────────────────────────────────
    if await fts5_available(db):
        ranked = await _fts_search_session_pks(db, query)
    else:
        # LIKE fallback: candidate set = all sessions matching status/scope,
        # ordered started_at desc, then filtered to those whose text matches.
        cand_stmt = select(Session.id)
        cand_stmt = _apply_status_scope(cand_stmt, status_filter, scope, user.id)
        cand_stmt = cand_stmt.order_by(Session.started_at.desc())
        candidate_pks = list((await db.execute(cand_stmt)).scalars().all())
        ranked = await _like_search_session_pks(db, query, candidate_pks)

    # Load metadata for the ranked pks and re-apply status/scope (FTS path has
    # not filtered yet; LIKE path already restricted candidates but re-filtering
    # is a cheap no-op that keeps both paths uniform).
    ranked_pks = [pk for pk, _ in ranked]
    snippet_map = {pk: snips for pk, snips in ranked}
    meta_map: dict[int, Session] = {}
    if ranked_pks:
        meta_stmt = select(Session).where(Session.id.in_(ranked_pks))
        meta_stmt = _apply_status_scope(meta_stmt, status_filter, scope, user.id)
        for m in (await db.execute(meta_stmt)).scalars().all():
            meta_map[m.id] = m

    filtered_pks = [pk for pk in ranked_pks if pk in meta_map]
    page_pks = filtered_pks[offset : offset + fetch]
    has_more = len(page_pks) > limit
    page_pks = page_pks[:limit]
    counts = await _utterance_counts(db, page_pks)
    items = [
        _to_list_item(meta_map[pk], counts.get(pk, 0), snippet_map.get(pk, []))
        for pk in page_pks
    ]
    return SessionListOut(items=items, has_more=has_more)


@router.get("/{external_id}/report")
# === ANCHOR: SESSIONS_DOWNLOAD_SESSION_REPORT_START ===
async def download_session_report(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
# === ANCHOR: SESSIONS_DOWNLOAD_SESSION_REPORT_END ===
) -> FileResponse:
    meeting = await _get_operator_session_or_404(db, external_id)
    path = report_path(_storage_root(), str(meeting.external_id))
    if not path.exists():
        if meeting.status != "ended":
            raise HTTPException(status.HTTP_409_CONFLICT, "Session has not ended")
        utterances = await _session_utterances(db, meeting.id)
        path = write_session_report(_storage_root(), meeting, utterances)
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{meeting.external_id}.md",
    )


@router.get("/{external_id}/report.html")
async def download_session_report_html(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    meeting = await _get_operator_session_or_404(db, external_id)
    if meeting.status != "ended":
        raise HTTPException(status.HTTP_409_CONFLICT, "Session has not ended")
    utterances = await _session_utterances(db, meeting.id)
    html_content = build_session_report_html(meeting, utterances)
    return Response(
        content=html_content,
        media_type="text/html; charset=utf-8",
    )


@router.get("/{external_id}/report.docx")
async def download_session_report_docx(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    meeting = await _get_operator_session_or_404(db, external_id)
    if meeting.status != "ended":
        raise HTTPException(status.HTTP_409_CONFLICT, "Session has not ended")
    utterances = await _session_utterances(db, meeting.id)
    docx_bytes = build_session_report_docx(meeting, utterances)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=\"report.docx\""},
    )


@router.get("/{external_id}/report.pdf")
async def download_session_report_pdf(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    meeting = await _get_operator_session_or_404(db, external_id)
    if meeting.status != "ended":
        raise HTTPException(status.HTTP_409_CONFLICT, "Session has not ended")
    utterances = await _session_utterances(db, meeting.id)
    docx_bytes = build_session_report_docx(meeting, utterances)
    pdf_bytes = await asyncio.to_thread(convert_docx_to_pdf, docx_bytes)
    if pdf_bytes is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF 변환 불가 — 서버에 LibreOffice(soffice)가 설치되어 있지 않습니다.",
        )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=\"report.pdf\""},
    )


@router.get("/{external_id}/report.summary")
async def download_session_report_summary(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Return the standalone summary.md file if available.

    The summary is generated asynchronously after session end.
    Returns 404 with a descriptive message if not yet available.
    """
    meeting = await _get_operator_session_or_404(db, external_id)
    path = summary_path(_storage_root(), str(meeting.external_id))
    if not path.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="요약이 아직 생성되지 않았습니다. 회의 종료 후 잠시 후 다시 시도하세요.",
        )
    return Response(
        content=path.read_text(encoding="utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=\"summary.md\""},
    )


# === ANCHOR: SESSIONS__READ_SUMMARY_TEXT_START ===
def _read_summary_text_or_404(session_id: str) -> str:
    """Read the stored summary.md and return the raw summary body (no md header).

    Raises 404 if no summary has been generated yet.  The stored file prepends a
    ``# 요약 — {title}`` markdown H1; it is stripped so HTML/DOCX/PDF builders
    receive only the summary body.
    """
    path = summary_path(_storage_root(), session_id, "md")
    if not path.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="요약이 아직 생성되지 않았습니다. 회의 종료 후 잠시 후 다시 시도하세요.",
        )
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[0].startswith("# 요약"):
        # Drop the H1 header and one following blank line if present.
        lines = lines[1:]
        if lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()
# === ANCHOR: SESSIONS__READ_SUMMARY_TEXT_END ===


@router.get("/{external_id}/report.summary.html")
async def download_session_report_summary_html(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    meeting = await _get_operator_session_or_404(db, external_id)
    summary = _read_summary_text_or_404(str(meeting.external_id))
    return Response(
        content=build_summary_html(meeting, summary),
        media_type="text/html; charset=utf-8",
    )


@router.get("/{external_id}/report.summary.docx")
async def download_session_report_summary_docx(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    meeting = await _get_operator_session_or_404(db, external_id)
    summary = _read_summary_text_or_404(str(meeting.external_id))
    return Response(
        content=build_summary_docx(meeting, summary),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=\"summary.docx\""},
    )


@router.get("/{external_id}/report.summary.pdf")
async def download_session_report_summary_pdf(
    external_id: UUID,
    _user: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    meeting = await _get_operator_session_or_404(db, external_id)
    summary = _read_summary_text_or_404(str(meeting.external_id))
    docx_bytes = build_summary_docx(meeting, summary)
    pdf_bytes = await asyncio.to_thread(convert_docx_to_pdf, docx_bytes)
    if pdf_bytes is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF 변환 불가 — 서버에 LibreOffice(soffice)가 설치되어 있지 않습니다.",
        )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=\"summary.pdf\""},
    )
# === ANCHOR: SESSIONS_END ===
