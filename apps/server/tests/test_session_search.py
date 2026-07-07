"""Meeting knowledge repository — FTS5 search index + list/search endpoint.

Two layers:

1. SQLite-side tests build their OWN engines (like test_db_portability.py),
   because the conftest ``client``/``db_session`` fixtures are Postgres-only and
   FTS5 is a SQLite construct. These cover the migration, create_schema FTS hook,
   the fts5_available probe, reindex idempotency, and FTS ranking/snippet over
   utterance AND summary rows.

2. Endpoint tests use the conftest Postgres ``client`` fixture. Postgres has no
   FTS5, so the search path there exercises the LIKE fallback — proving the
   fallback returns the identical SessionListOut/SessionListItem shape. Plus list
   ordering, pagination/has_more, scope, report_ready, auth, and the
   ended-zero-utterance edge.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from apps.server.db.base import Base
from apps.server.db.models import AppUser, Session, Utterance
from apps.server.db.search import (
    ensure_session_search_fts,
    fts5_available,
    reindex_session_fts,
)


# ─────────────────────────────────────────────────────────────────────────────
# SQLite helpers (self-built engines — no conftest PG dependency)
# ─────────────────────────────────────────────────────────────────────────────
def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "m0003",
        "apps/server/db/alembic/versions/0003_session_search_fts.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _sqlite_engine(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fts.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(ensure_session_search_fts)
    return engine


# ─────────────────────────────────────────────────────────────────────────────
# S3 — fts5_available probe
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fts5_available_true_on_sqlite(tmp_path: Path) -> None:
    engine = await _sqlite_engine(tmp_path)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db:
        assert await fts5_available(db) is True
    await engine.dispose()


def test_fts5_available_false_on_postgres_dialect() -> None:
    """Non-SQLite dialects must report FTS5 absent (probe short-circuits)."""

    class _FakeDialect:
        name = "postgresql"

    class _FakeConn:
        dialect = _FakeDialect()

        class engine:  # noqa: N801
            url = "postgresql://x"

    # _probe_fts5 returns False for non-sqlite without touching the connection.
    from apps.server.db.search import _probe_fts5

    assert _probe_fts5(_FakeConn()) is False


# ─────────────────────────────────────────────────────────────────────────────
# S1 — migration upgrade/downgrade + backfill (SQLite)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_migration_upgrade_creates_table_and_backfills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    m = _load_migration()

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mig.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Seed a session + is_final utterances + a legacy on-disk summary.
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    ext = uuid4()
    now = datetime.now(timezone.utc)
    async with factory() as db:
        user = AppUser(email="m@m", name="M", password_hash="x", role="operator")
        db.add(user)
        await db.flush()
        meeting = Session(
            external_id=ext, owner_user_id=user.id, title="T", status="ended",
            started_at=now, ended_at=now,
        )
        db.add(meeting)
        await db.flush()
        db.add(Utterance(
            session_id=meeting.id, seq=1, speaker=None,
            text_en="Send the report by Friday", text_ko="금요일까지 보고서",
            started_at=now, ended_at=now, is_final=True,
        ))
        # a non-final utterance must NOT be indexed
        db.add(Utterance(
            session_id=meeting.id, seq=2, speaker=None,
            text_en="partial", text_ko="부분", started_at=now, ended_at=now,
            is_final=False,
        ))
        await db.commit()
        session_pk = meeting.id

    sdir = Path(tmp_path / "storage" / str(ext))
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "summary.md").write_text("# 요약 — T\n\n핵심 요약 본문\n", encoding="utf-8")
    assert session_pk  # sanity

    # Drive the real upgrade()/downgrade() through an alembic MigrationContext so
    # op.get_bind() resolves to our sqlite connection (no monkeypatching of op).
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    def _run_upgrade(sync_conn) -> None:
        ctx = MigrationContext.configure(sync_conn)
        with Operations.context(ctx):
            m.upgrade()

    def _run_downgrade(sync_conn) -> None:
        ctx = MigrationContext.configure(sync_conn)
        with Operations.context(ctx):
            m.downgrade()

    async with engine.begin() as conn:
        await conn.run_sync(_run_upgrade)

    async with factory() as db:
        n_utt = (await db.execute(
            text("SELECT count(*) FROM session_search_fts WHERE kind='utterance'")
        )).scalar()
        n_sum = (await db.execute(
            text("SELECT count(*) FROM session_search_fts WHERE kind='summary'")
        )).scalar()
    # exactly the one is_final utterance + the one on-disk summary
    assert n_utt == 1
    assert n_sum == 1

    # downgrade drops the table cleanly
    async with engine.begin() as conn:
        await conn.run_sync(_run_downgrade)
        exists = await conn.run_sync(
            lambda c: c.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE name='session_search_fts'"
            ).scalar()
        )
    assert exists == 0
    await engine.dispose()


def test_migration_noop_on_postgres_dialect() -> None:
    """upgrade()/downgrade() must no-op (not raise) on a non-sqlite bind."""
    m = _load_migration()

    class _Bind:
        class dialect:  # noqa: N801
            name = "postgresql"

    import apps.server.db.alembic.versions  # noqa: F401
    import types

    # Patch alembic.op.get_bind to return our fake non-sqlite bind.
    from alembic import op as alembic_op

    orig = alembic_op.get_bind
    alembic_op.get_bind = lambda: _Bind()  # type: ignore[assignment]
    try:
        # Should return immediately without touching any DDL.
        m.upgrade()
        m.downgrade()
    finally:
        alembic_op.get_bind = orig


# ─────────────────────────────────────────────────────────────────────────────
# S3/S1b — reindex idempotency + FTS ranking/snippet over utterance AND summary
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_reindex_idempotent_and_summary_indexed(tmp_path: Path) -> None:
    engine = await _sqlite_engine(tmp_path)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    from apps.server.api.v1.sessions import _fts_search_session_pks

    async with factory() as db:
        await reindex_session_fts(
            db, 42,
            [("금요일까지 보고서 보내주세요", "Send the report by Friday")],
            "핵심 요약: the deadline summary highlights the budget",
        )
        await db.commit()

        # English token match (transcript)
        res = await _fts_search_session_pks(db, "Friday")
        assert res and res[0][0] == 42
        assert any("Friday" in s for s in res[0][1])

        # Summary row is searchable too (a word that appears ONLY in the summary).
        res_sum = await _fts_search_session_pks(db, "deadline")
        assert res_sum and res_sum[0][0] == 42

        # Re-index with fewer rows → old rows gone (idempotent by session)
        await reindex_session_fts(db, 42, [("회의 종료", "Meeting over")], None)
        await db.commit()
        total = (await db.execute(
            text("SELECT count(*) FROM session_search_fts WHERE session_id='42'")
        )).scalar()
        assert total == 1  # one utterance, no summary
    await engine.dispose()


@pytest.mark.asyncio
async def test_fts_row_deleted_by_session_pk_key(tmp_path: Path) -> None:
    """Mirrors the DELETE /api/v1/reports/{external_id}/session FTS-cleanup line:
    ``DELETE FROM session_search_fts WHERE session_id = :sid`` with
    ``sid = str(session_pk)``. Never exercised on the Postgres-backed conftest
    suite (fts5_available is False there) but this IS the branch that runs in
    the SQLite production bundle — needs its own direct SQLite proof."""
    engine = await _sqlite_engine(tmp_path)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as db:
        await reindex_session_fts(db, 11, [("안녕", "hello")], "요약")
        await reindex_session_fts(db, 22, [("반가워", "hi there")], "요약2")
        await db.commit()

        # Both sessions' rows exist before the delete.
        n_11 = (await db.execute(
            text("SELECT count(*) FROM session_search_fts WHERE session_id = :sid"),
            {"sid": str(11)},
        )).scalar()
        n_22 = (await db.execute(
            text("SELECT count(*) FROM session_search_fts WHERE session_id = :sid"),
            {"sid": str(22)},
        )).scalar()
        assert n_11 > 0
        assert n_22 > 0

        # The exact statement the endpoint runs on meeting deletion.
        await db.execute(
            text("DELETE FROM session_search_fts WHERE session_id = :sid"),
            {"sid": str(11)},
        )
        await db.commit()

        n_11_after = (await db.execute(
            text("SELECT count(*) FROM session_search_fts WHERE session_id = :sid"),
            {"sid": str(11)},
        )).scalar()
        n_22_after = (await db.execute(
            text("SELECT count(*) FROM session_search_fts WHERE session_id = :sid"),
            {"sid": str(22)},
        )).scalar()
        assert n_11_after == 0
        assert n_22_after == n_22  # a different session_pk's rows are untouched
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_schema_creates_fts_table(tmp_path: Path, monkeypatch) -> None:
    """The cold-bundle create_schema() path creates the FTS table on SQLite."""
    import apps.server.db.seed as seed_mod
    import apps.server.db.session as session_mod

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cold.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(seed_mod, "engine", engine, raising=True)
    monkeypatch.setattr(seed_mod, "AsyncSessionLocal", factory, raising=True)
    monkeypatch.setattr(session_mod, "AsyncSessionLocal", factory, raising=True)

    await seed_mod.create_schema()
    async with factory() as db:
        exists = (await db.execute(
            text("SELECT count(*) FROM sqlite_master WHERE name='session_search_fts'")
        )).scalar()
    assert exists == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_schema_backfills_empty_fts_on_in_place_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-place bundle upgrade: an existing SQLite with is_final utterances but an
    empty session_search_fts must get backfilled by create_schema (the bundle
    never runs alembic). Warm re-run must not duplicate."""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    import apps.server.db.seed as seed_mod
    import apps.server.db.session as session_mod

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'upgrade.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(seed_mod, "engine", engine, raising=True)
    monkeypatch.setattr(seed_mod, "AsyncSessionLocal", factory, raising=True)
    monkeypatch.setattr(session_mod, "AsyncSessionLocal", factory, raising=True)

    # Simulate a PRE-feature DB: ORM tables + historical rows, but NO FTS table.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    now = datetime.now(timezone.utc)
    ext = uuid4()
    async with factory() as db:
        user = AppUser(email="up@up", name="U", password_hash="x", role="operator")
        db.add(user)
        await db.flush()
        meeting = Session(
            external_id=ext, owner_user_id=user.id, title="Legacy", status="ended",
            started_at=now, ended_at=now,
        )
        db.add(meeting)
        await db.flush()
        await _add_final_utterance(db, meeting.id, 1, "historical content", "과거 내용")
        await db.commit()
    # A legacy on-disk summary should also be indexed by the backfill.
    sdir = tmp_path / "storage" / str(ext)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "summary.md").write_text("# 요약 — Legacy\n\nlegacy summary body\n", encoding="utf-8")

    # First "upgraded launch": create_schema must create the table AND backfill.
    await seed_mod.create_schema()
    async with factory() as db:
        n_utt = (await db.execute(
            text("SELECT count(*) FROM session_search_fts WHERE kind='utterance'")
        )).scalar()
        n_sum = (await db.execute(
            text("SELECT count(*) FROM session_search_fts WHERE kind='summary'")
        )).scalar()
    assert n_utt == 1
    assert n_sum == 1

    # Warm re-launch: table already populated → no duplicate rows.
    await seed_mod.create_schema()
    async with factory() as db:
        total = (await db.execute(
            text("SELECT count(*) FROM session_search_fts")
        )).scalar()
    assert total == 2
    await engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# S1b — meeting-end index hook (SQLite; binds the sessions module AsyncSessionLocal)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_index_hook_indexes_is_final_only_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import apps.server.api.v1.sessions as sessions_mod

    engine = await _sqlite_engine(tmp_path)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(sessions_mod, "AsyncSessionLocal", factory, raising=True)

    ext = uuid4()
    now = datetime.now(timezone.utc)
    async with factory() as db:
        user = AppUser(email="h@h", name="H", password_hash="x", role="operator")
        db.add(user)
        await db.flush()
        meeting = Session(
            external_id=ext, owner_user_id=user.id, title="Hook", status="ended",
            started_at=now, ended_at=now,
        )
        db.add(meeting)
        await db.flush()
        await _add_final_utterance(db, meeting.id, 1, "alpha bravo", "알파 브라보")
        # a non-final partial must NOT be indexed
        db.add(Utterance(
            session_id=meeting.id, seq=2, speaker=None, text_en="partial charlie",
            text_ko="부분", started_at=now, ended_at=now, is_final=False,
        ))
        await db.commit()
        meeting_pk = meeting.id

    # Run the hook (resolves by external_id via its own session).
    await sessions_mod._index_session_search_fts(ext, "delta echo summary")

    async with factory() as db:
        n_utt = (await db.execute(
            text("SELECT count(*) FROM session_search_fts WHERE kind='utterance' AND session_id=:s"),
            {"s": str(meeting_pk)},
        )).scalar()
        n_sum = (await db.execute(
            text("SELECT count(*) FROM session_search_fts WHERE kind='summary' AND session_id=:s"),
            {"s": str(meeting_pk)},
        )).scalar()
    assert n_utt == 1  # only the is_final utterance
    assert n_sum == 1

    # Idempotent: re-running does not duplicate rows.
    await sessions_mod._index_session_search_fts(ext, "delta echo summary")
    async with factory() as db:
        total = (await db.execute(
            text("SELECT count(*) FROM session_search_fts WHERE session_id=:s"),
            {"s": str(meeting_pk)},
        )).scalar()
    assert total == 2
    await engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# S2 — endpoint (Postgres conftest fixture → exercises the LIKE fallback path)
