# === ANCHOR: AUTH_START ===
"""Auth router stub. Body implemented in S1-L1 (POST /auth/login)."""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.jwt import TokenPair, create_access, create_refresh
from apps.server.auth.login_rate_limit import login_rate_limiter
from apps.server.auth.password import verify_password
from apps.server.db.models import AppUser
from apps.server.db.session import get_session

router = APIRouter(tags=["auth"], prefix="/auth")

_LOGIN_FAIL_DELAY_SECONDS = 0.3

# === ANCHOR: AUTH_LOGININ_START ===
class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
# === ANCHOR: AUTH_LOGININ_END ===


@router.post("/login", response_model=TokenPair)
# === ANCHOR: AUTH_LOGIN_START ===
async def login(
    body: LoginIn,
    db: Annotated[AsyncSession, Depends(get_session)],
# === ANCHOR: AUTH_LOGIN_END ===
) -> TokenPair:
    retry_after = login_rate_limiter.check(body.email)
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed login attempts",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    user = (
        await db.execute(
            select(AppUser).where(
                AppUser.email == body.email,
                AppUser.is_active == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        login_rate_limiter.record_failure(body.email)
        await asyncio.sleep(_LOGIN_FAIL_DELAY_SECONDS)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid email or password"
        )
    login_rate_limiter.record_success(body.email)
    sub = str(user.id)
    return TokenPair(access_token=create_access(sub), refresh_token=create_refresh(sub))
# === ANCHOR: AUTH_END ===
