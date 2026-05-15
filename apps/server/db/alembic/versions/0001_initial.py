"""Slice 1 initial schema: 5 tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="operator"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("email", name="uq_app_user_email"),
    )

    op.create_table(
        "device",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("api_key_hash", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("api_key_hash", name="uq_device_api_key_hash"),
    )

    op.create_table(
        "session",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True, nullable=False),
        sa.Column("external_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("device_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("client_label", sa.String(length=255), nullable=True),
        sa.Column("visibility", sa.String(length=16), nullable=False, server_default="org"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="live"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["app_user.id"], name="fk_session_owner_user"),
        sa.ForeignKeyConstraint(["device_id"], ["device.id"], name="fk_session_device"),
        sa.UniqueConstraint("external_id", name="uq_session_external_id"),
    )

    op.create_table(
        "session_token",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("pin", sa.CHAR(length=6), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["session.id"], ondelete="CASCADE", name="fk_session_token_session"
        ),
        sa.UniqueConstraint("token", name="uq_session_token_token"),
    )

    op.create_table(
        "utterance",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=255), nullable=True),
        sa.Column("text_en", sa.Text(), nullable=False),
        sa.Column("text_ko", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(
            ["session_id"], ["session.id"], ondelete="CASCADE", name="fk_utterance_session"
        ),
        sa.UniqueConstraint("session_id", "seq", name="uq_utterance_session_seq"),
    )
    op.create_index(
        "idx_utterance_session_started", "utterance", ["session_id", "started_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("idx_utterance_session_started", table_name="utterance")
    op.drop_table("utterance")
    op.drop_table("session_token")
    op.drop_table("session")
    op.drop_table("device")
    op.drop_table("app_user")
