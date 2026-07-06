"""video captions: per-job translate provider override

Revision ID: 0005_video_translate_provider
Revises: 0004_video_captions
Create Date: 2026-07-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_video_translate_provider"
down_revision = "0004_video_captions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_job", sa.Column("translate_provider", sa.String(32), nullable=True))
    op.add_column("video_job", sa.Column("translate_cli_model", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("video_job", "translate_cli_model")
    op.drop_column("video_job", "translate_provider")
