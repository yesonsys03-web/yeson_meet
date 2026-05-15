"""JWT (HS256) create/decode. Locked: JWT_SECRET from env, access 24h, refresh 30d."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
from pydantic import BaseModel

ALGO = "HS256"
ACCESS_TTL = timedelta(hours=24)
REFRESH_TTL = timedelta(days=30)


def _secret() -> str:
    """Resolve JWT_SECRET lazily so import-time tools (alembic, tsc, static
    analyzers) don't crash when the env var isn't set."""
    value = os.environ.get("JWT_SECRET")
    if not value:
        raise RuntimeError("JWT_SECRET env var is required to mint/verify tokens")
    return value


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def create_token(sub: str, kind: str, ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "kind": kind,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return pyjwt.encode(payload, _secret(), algorithm=ALGO)


def create_access(sub: str) -> str:
    return create_token(sub, "access", ACCESS_TTL)


def create_refresh(sub: str) -> str:
    return create_token(sub, "refresh", REFRESH_TTL)


def decode_token(token: str) -> dict:
    return pyjwt.decode(token, _secret(), algorithms=[ALGO])
