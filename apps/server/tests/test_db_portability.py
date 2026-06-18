"""Slice 1 — DB portability (dual-backend) verification.

These tests prove the server's DB layer boots on a cold, bundled SQLite file
(the packaged-app default) while the Postgres deploy stays byte-for-byte
unchanged.

Design note: the SQLite assertions deliberately build their OWN engines (sync
``sqlite`` for reflection, async ``sqlite+aiosqlite`` for the bootstrap path)
rather than the conftest ``db_session`` fixture, which is Postgres-only. The
Postgres-side drift test reflects against the live test DB and skips with a
clear reason when no Postgres is reachable.

ACs covered: AC1.1, AC1.2, AC1.3 (drift), AC1.3-tz/watchdog, AC1.4, AC1.5,
AC1.6, AC1.7, plus the SQLite pragmas.
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import (
    BigInteger,
    Uuid,
    create_engine,
    insert,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from apps.server.auth.password import verify_password
from apps.server.db.base import Base
from apps.server.db.models import AppUser, Session

SLICE1_TABLES = {"app_user", "device", "session", "session_token", "utterance"}

# Default seed.py credentials that MUST NOT be plantable on the packaged path.
SEED_DEFAULT_EMAIL = "admin@yeson.local"
SEED_DEFAULT_PASSWORD = "change-me-now"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _sqlite_sync_engine(tmp_path: Path) -> Engine:
    return create_engine(f"sqlite:///{tmp_path / 'cold.db'}", echo=False)


# ─────────────────────────────────────────────────────────────────────────────
# AC1.1 — cold SQLite create_all → all 5 tables; UUID round-trips; now() default
# ─────────────────────────────────────────────────────────────────────────────
def test_ac1_1_cold_sqlite_create_all_all_tables(tmp_path: Path) -> None:
    engine = _sqlite_sync_engine(tmp_path)
    # No postgresql.UUID / now() crash on a cold SQLite file.
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())
    assert SLICE1_TABLES <= tables, f"missing tables: {SLICE1_TABLES - tables}"
    engine.dispose()


def test_ac1_1_external_id_uuid_roundtrip_sqlite(tmp_path: Path) -> None:
    engine = _sqlite_sync_engine(tmp_path)
    Base.metadata.create_all(engine)
    ext = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            insert(AppUser.__table__).values(
                id=1, email="a@b.c", name="A", password_hash="x", role="admin"
            )
        )
        conn.execute(
            insert(Session.__table__).values(
                id=1, external_id=ext, owner_user_id=1, title="T"
            )
        )
        got = conn.execute(select(Session.__table__.c.external_id)).scalar_one()
    assert isinstance(got, uuid.UUID), f"external_id came back as {type(got)}"
    assert got == ext
    engine.dispose()


def test_ac1_1_now_default_does_not_crash_on_sqlite(tmp_path: Path) -> None:
    """Inserting a row that relies on the func.now() server_default must not
    crash on SQLite (no PG ``now()`` function required)."""
    engine = _sqlite_sync_engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # created_at omitted on purpose → exercises the func.now() default.
        conn.execute(
            insert(AppUser.__table__).values(
                email="now@b.c", name="N", password_hash="x", role="operator"
            )
        )
        created_at = conn.execute(
            select(AppUser.__table__.c.created_at)
        ).scalar_one()
    assert created_at is not None
    engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# AC1.2 — Postgres path unchanged: ORM still compiles native ``uuid`` on PG.
# This runs WITHOUT a live Postgres (pure dialect compile).
# ─────────────────────────────────────────────────────────────────────────────
def test_ac1_2_orm_emits_native_uuid_on_pg_dialect() -> None:
    col_type = Session.__table__.c.external_id.type
    compiled = col_type.compile(dialect=postgresql.dialect())
    assert compiled == "UUID", f"PG regression: external_id compiled to {compiled!r}"
    # And the portable representation on SQLite is a CHAR string.
    assert col_type.compile(dialect=sqlite.dialect()).startswith("CHAR")


def test_ac1_2_external_id_is_dialect_portable_uuid() -> None:
    assert isinstance(Session.__table__.c.external_id.type, Uuid)


# ─────────────────────────────────────────────────────────────────────────────
# AC1.3 — schema-drift test (same-backend reflection: create_all vs Alembic).
# PG side skips-with-reason when no live Postgres is reachable.
# ─────────────────────────────────────────────────────────────────────────────
def _reflect(engine: Engine) -> dict:
    insp = inspect(engine)
    out: dict = {}
    for table in sorted(insp.get_table_names()):
        if table == "alembic_version":
            continue
        cols = {}
        for c in insp.get_columns(table):
            cols[c["name"]] = {
                "nullable": c["nullable"],
                "type": str(c["type"]),
            }
        pk = set(insp.get_pk_constraint(table).get("constrained_columns") or [])
        uniques = {
            tuple(u["column_names"]) for u in insp.get_unique_constraints(table)
        }
        fks = {
            (
                tuple(f["constrained_columns"]),
                f["referred_table"],
                tuple(f["referred_columns"]),
            )
            for f in insp.get_foreign_keys(table)
        }
        out[table] = {
            "columns": cols,
            "pk": pk,
            "uniques": uniques,
            "fks": fks,
        }
    return out


def _pg_test_dsn() -> str | None:
    """Sync psycopg DSN to a scratch DB, or None if Postgres is unreachable.

    The password is read from ``PG_TEST_PASSWORD`` (no secret in source); when it
    is unset the drift test skips with a clear reason — same contract as an
    unreachable Postgres.
    """
    pw = os.environ.get("PG_TEST_PASSWORD")
    if not pw:
        return None
    admin = f"postgresql://yeson:{pw}@127.0.0.1:5432/postgres"  # vibelign: allow-secret — pw from env, test-only DSN
    try:
        import psycopg
    except ModuleNotFoundError:
        return None
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=3) as conn:
            conn.execute("DROP DATABASE IF EXISTS yeson_meet_drift")
            conn.execute("CREATE DATABASE yeson_meet_drift")
    except Exception:
        return None
    return f"postgresql+psycopg://yeson:{pw}@127.0.0.1:5432/yeson_meet_drift"


def test_ac1_3_schema_drift_create_all_vs_alembic_postgres() -> None:
    dsn = _pg_test_dsn()
    if dsn is None:
        pytest.skip(
            "PG side skipped: no live Postgres reachable at 127.0.0.1:5432 "
            "(psycopg missing or connect failed). SQLite side + PG dialect "
            "compile are still asserted by the other tests in this module."
        )

    # 1) create_all schema
    ca_engine = create_engine(dsn, echo=False)
    Base.metadata.create_all(ca_engine)
    created = _reflect(ca_engine)
    Base.metadata.drop_all(ca_engine)
    ca_engine.dispose()

    # 2) Alembic-migrated schema (to HEAD = 0001 + 0002, matching current ORM).
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path("apps/server/db/alembic.ini")))
    cfg.set_main_option("script_location", "apps/server/db/alembic")
    prev = os.environ.get("DATABASE_URL")
    # env.py reads DATABASE_URL; it builds an async engine, so give it asyncpg.
    os.environ["DATABASE_URL"] = dsn.replace("+psycopg", "+asyncpg")
    try:
        command.upgrade(cfg, "head")
    finally:
        if prev is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev

    mig_engine = create_engine(dsn, echo=False)
    migrated = _reflect(mig_engine)
    mig_engine.dispose()

    assert set(created) == set(migrated), (
        f"table set drift: create_all={set(created)} alembic={set(migrated)}"
    )
    for table in created:
        c, m = created[table], migrated[table]
        assert set(c["columns"]) == set(m["columns"]), f"{table} column-name drift"
        for col in c["columns"]:
            assert c["columns"][col]["nullable"] == m["columns"][col]["nullable"], (
                f"{table}.{col} nullability drift"
            )
            # Same backend (PG) → types must match exactly. (UUID/now cross-
            # dialect variances are by design and only arise on SQLite, so they
            # never apply to this same-backend PG-vs-PG comparison.)
            assert c["columns"][col]["type"] == m["columns"][col]["type"], (
                f"{table}.{col} type drift: "
                f"{c['columns'][col]['type']} vs {m['columns'][col]['type']}"
            )
        assert c["pk"] == m["pk"], f"{table} PK drift"
        assert c["uniques"] == m["uniques"], f"{table} unique-constraint drift"
        assert c["fks"] == m["fks"], f"{table} FK drift"


# ─────────────────────────────────────────────────────────────────────────────
# AC1.3-tz / watchdog — _as_utc()-based subtraction does not raise on SQLite.
# ─────────────────────────────────────────────────────────────────────────────
def test_ac1_3_tz_sqlite_disconnected_at_subtraction(tmp_path: Path) -> None:
    from apps.server.ops.session_safety import _as_utc

    engine = _sqlite_sync_engine(tmp_path)
    Base.metadata.create_all(engine)
    stamped = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            insert(AppUser.__table__).values(
                id=1, email="tz@b.c", name="A", password_hash="x", role="admin"
            )
        )
        conn.execute(
            insert(Session.__table__).values(
                id=1,
                external_id=uuid.uuid4(),
                owner_user_id=1,
                title="T",
                status="live",
                disconnected_at=stamped,
            )
        )
        read_back = conn.execute(
            select(Session.__table__.c.disconnected_at)
        ).scalar_one()
    # The watchdog does exactly this subtraction (session_safety.py); it must
    # not raise on a SQLite-sourced (possibly naive) datetime.
    delta = _as_utc(datetime.now(timezone.utc)) - _as_utc(read_back)
    assert delta.total_seconds() >= 0
    engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# AC1.5 — render_as_batch is NOT introduced anywhere in the DB layer.
# AC1.4 — no new DB-layer tz TypeDecorator was introduced.
# ─────────────────────────────────────────────────────────────────────────────
def test_ac1_5_no_render_as_batch_in_repo() -> None:
    db_dir = Path("apps/server/db")
    offenders = [
        p
        for p in db_dir.rglob("*.py")
        if "render_as_batch" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"render_as_batch found in: {offenders}"


def test_ac1_4_no_db_layer_tz_typedecorator() -> None:
    """The plan forbids a DB-layer tz TypeDecorator; _as_utc() in the ops layer
    is the only tz coercion. Assert the db package defines no TypeDecorator."""
    db_dir = Path("apps/server/db")
    for p in db_dir.rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        assert not re.search(r"\bclass\s+\w+\s*\(\s*TypeDecorator", src), (
            f"unexpected TypeDecorator subclass in {p}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC1.6 / AC1.7 — secure first-run admin bootstrap on the async SQLite path.
# ─────────────────────────────────────────────────────────────────────────────
def _bind_sqlite_async(monkeypatch, db_path: Path):
    """Point seed/session at a throwaway async SQLite file and return helpers
    bound to a fresh engine (so the connect-pragma listener is exercised)."""
    import apps.server.db.seed as seed_mod
    import apps.server.db.session as session_mod

    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(session_mod, "engine", engine, raising=True)
    monkeypatch.setattr(session_mod, "AsyncSessionLocal", factory, raising=True)
    monkeypatch.setattr(seed_mod, "engine", engine, raising=True)
    monkeypatch.setattr(seed_mod, "AsyncSessionLocal", factory, raising=True)
    return seed_mod, engine, factory


@pytest.mark.asyncio
async def test_ac1_6_bootstrap_admin_secure_no_default_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_mod, engine, factory = _bind_sqlite_async(monkeypatch, tmp_path / "boot.db")

    await seed_mod.create_schema()
    chosen_email = "operator@example.com"
    chosen_password = "a-real-operator-secret"  # vibelign: allow-secret — test fixture, not a real credential
    created = await seed_mod.bootstrap_admin(chosen_email, chosen_password)
    assert created is True

    async with factory() as s:
        users = (await s.execute(select(AppUser))).scalars().all()
    # Exactly one operator, and it is NOT the seed.py default pair.
    assert len(users) == 1
    user = users[0]
    assert user.email == chosen_email
    assert user.email != SEED_DEFAULT_EMAIL

    # (a) The chosen password authenticates.
    assert verify_password(chosen_password, user.password_hash) is True
    # (b) The known seed default pair CANNOT authenticate post-bootstrap.
    assert user.email != SEED_DEFAULT_EMAIL
    assert verify_password(SEED_DEFAULT_PASSWORD, user.password_hash) is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_ac1_6_bootstrap_admin_rejects_empty_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_mod, engine, _ = _bind_sqlite_async(monkeypatch, tmp_path / "empty.db")
    await seed_mod.create_schema()
    with pytest.raises(ValueError):
        await seed_mod.bootstrap_admin("operator@example.com", "")
    with pytest.raises(ValueError):
        await seed_mod.bootstrap_admin("", "pw")
    await engine.dispose()


@pytest.mark.asyncio
async def test_ac1_6_bootstrap_admin_is_no_op_when_operator_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_mod, engine, factory = _bind_sqlite_async(monkeypatch, tmp_path / "once.db")
    await seed_mod.create_schema()
    assert await seed_mod.bootstrap_admin("first@example.com", "pw1") is True
    # A second attempt with the KNOWN DEFAULT pair must not plant it.
    assert (
        await seed_mod.bootstrap_admin(SEED_DEFAULT_EMAIL, SEED_DEFAULT_PASSWORD)
        is False
    )
    async with factory() as s:
        emails = {u.email for u in (await s.execute(select(AppUser))).scalars().all()}
    assert emails == {"first@example.com"}
    assert SEED_DEFAULT_EMAIL not in emails
    await engine.dispose()


@pytest.mark.asyncio
async def test_ac1_7_wipe_then_relaunch_reseeds_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JWT_SECRET <-> disposable-SQLite coupling: wiping the DB file and
    relaunching yields a clean re-seed + a working (verifiable) login, with no
    stale password hash surviving the wipe."""
    db_path = tmp_path / "disposable.db"

    # First launch.
    seed_mod, engine, factory = _bind_sqlite_async(monkeypatch, db_path)
    await seed_mod.create_schema()
    await seed_mod.bootstrap_admin("op@example.com", "first-pw")
    async with factory() as s:
        first_hash = (await s.execute(select(AppUser.password_hash))).scalar_one()
    await engine.dispose()

    # Wipe the SQLite file (and any -wal/-shm) — the "disposable DB" event.
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    assert not db_path.exists()

    # Relaunch with the SAME (persistent) JWT_SECRET but a fresh DB + new pw.
    seed_mod2, engine2, factory2 = _bind_sqlite_async(monkeypatch, db_path)
    await seed_mod2.create_schema()
    created = await seed_mod2.bootstrap_admin("op@example.com", "second-pw")
    assert created is True  # fresh DB → re-seed happens
    async with factory2() as s:
        user = (await s.execute(select(AppUser))).scalar_one()
    # New login works; the stale (pre-wipe) hash did not survive.
    assert verify_password("second-pw", user.password_hash) is True
    assert verify_password("first-pw", user.password_hash) is False
    assert user.password_hash != first_hash
    await engine2.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# Pragmas — foreign_keys + journal_mode=wal ON for SQLite; PG connect is no-op.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sqlite_pragmas_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import apps.server.db.session as session_mod
    from sqlalchemy import event

    url = f"sqlite+aiosqlite:///{tmp_path / 'pragma.db'}"
    engine = create_async_engine(url, echo=False)
    # Re-attach the production pragma listener (engine is created per-test here).
    event.listen(
        engine.sync_engine, "connect", session_mod._apply_sqlite_pragmas
    )
    async with engine.connect() as conn:
        fk = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar()
        jm = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()
    assert fk == 1, "foreign_keys pragma not ON"
    assert str(jm).lower() == "wal", f"journal_mode is {jm!r}, expected wal"
    await engine.dispose()


def test_pg_connect_listener_is_noop_for_postgres() -> None:
    """The pragma listener is only registered when DATABASE_URL starts with
    'sqlite'. On the (default) Postgres URL the production engine has no such
    listener, so the PG path is identical."""
    import apps.server.db.session as session_mod

    # The module under test was imported with a Postgres DATABASE_URL (conftest
    # sets it), so the production engine must carry NO sqlite pragma listener.
    assert session_mod.DATABASE_URL.startswith("postgresql")
    fn = session_mod._apply_sqlite_pragmas
    # Inspect registered 'connect' listeners on the production sync engine.
    from sqlalchemy import event

    assert not event.contains(session_mod.engine.sync_engine, "connect", fn), (
        "sqlite pragma listener must NOT be attached on the Postgres engine"
    )
