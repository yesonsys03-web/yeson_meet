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
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

# Dialect-portable surrogate-key type. PostgreSQL keeps native ``BIGINT`` (zero
# regression); SQLite renders ``INTEGER`` so that a ``PRIMARY KEY`` column gets
# rowid autoincrement (SQLite only autoincrements ``INTEGER PRIMARY KEY``, never
# ``BIGINT``). FK columns use the same type so cross-dialect joins stay aligned.
_BigIntId = BigInteger().with_variant(Integer, "sqlite")


# === ANCHOR: MODELS_APPUSER_START ===
class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(_BigIntId, primary_key=True, autoincrement=True)
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

    id: Mapped[int] = mapped_column(_BigIntId, primary_key=True, autoincrement=True)
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

    id: Mapped[int] = mapped_column(_BigIntId, primary_key=True, autoincrement=True)
    external_id: Mapped[PyUUID] = mapped_column(
        # Dialect-portable UUID (PR1.1): SQLAlchemy 2.0 Uuid(as_uuid=True)
        # compiles to native ``uuid`` on PostgreSQL (zero regression vs the
        # prior postgresql.UUID) and to ``CHAR(32)`` on SQLite, round-tripping
        # ``uuid.UUID`` on both backends so a cold SQLite file boots cleanly.
        Uuid(as_uuid=True), unique=True, nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(
        _BigIntId, ForeignKey("app_user.id"), nullable=False
    )
    device_id: Mapped[int | None] = mapped_column(
        _BigIntId, ForeignKey("device.id"), nullable=True
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

    id: Mapped[int] = mapped_column(_BigIntId, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        _BigIntId,
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

    id: Mapped[int] = mapped_column(_BigIntId, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        _BigIntId,
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


# === ANCHOR: MODELS_VIDEO_JOB_START ===
class VideoJob(Base):
    __tablename__ = "video_job"

    id: Mapped[int] = mapped_column(_BigIntId, primary_key=True, autoincrement=True)
    external_id: Mapped[PyUUID] = mapped_column(
        Uuid(as_uuid=True), unique=True, nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(
        _BigIntId, ForeignKey("app_user.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # "youtube" | "upload"
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # youtube URL or original upload filename
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    whisper_model: Mapped[str] = mapped_column(String(32), nullable=False)
    translate_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    translate_cli_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # queued|ingesting|extracting|transcribing|translating|review|burning|done|error
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="queued", default="queued"
    )
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    burned_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 미디어 길이(ms). 전사 후 audio.wav를 삭제하므로 굽기 진행률 분모를
    # wav 대신 여기서 읽는다 (pipeline.run_burn_job).
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )
# === ANCHOR: MODELS_VIDEO_JOB_END ===


# === ANCHOR: MODELS_VIDEO_SEGMENT_START ===
class VideoSegment(Base):
    __tablename__ = "video_segment"

    id: Mapped[int] = mapped_column(_BigIntId, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        _BigIntId, ForeignKey("video_job.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text_en: Mapped[str] = mapped_column(Text, nullable=False)
    text_ko: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("job_id", "seq", name="uq_video_segment_job_seq"),
        Index("idx_video_segment_job", "job_id"),
    )
# === ANCHOR: MODELS_VIDEO_SEGMENT_END ===
# === ANCHOR: MODELS_END ===
