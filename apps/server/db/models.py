# === ANCHOR: MODELS_START ===
"""SQLAlchemy 2.0 ORM models for yeson-meet (Slice 1).

Locked in PRD §10: only 5 tables in Slice 1. department / role / glossary /
keyword / action_item / report are added in Slice 5+. DDL mirrors
docs/ARCHITECTURE.md §3.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


# === ANCHOR: MODELS_APPUSER_START ===
class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="operator", default="operator"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
# === ANCHOR: MODELS_APPUSER_END ===


# === ANCHOR: MODELS_DEVICE_START ===
class Device(Base):
    __tablename__ = "device"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
# === ANCHOR: MODELS_DEVICE_END ===


# === ANCHOR: MODELS_SESSION_START ===
class Session(Base):
    __tablename__ = "session"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    external_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_user.id"), nullable=False
    )
    device_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("device.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    client_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="org", default="org"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="live", default="live"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
# === ANCHOR: MODELS_SESSION_END ===


# === ANCHOR: MODELS_SESSIONTOKEN_START ===
class SessionToken(Base):
    __tablename__ = "session_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("session.id", ondelete="CASCADE"),
        nullable=False,
    )
    token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    pin: Mapped[str | None] = mapped_column(CHAR(6), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
# === ANCHOR: MODELS_SESSIONTOKEN_END ===


# === ANCHOR: MODELS_UTTERANCE_START ===
class Utterance(Base):
    __tablename__ = "utterance"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("session.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text_en: Mapped[str] = mapped_column(Text, nullable=False)
    text_ko: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    is_final: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )

    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_utterance_session_seq"),
        Index("idx_utterance_session_started", "session_id", "started_at"),
    )
# === ANCHOR: MODELS_UTTERANCE_END ===
# === ANCHOR: MODELS_END ===
