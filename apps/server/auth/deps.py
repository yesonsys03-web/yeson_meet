# === ANCHOR: DEPS_START ===
"""FastAPI auth dependencies."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.device import hash_api_key
from apps.server.auth.jwt import decode_token
from apps.server.db.models import AppUser, Device
from apps.server.db.session import get_session


# === ANCHOR: DEPS_GET_CURRENT_USER_START ===
async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: Annotated[AsyncSession, Depends(get_session)] = ...,
# === ANCHOR: DEPS_GET_CURRENT_USER_END ===
) -> AppUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_token(token)
        if payload.get("kind") != "access":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token kind")
        user_id = int(payload["sub"])
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    user = (
        await db.execute(
            select(AppUser).where(AppUser.id == user_id, AppUser.is_active == True)  # noqa: E712
        )
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


# === ANCHOR: DEPS_REQUIRE_ADMIN_START ===
async def require_admin(
    user: Annotated[AppUser, Depends(get_current_user)],
# === ANCHOR: DEPS_REQUIRE_ADMIN_END ===
) -> AppUser:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user


# === ANCHOR: DEPS_REQUIRE_OPERATOR_START ===
async def require_operator(
    user: Annotated[AppUser, Depends(get_current_user)],
# === ANCHOR: DEPS_REQUIRE_OPERATOR_END ===
) -> AppUser:
    if user.role not in ("admin", "operator"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Operator only")
    return user


# === ANCHOR: DEPS_DEVICE_FROM_KEY_START ===
async def device_from_key(
    key: Annotated[str, Query()],
    db: Annotated[AsyncSession, Depends(get_session)] = ...,
# === ANCHOR: DEPS_DEVICE_FROM_KEY_END ===
) -> Device:
    """Resolve a Device from a plaintext API key (hash-first single lookup)."""
    hashed = hash_api_key(key)
    device = (
        await db.execute(
            select(Device).where(
                Device.api_key_hash == hashed,
                Device.is_active == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid device key")
    return device
# === ANCHOR: DEPS_END ===