# ─────────────────────────────────────────────────────────────────────────────
async def _mk_session(
    db: AsyncSession,
    owner_id: int,
    *,
    title: str,
    status: str = "ended",
    started: datetime | None = None,
) -> Session:
    started = started or datetime.now(timezone.utc)
    m = Session(
        external_id=uuid4(), owner_user_id=owner_id, title=title, status=status,
        started_at=started, ended_at=(started if status == "ended" else None),
    )
    db.add(m)
    await db.flush()
    return m


async def _add_final_utterance(
    db: AsyncSession, session_pk: int, seq: int, text_en: str, text_ko: str
) -> None:
    now = datetime.now(timezone.utc)
    db.add(Utterance(
        session_id=session_pk, seq=seq, speaker=None,
        text_en=text_en, text_ko=text_ko, started_at=now, ended_at=now,
        is_final=True,
    ))


@pytest.mark.asyncio
async def test_list_requires_operator_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/sessions")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_orders_started_at_desc_and_report_ready(
    client: AsyncClient, admin_user: AppUser, admin_token: str, db_session: AsyncSession
) -> None:
    base = datetime.now(timezone.utc)
    await _mk_session(db_session, admin_user.id, title="old", started=base - timedelta(hours=2))
    await _mk_session(db_session, admin_user.id, title="new", started=base)
    await db_session.commit()

    resp = await client.get(
        "/api/v1/sessions", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    titles = [i["title"] for i in body["items"]]
    assert titles == ["new", "old"]  # started_at desc
    assert all(i["report_ready"] is True for i in body["items"])  # status == ended
    assert all(i["snippets"] == [] for i in body["items"])  # no q → empty snippets
    assert body["has_more"] is False


@pytest.mark.asyncio
async def test_list_pagination_has_more(
    client: AsyncClient, admin_user: AppUser, admin_token: str, db_session: AsyncSession
) -> None:
    base = datetime.now(timezone.utc)
    for i in range(3):
        await _mk_session(db_session, admin_user.id, title=f"s{i}", started=base - timedelta(minutes=i))
    await db_session.commit()

    resp = await client.get(
        "/api/v1/sessions?limit=2&offset=0",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["has_more"] is True

    resp2 = await client.get(
        "/api/v1/sessions?limit=2&offset=2",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    body2 = resp2.json()
    assert len(body2["items"]) == 1
    assert body2["has_more"] is False


@pytest.mark.asyncio
async def test_list_status_filter_excludes_live_by_default(
    client: AsyncClient, admin_user: AppUser, admin_token: str, db_session: AsyncSession
) -> None:
    await _mk_session(db_session, admin_user.id, title="ended-one", status="ended")
    await _mk_session(db_session, admin_user.id, title="live-one", status="live")
    await db_session.commit()

    # default status=ended → only the ended session
    resp = await client.get(
        "/api/v1/sessions", headers={"Authorization": f"Bearer {admin_token}"}
    )
    titles = {i["title"] for i in resp.json()["items"]}
    assert titles == {"ended-one"}

    # status=all → both
    resp_all = await client.get(
        "/api/v1/sessions?status=all", headers={"Authorization": f"Bearer {admin_token}"}
    )
    titles_all = {i["title"] for i in resp_all.json()["items"]}
    assert titles_all == {"ended-one", "live-one"}


@pytest.mark.asyncio
async def test_list_scope_mine(
    client: AsyncClient, admin_user: AppUser, admin_token: str, db_session: AsyncSession
) -> None:
    # A second owner whose session must be hidden under scope=mine.
    other = AppUser(email="other@x", name="O", password_hash="x", role="operator")
    db_session.add(other)
    await db_session.flush()
    await _mk_session(db_session, admin_user.id, title="mine")
    await _mk_session(db_session, other.id, title="theirs")
    await db_session.commit()

    resp = await client.get(
        "/api/v1/sessions?scope=mine",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    titles = {i["title"] for i in resp.json()["items"]}
    assert titles == {"mine"}

    resp_all = await client.get(
        "/api/v1/sessions?scope=all",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    titles_all = {i["title"] for i in resp_all.json()["items"]}
    assert titles_all == {"mine", "theirs"}


@pytest.mark.asyncio
async def test_search_like_fallback_shape_and_match(
    client: AsyncClient, admin_user: AppUser, admin_token: str, db_session: AsyncSession,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Postgres (no FTS5) the search path is the LIKE fallback; it must match
    transcript content AND return the identical SessionListItem/snippets shape."""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    m_hit = await _mk_session(db_session, admin_user.id, title="hit")
    m_miss = await _mk_session(db_session, admin_user.id, title="miss")
    await _add_final_utterance(
        db_session, m_hit.id, 1, "Send the budget report by Friday", "금요일까지 예산 보고서"
    )
    await _add_final_utterance(
        db_session, m_miss.id, 1, "totally unrelated text", "관계없는 내용"
    )
    await db_session.commit()

    resp = await client.get(
        "/api/v1/sessions?q=budget",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    titles = [i["title"] for i in body["items"]]
    assert titles == ["hit"]  # only the matching session
    item = body["items"][0]
    # identical shape: snippets present + windowed around the hit
    assert isinstance(item["snippets"], list) and item["snippets"]
    assert "budget" in item["snippets"][0].lower()
    assert item["report_ready"] is True
    assert item["utterance_count"] == 1
    assert set(item.keys()) >= {
        "external_id", "title", "client_label", "status", "started_at",
        "ended_at", "owner_user_id", "visibility", "utterance_count",
        "report_ready", "snippets",
    }


@pytest.mark.asyncio
async def test_search_like_fallback_matches_summary(
    client: AsyncClient, admin_user: AppUser, admin_token: str, db_session: AsyncSession,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    m = await _mk_session(db_session, admin_user.id, title="summ")
    await _add_final_utterance(db_session, m.id, 1, "hello world", "안녕")
    await db_session.commit()
    # Write the on-disk summary the LIKE fallback reads.
    sdir = tmp_path / str(m.external_id)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "summary.md").write_text(
        "# 요약 — summ\n\nThe quarterly milestone was achieved.\n", encoding="utf-8"
    )

    resp = await client.get(
        "/api/v1/sessions?q=milestone",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    body = resp.json()
    titles = [i["title"] for i in body["items"]]
    assert titles == ["summ"]
    assert any("milestone" in s.lower() for s in body["items"][0]["snippets"])


@pytest.mark.asyncio
async def test_ended_zero_utterance_lists_and_zero_search_hits(
    client: AsyncClient, admin_user: AppUser, admin_token: str, db_session: AsyncSession,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ended session with zero is_final utterances lists fine (no 409) and
    yields zero search hits — no special guard required."""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    await _mk_session(db_session, admin_user.id, title="empty")
    await db_session.commit()

    # lists
    resp = await client.get(
        "/api/v1/sessions", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["title"] == "empty"
    assert item["utterance_count"] == 0
    assert item["report_ready"] is True

    # search → zero hits
    resp_q = await client.get(
        "/api/v1/sessions?q=anything",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp_q.status_code == 200
    assert resp_q.json()["items"] == []
    assert resp_q.json()["has_more"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Review fixes: FTS5 punctuated-query safety + LIKE wildcard escaping
# ─────────────────────────────────────────────────────────────────────────────
def test_build_fts_match_query_neutralizes_syntax() -> None:
    from apps.server.api.v1.sessions import _build_fts_match_query

    # Hyphen/colon/AND become quoted literal tokens — no FTS5 operators leak.
    assert _build_fts_match_query("action-item") == '"action-item"'
    assert _build_fts_match_query("Q3:") == '"Q3:"'
    assert _build_fts_match_query("foo AND bar") == '"foo" "AND" "bar"'
    # Embedded double-quote is doubled.
    assert _build_fts_match_query('a"b') == '"a""b"'
    # Empty / whitespace-only → empty string (caller returns no results).
    assert _build_fts_match_query("   ") == ""


@pytest.mark.asyncio
async def test_fts_search_punctuated_query_does_not_crash(tmp_path: Path) -> None:
    """A hyphenated AND a colon query must return results (not raise) on FTS5."""
    engine = await _sqlite_engine(tmp_path)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    from apps.server.api.v1.sessions import _fts_search_session_pks

    async with factory() as db:
        await reindex_session_fts(
            db, 99,
            [("Q3 예산", "the Q3 action-item budget review")],
            None,
        )
        await db.commit()
        # These raw inputs would be FTS5 syntax errors if passed unescaped.
        res_hyphen = await _fts_search_session_pks(db, "action-item")
        assert res_hyphen and res_hyphen[0][0] == 99
        res_colon = await _fts_search_session_pks(db, "Q3:")
        assert res_colon and res_colon[0][0] == 99
        # A query that is pure punctuation degrades to no hits, never a crash.
        assert await _fts_search_session_pks(db, "---") == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_search_endpoint_punctuated_query_returns_200(
    client: AsyncClient, admin_user: AppUser, admin_token: str, db_session: AsyncSession,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: hyphen/colon queries return HTTP 200 (LIKE path on Postgres),
    proving neither the FTS nor LIKE path 500s on ordinary punctuated phrases."""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    m = await _mk_session(db_session, admin_user.id, title="punct")
    await _add_final_utterance(db_session, m.id, 1, "the action-item for Q3: ship it", "출시")
    await db_session.commit()

    for q in ("action-item", "Q3:", "R&D"):
        resp = await client.get(
            f"/api/v1/sessions?q={q}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200, f"q={q!r}: {resp.text}"
    # The hyphenated phrase actually matches.
    resp = await client.get(
        "/api/v1/sessions?q=action-item",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert [i["title"] for i in resp.json()["items"]] == ["punct"]


@pytest.mark.asyncio
async def test_like_fallback_wildcard_does_not_overmatch(
    client: AsyncClient, admin_user: AppUser, admin_token: str, db_session: AsyncSession,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A query containing `_` or `%` must be matched literally, not as a wildcard
    (an unescaped `_` would match any single char and over-match everything)."""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    m_lit = await _mk_session(db_session, admin_user.id, title="has-underscore")
    m_other = await _mk_session(db_session, admin_user.id, title="no-underscore")
    await _add_final_utterance(db_session, m_lit.id, 1, "build_pipeline failed", "빌드 실패")
    await _add_final_utterance(db_session, m_other.id, 1, "buildXpipeline ok", "정상")
    await db_session.commit()

    # `build_pipeline` must match ONLY the literal underscore row, not buildXpipeline.
    resp = await client.get(
        "/api/v1/sessions?q=build_pipeline",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert [i["title"] for i in resp.json()["items"]] == ["has-underscore"]

    # A bare `%` must not match-all.
    resp_pct = await client.get(
        "/api/v1/sessions?q=%25",  # url-encoded '%'
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp_pct.status_code == 200
    assert resp_pct.json()["items"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Review fix: SessionListItem must serialize UTC-aware timestamps
# ─────────────────────────────────────────────────────────────────────────────
def test_session_list_item_serializes_utc_aware_timestamps() -> None:
    """Naive-UTC stored datetimes must serialize WITH a tz suffix so the client
    doesn't read them as local time (the off-by-UTC-offset display bug)."""
    from apps.server.api.v1.sessions import SessionListItem

    naive = datetime(2026, 6, 24, 6, 52, 0)  # naive UTC, as stored
    item = SessionListItem(
        external_id="x", title="t", client_label=None, status="ended",
        started_at=naive, ended_at=naive, owner_user_id=1, visibility="org",
        utterance_count=0, report_ready=True,
    )
    dumped = item.model_dump(mode="json")
    # Both timestamps carry UTC tz info (Z or +00:00).
    for key in ("started_at", "ended_at"):
        s = dumped[key]
        assert s.endswith("Z") or s.endswith("+00:00"), f"{key} not tz-aware: {s!r}"
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)

    # ended_at=None passes through as null.
    item_no_end = SessionListItem(
        external_id="x", title="t", client_label=None, status="live",
        started_at=naive, ended_at=None, owner_user_id=1, visibility="org",
        utterance_count=0, report_ready=False,
    )
    assert item_no_end.model_dump(mode="json")["ended_at"] is None


@pytest.mark.asyncio
async def test_list_endpoint_timestamps_are_utc_aware(
    client: AsyncClient, admin_user: AppUser, admin_token: str, db_session: AsyncSession
) -> None:
    """End-to-end: the list endpoint JSON carries tz-aware timestamps."""
    await _mk_session(db_session, admin_user.id, title="tz")
    await db_session.commit()
    resp = await client.get(
        "/api/v1/sessions", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200, resp.text
    started = resp.json()["items"][0]["started_at"]
    assert started.endswith("Z") or started.endswith("+00:00"), started
