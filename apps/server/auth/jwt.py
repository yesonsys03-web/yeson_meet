# === ANCHOR: JWT_START ===
"""JWT (HS256) create/decode. Locked: JWT_SECRET from env, access 24h, refresh 30d."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
from pydantic import BaseModel

ALGO = "HS256"
ACCESS_TTL = timedelta(hours=24)
REFRESH_TTL = timedelta(days=30)


# === ANCHOR: JWT__SECRET_START ===
def _secret() -> str:
    """Resolve JWT_SECRET lazily so import-time tools (alembic, tsc, static
    analyzers) don't crash when the env var isn't set."""
    value = os.environ.get("JWT_SECRET")
    if not value:
        raise RuntimeError("JWT_SECRET env var is required to mint/verify tokens")
    return value
# === ANCHOR: JWT__SECRET_END ===


# === ANCHOR: JWT_TOKENPAIR_START ===
class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
# === ANCHOR: JWT_TOKENPAIR_END ===


# === ANCHOR: JWT_CREATE_TOKEN_START ===
def create_token(sub: str, kind: str, ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "kind": kind,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return pyjwt.encode(payload, _secret(), algorithm=ALGO)
# === ANCHOR: JWT_CREATE_TOKEN_END ===


# === ANCHOR: JWT_CREATE_ACCESS_START ===
def create_access(sub: str) -> str:
    return create_token(sub, "access", ACCESS_TTL)
# === ANCHOR: JWT_CREATE_ACCESS_END ===


# === ANCHOR: JWT_CREATE_REFRESH_START ===
def create_refresh(sub: str) -> str:
    return create_token(sub, "refresh", REFRESH_TTL)
# === ANCHOR: JWT_CREATE_REFRESH_END ===


# === ANCHOR: JWT_DECODE_TOKEN_START ===
def decode_token(token: str) -> dict:
    return pyjwt.decode(token, _secret(), algorithms=[ALGO])
# === ANCHOR: JWT_DECODE_TOKEN_END ===
# === ANCHOR: JWT_END ===
