# === ANCHOR: SEARCH_START ===
"""Meeting knowledge-repository full-text search (FTS5) support.

Standalone SQLite FTS5 index ``session_search_fts(session_id, kind, text)`` used
by the ``GET /api/v1/sessions`` list/search endpoint, the meeting-end index hook,
and the migration / cold-start schema creation.

Design (plan Option A'): the table is **standalone** (stores ``session_id`` so a
re-index/delete is ``WHERE session_id = ?`` and results GROUP BY it directly), has
**no triggers** (populated at meeting-end + a one-time backfill), and indexes only
``is_final`` utterances (``kind='utterance'``) plus the one LLM summary row
(``kind='summary'``).

FTS5 may be absent from the SQLite the engine is compiled against (or the backend
may be PostgreSQL). Every entry point therefore guards on :func:`fts5_available`
and the endpoint falls back to a LIKE scan that returns the identical response
shape.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

FTS_TABLE = "session_search_fts"

# Probe result cached per engine URL — the probe is cheap but runs on every
# request/index call, and the answer never changes for a given engine.
_fts5_cache: dict[str, bool] = {}


# === ANCHOR: SEARCH_FTS5_PROBE_START ===
def _probe_fts5(conn: Connection) -> bool:
    """Return True iff the bound SQLite engine has the FTS5 module compiled in.

    Non-SQLite dialects (PostgreSQL) always return False — the FTS5 table is a
    SQLite-only construct; the endpoint uses its LIKE fallback there. The probe
    creates and drops a throwaway in-connection temp virtual table; a missing
    module raises ``OperationalError`` which we swallow as "absent".
    """
    if conn.dialect.name != "sqlite":
        return False
    try:
        conn.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS temp._yeson_fts5_probe USING fts5(x)"
        )
        conn.exec_driver_sql("DROP TABLE IF EXISTS temp._yeson_fts5_probe")
        return True
    except Exception as exc:  # noqa: BLE001 — any failure means FTS5 unusable
        logger.warning("FTS5 unavailable on this SQLite engine: %s", exc)
        return False
# === ANCHOR: SEARCH_FTS5_PROBE_END ===


# === ANCHOR: SEARCH_FTS5_AVAILABLE_SYNC_START ===
def fts5_available_sync(conn: Connection) -> bool:
    """Cached :func:`_probe_fts5` keyed on the engine URL (sync connection)."""
    key = str(conn.engine.url)
    cached = _fts5_cache.get(key)
    if cached is None:
        cached = _probe_fts5(conn)
        _fts5_cache[key] = cached
    return cached
# === ANCHOR: SEARCH_FTS5_AVAILABLE_SYNC_END ===


# === ANCHOR: SEARCH_FTS5_AVAILABLE_START ===
async def fts5_available(session) -> bool:
    """Async wrapper over :func:`fts5_available_sync` for the request/session path.

    Accepts an ``AsyncSession`` and runs the (cached) sync probe on its
    connection. Used by the endpoint and the meeting-end index hook.
    """
    conn = await session.connection()
    return await conn.run_sync(fts5_available_sync)
# === ANCHOR: SEARCH_FTS5_AVAILABLE_END ===


# === ANCHOR: SEARCH_ENSURE_TABLE_START ===
def ensure_session_search_fts(conn: Connection) -> bool:
    """Create the standalone FTS5 search table on SQLite if FTS5 is available.

    Idempotent (``CREATE VIRTUAL TABLE IF NOT EXISTS``). No-ops and returns
    False on non-SQLite dialects or when the SQLite lacks FTS5 — callers
    (migration sqlite-branch, cold-start ``create_schema``) must never brick
    startup over a missing FTS5 module. Returns True iff the table now exists.
    """
    if not fts5_available_sync(conn):
        return False
    conn.exec_driver_sql(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
        "USING fts5(session_id UNINDEXED, kind UNINDEXED, text)"
    )
    return True
# === ANCHOR: SEARCH_ENSURE_TABLE_END ===


# === ANCHOR: SEARCH_DROP_TABLE_START ===
def drop_session_search_fts(conn: Connection) -> None:
    """Drop the FTS5 search table (migration downgrade)."""
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {FTS_TABLE}")
# === ANCHOR: SEARCH_DROP_TABLE_END ===


# === ANCHOR: SEARCH_STORAGE_ROOT_START ===
def _storage_root() -> str:
    import os

    return os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")
# === ANCHOR: SEARCH_STORAGE_ROOT_END ===


# === ANCHOR: SEARCH_LEGACY_SUMMARY_TEXT_START ===
def _legacy_summary_text(session_external_id: str) -> str | None:
    """Return the on-disk summary body for a session, or None if absent.

    Mirrors api/v1/sessions._read_summary_text_or_404 (header strip) without the
    HTTP coupling — the stored summary.md prepends a ``# 요약`` H1 we drop.
    """
    from pathlib import Path

    path = Path(_storage_root()) / session_external_id / "summary.md"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if lines and lines[0].startswith("# 요약"):
        lines = lines[1:]
        if lines and not lines[0].strip():
            lines = lines[1:]
    body = "\n".join(lines).strip()
    return body or None
# === ANCHOR: SEARCH_LEGACY_SUMMARY_TEXT_END ===


# === ANCHOR: SEARCH_BACKFILL_START ===
def backfill_session_search_fts(conn: Connection) -> None:
    """Backfill is_final utterances + legacy on-disk summaries into the FTS table.

    Canonical backfill used by BOTH the 0003 migration (Postgres deploys) and the
    cold-start ``create_schema`` (frozen bundle, which never runs alembic). Pure
    INSERTs — the caller decides when to run it (the migration on upgrade; the
    bundle only when the table is empty, see :func:`backfill_if_empty`).
    """
    import uuid as _uuid

    # One utterance document per is_final row (KO+EN combined).
    utt_rows = conn.exec_driver_sql(
        "SELECT session_id, text_ko, text_en FROM utterance WHERE is_final = 1"
    ).fetchall()
    for session_id, text_ko, text_en in utt_rows:
        body = "\n".join(p for p in (text_ko, text_en) if p)
        conn.exec_driver_sql(
            f"INSERT INTO {FTS_TABLE} (session_id, kind, text) "
            "VALUES (?, 'utterance', ?)",
            (str(session_id), body),
        )

    # Legacy summaries: index where a summary.md already exists on disk. SQLite's
    # CHAR(32) stores the UUID hex WITHOUT dashes, but report dirs are named with
    # the canonical dashed form (str(meeting.external_id)) — normalize first.
    sess_rows = conn.exec_driver_sql(
        "SELECT id, external_id FROM session WHERE status = 'ended'"
    ).fetchall()
    for sess_id, external_id in sess_rows:
        try:
            canonical = str(_uuid.UUID(str(external_id)))
        except (ValueError, AttributeError):
            canonical = str(external_id)
        summary = _legacy_summary_text(canonical)
        if summary:
            conn.exec_driver_sql(
                f"INSERT INTO {FTS_TABLE} (session_id, kind, text) "
                "VALUES (?, 'summary', ?)",
                (str(sess_id), summary),
            )
# === ANCHOR: SEARCH_BACKFILL_END ===


# === ANCHOR: SEARCH_BACKFILL_IF_EMPTY_START ===
def backfill_if_empty(conn: Connection) -> bool:
    """One-time idempotent backfill for in-place bundle upgrades.

    The frozen bundle builds its schema via ``create_schema`` (create_all), NOT
    alembic — so an EXISTING SQLite file upgraded in place gets an empty
    ``session_search_fts`` table and would leave every past meeting silently
    unsearchable (meetings never re-end, so the S1b hook never fires for them).

    This runs the canonical backfill ONLY when the table exists and is empty but
    ``is_final`` utterances are present. The empty-guard makes warm starts a
    single cheap COUNT with no writes. Returns True iff a backfill ran. No-ops
    when FTS5 is unavailable or the table is missing.
    """
    if not fts5_available_sync(conn):
        return False
    try:
        n_fts = conn.exec_driver_sql(f"SELECT count(*) FROM {FTS_TABLE}").scalar()
    except Exception:  # noqa: BLE001 — table absent (ensure step skipped/failed)
        return False
    if n_fts:
        return False  # already populated → warm start, no work
    n_final = conn.exec_driver_sql(
        "SELECT count(*) FROM utterance WHERE is_final = 1"
    ).scalar()
    if not n_final:
        return False  # nothing historical to index (fresh install)
    backfill_session_search_fts(conn)
    logger.info(
        "Backfilled session_search_fts on in-place upgrade (%s is_final rows)", n_final
    )
    return True
# === ANCHOR: SEARCH_BACKFILL_IF_EMPTY_END ===


# === ANCHOR: SEARCH_REINDEX_SESSION_START ===
async def reindex_session_fts(
    session,
    session_pk: int,
    utterances: list[tuple[str, str]],
    summary: str | None,
) -> None:
    """Re-index one session's rows in the FTS table (DELETE-then-insert).

    Idempotent: removes all existing rows for ``session_pk`` then inserts one
    ``kind='utterance'`` row per (text_ko, text_en) pair plus one
    ``kind='summary'`` row when a summary is present. Combines KO+EN text into a
    single document per utterance so a phrase in either language matches.

    No-ops when FTS5 is unavailable. The caller owns the commit.
    """
    if not await fts5_available(session):
        return
    await session.execute(
        text(f"DELETE FROM {FTS_TABLE} WHERE session_id = :sid"),
        {"sid": str(session_pk)},
    )
    for text_ko, text_en in utterances:
        body = "\n".join(p for p in (text_ko, text_en) if p)
        await session.execute(
            text(
                f"INSERT INTO {FTS_TABLE} (session_id, kind, text) "
                "VALUES (:sid, 'utterance', :body)"
            ),
            {"sid": str(session_pk), "body": body},
        )
    if summary:
        await session.execute(
            text(
                f"INSERT INTO {FTS_TABLE} (session_id, kind, text) "
                "VALUES (:sid, 'summary', :body)"
            ),
            {"sid": str(session_pk), "body": summary},
        )
# === ANCHOR: SEARCH_REINDEX_SESSION_END ===
# === ANCHOR: SEARCH_END ===
