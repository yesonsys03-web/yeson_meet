"""Auth router stub. Body implemented in S1-L1 (POST /auth/login)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.server.auth.jwt import TokenPair, create_access, create_refresh
from apps.server.auth.password import verify_password
from apps.server.db.models import AppUser
from apps.server.db.session import get_session

router = APIRouter(tags=["auth"], prefix="/auth")


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/login", response_model=TokenPair)
async def login(
    body: LoginIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> TokenPair:
    user = (
        await db.execute(
            select(AppUser).where(
                AppUser.email == body.email,
                AppUser.is_active == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid email or password"
        )
    sub = str(user.id)
    return TokenPair(access_token=create_access(sub), refresh_token=create_refresh(sub))
