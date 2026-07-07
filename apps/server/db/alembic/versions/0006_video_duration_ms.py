"""video captions: store media duration so audio.wav can be pruned early

Revision ID: 0006_video_duration_ms
Revises: 0005_video_translate_provider
Create Date: 2026-07-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_video_duration_ms"
down_revision = "0005_video_translate_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_job", sa.Column("duration_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("video_job", "duration_ms")
