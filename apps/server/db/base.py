"""SQLAlchemy 2.0 declarative base."""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all yeson-meet ORM models."""
