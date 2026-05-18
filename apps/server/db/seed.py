# === ANCHOR: SEED_START ===
"""Seed Slice 1 demo data (admin user, device, session, viewer token).

Run with::

    python -m apps.server.db.seed

Outputs (stdout) the env keys subsequent waves grep for:
ADMIN_EMAIL / ADMIN_PASSWORD / DEVICE_API_KEY / SESSION_EXTERNAL_ID /
VIEWER_TOKEN / VIEWER_URL.

Idempotent: re-running prints the same identifiers (existing rows updated
where it is safe; tokens/api keys preserved).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from apps.server.auth.password import hash_password
from apps.server.db.models import AppUser, Device, Session, SessionToken
from apps.server.db.session import AsyncSessionLocal

ADMIN_EMAIL = "admin@yeson.local"
ADMIN_NAME = "Admin"
ADMIN_ROLE = "admin"
DEVICE_NAME = "seed-device"
SESSION_TITLE = "S1 fixture demo"
VIEWER_URL_BASE = "http://localhost:5173/v/"
TOKEN_TTL_HOURS = 24


# === ANCHOR: SEED__SHA256_HEX_START ===
def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
# === ANCHOR: SEED__SHA256_HEX_END ===


# === ANCHOR: SEED__GEN_API_KEY_START ===
def _gen_api_key() -> str:
    """32-byte base64url API key (plaintext returned to caller)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
# === ANCHOR: SEED__GEN_API_KEY_END ===


# === ANCHOR: SEED__GEN_VIEWER_TOKEN_START ===
def _gen_viewer_token() -> str:
    """32-byte URL-safe base64 token."""
    return secrets.token_urlsafe(32)
# === ANCHOR: SEED__GEN_VIEWER_TOKEN_END ===


# === ANCHOR: SEED_SEED_START ===
async def seed() -> dict[str, str]:
    admin_password = os.environ.get("ADMIN_PASSWORD", "change-me-now")

    async with AsyncSessionLocal() as session:
        # --- admin user (idempotent on email) ---
        result = await session.execute(select(AppUser).where(AppUser.email == ADMIN_EMAIL))
        admin = result.scalar_one_or_none()
        if admin is None:
            admin = AppUser(
                email=ADMIN_EMAIL,
                name=ADMIN_NAME,
                password_hash=hash_password(admin_password),
                role=ADMIN_ROLE,
                is_active=True,
            )
            session.add(admin)
            await session.flush()

        # --- device (idempotent on name; if absent, mint new key) ---
        result = await session.execute(select(Device).where(Device.name == DEVICE_NAME))
        device = result.scalar_one_or_none()
        plaintext_api_key: str
        if device is None:
            plaintext_api_key = _gen_api_key()
            device = Device(
                name=DEVICE_NAME,
                api_key_hash=_sha256_hex(plaintext_api_key),
                is_active=True,
            )
            session.add(device)
            await session.flush()
        else:
            # Existing device: api key plaintext is not recoverable; emit sentinel.
            plaintext_api_key = "<existing-device-key-not-recoverable>"

        # --- session (idempotent on title for the same owner) ---
        result = await session.execute(
            select(Session).where(
                Session.title == SESSION_TITLE,
                Session.owner_user_id == admin.id,
            )
        )
        meeting = result.scalar_one_or_none()
        if meeting is None:
            meeting = Session(
                external_id=uuid4(),
                owner_user_id=admin.id,
                device_id=device.id,
                title=SESSION_TITLE,
                visibility="org",
                status="live",
            )
            session.add(meeting)
            await session.flush()

        # --- viewer token (idempotent on session+kind) ---
        result = await session.execute(
            select(SessionToken).where(
                SessionToken.session_id == meeting.id,
                SessionToken.kind == "viewer",
            )
        )
        token_row = result.scalar_one_or_none()
        if token_row is None:
            viewer_token = _gen_viewer_token()
            token_row = SessionToken(
                session_id=meeting.id,
                token=viewer_token,
                kind="viewer",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
            )
            session.add(token_row)
            await session.flush()
        else:
            viewer_token = token_row.token

        await session.commit()

        return {
            "ADMIN_EMAIL": ADMIN_EMAIL,
            "ADMIN_PASSWORD": admin_password,
            "DEVICE_API_KEY": plaintext_api_key,
            "SESSION_EXTERNAL_ID": str(meeting.external_id),
            "VIEWER_TOKEN": viewer_token,
            "VIEWER_URL": f"{VIEWER_URL_BASE}{viewer_token}",
        }
# === ANCHOR: SEED_SEED_END ===


# === ANCHOR: SEED__MAIN_START ===
async def _main() -> None:
    out = await seed()
    for key in (
        "ADMIN_EMAIL",
        "ADMIN_PASSWORD",
        "DEVICE_API_KEY",
        "SESSION_EXTERNAL_ID",
        "VIEWER_TOKEN",
        "VIEWER_URL",
    ):
        print(f"{key}={out[key]}")
# === ANCHOR: SEED__MAIN_END ===


if __name__ == "__main__":
    asyncio.run(_main())
# === ANCHOR: SEED_END ===
