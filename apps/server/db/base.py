# === ANCHOR: BASE_START ===
"""SQLAlchemy 2.0 declarative base."""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


# === ANCHOR: BASE_BASE_START ===
class Base(DeclarativeBase):
    """Shared declarative base for all yeson-meet ORM models."""
# === ANCHOR: BASE_BASE_END ===
# === ANCHOR: BASE_END ===
