"""pdf translate: pdf_job

Revision ID: 0007_pdf_jobs
Revises: 0006_video_duration_ms
Create Date: 2026-07-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_pdf_jobs"
down_revision = "0006_video_duration_ms"
branch_labels = None
depends_on = None

_BigIntId = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    op.create_table(
        "pdf_job",
        sa.Column("id", _BigIntId, primary_key=True, autoincrement=True),
        sa.Column("external_id", sa.Uuid(as_uuid=True), unique=True, nullable=False),
        sa.Column("owner_user_id", _BigIntId, sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("format", sa.String(32), nullable=True),
        sa.Column("translate_provider", sa.String(32), nullable=True),
        sa.Column("translate_cli_model", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("translated_path", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("block_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("pdf_job")
