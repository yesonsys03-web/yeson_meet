"""video captions: video_job + video_segment

Revision ID: 0004_video_captions
Revises: 0003_session_search_fts
Create Date: 2026-07-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_video_captions"
down_revision = "0003_session_search_fts"
branch_labels = None
depends_on = None

_BigIntId = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    op.create_table(
        "video_job",
        sa.Column("id", _BigIntId, primary_key=True, autoincrement=True),
        sa.Column("external_id", sa.Uuid(as_uuid=True), unique=True, nullable=False),
        sa.Column("owner_user_id", _BigIntId, sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("whisper_model", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("media_path", sa.Text(), nullable=True),
        sa.Column("preview_path", sa.Text(), nullable=True),
        sa.Column("audio_path", sa.Text(), nullable=True),
        sa.Column("burned_path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_table(
        "video_segment",
        sa.Column("id", _BigIntId, primary_key=True, autoincrement=True),
        sa.Column("job_id", _BigIntId,
                  sa.ForeignKey("video_job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("text_en", sa.Text(), nullable=False),
        sa.Column("text_ko", sa.Text(), nullable=False),
        sa.UniqueConstraint("job_id", "seq", name="uq_video_segment_job_seq"),
    )
    op.create_index("idx_video_segment_job", "video_segment", ["job_id"])


def downgrade() -> None:
    op.drop_table("video_segment")
    op.drop_table("video_job")
