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


def upgrade() -> None:
    op.add_column(
        "session",
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session", "disconnected_at")
