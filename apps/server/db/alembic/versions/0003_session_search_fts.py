# === ANCHOR: 0003_SESSION_SEARCH_FTS_START ===
"""add standalone FTS5 session_search_fts index for the knowledge repository

Revision ID: 0003_session_search_fts
Revises: 0002_session_disconnected_at

SQLite-only: the FTS5 virtual table is a SQLite construct. The whole body is
dialect-guarded so ``alembic upgrade`` is a clean no-op on PostgreSQL (a live
target with its own full-text search). Within the SQLite branch the FTS5 module
is probed and the migration no-ops gracefully (logged warning) when absent, so a
bundled SQLite without FTS5 never bricks startup.

Backfill: existing ``is_final`` utterances become ``kind='utterance'`` rows;
legacy summaries are indexed where a ``summary.md`` already exists on disk (read
via STORAGE_ROOT). Going forward the meeting-end hook (S1b) keeps both current.
No triggers (plan Option A').
"""
from __future__ import annotations

import logging

from alembic import op

from apps.server.db.search import (
    backfill_session_search_fts,
    drop_session_search_fts,
    ensure_session_search_fts,
)

revision = "0003_session_search_fts"
down_revision = "0002_session_disconnected_at"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

# Backfill logic lives in apps.server.db.search (the canonical implementation,
# shared with the cold-start create_schema path) so the dashed-UUID summary-dir
# normalization is maintained in exactly one place.
_backfill = backfill_session_search_fts


# === ANCHOR: 0003_SESSION_SEARCH_FTS_UPGRADE_START ===
def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        # PostgreSQL (and any non-SQLite): FTS5 is SQLite-only; no-op.
        return
    if not ensure_session_search_fts(bind):
        logger.warning(
            "0003_session_search_fts: FTS5 unavailable on this SQLite engine; "
            "search index not created (endpoint falls back to LIKE)."
        )
        return
    backfill_session_search_fts(bind)
# === ANCHOR: 0003_SESSION_SEARCH_FTS_UPGRADE_END ===


# === ANCHOR: 0003_SESSION_SEARCH_FTS_DOWNGRADE_START ===
def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    drop_session_search_fts(bind)
# === ANCHOR: 0003_SESSION_SEARCH_FTS_DOWNGRADE_END ===
# === ANCHOR: 0003_SESSION_SEARCH_FTS_END ===
