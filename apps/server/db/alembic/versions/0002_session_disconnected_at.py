# === ANCHOR: 0002_SESSION_DISCONNECTED_AT_START ===
"""add session.disconnected_at for sidecar disconnect zombie termination

Revision ID: 0002_session_disconnected_at
Revises: 0001_initial
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_session_disconnected_at"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


# === ANCHOR: 0002_SESSION_DISCONNECTED_AT_UPGRADE_START ===
def upgrade() -> None:
    op.add_column(
        "session",
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
    )
# === ANCHOR: 0002_SESSION_DISCONNECTED_AT_UPGRADE_END ===


# === ANCHOR: 0002_SESSION_DISCONNECTED_AT_DOWNGRADE_START ===
def downgrade() -> None:
    op.drop_column("session", "disconnected_at")
# === ANCHOR: 0002_SESSION_DISCONNECTED_AT_DOWNGRADE_END ===
# === ANCHOR: 0002_SESSION_DISCONNECTED_AT_END ===
