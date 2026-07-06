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
from apps.server.db.base import Base
from apps.server.db.models import AppUser, Device, Session, SessionToken
from apps.server.db.search import backfill_if_empty, ensure_session_search_fts
from apps.server.db.session import AsyncSessionLocal, engine

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


# === ANCHOR: SEED_CREATE_SCHEMA_START ===
async def create_schema() -> None:
    """Create all ORM tables on the bound engine via ``create_all`` (idempotent).

    This is the packaged-app cold-start path: on a fresh SQLite file SQLAlchemy
    dialect-compiles ``func.now()`` and the portable ``Uuid`` type from the ORM,
    so no ``now()``/``postgresql.UUID`` crash occurs. Alembic remains the sole
    migration authority for the persistent Postgres deploy and is NOT wired here.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # The knowledge-repository FTS5 search table is a SQLite virtual table
        # that ``Base.metadata`` cannot model, so create it here on the cold
        # bundle path (mirrors the dialect-guarded 0003 migration used on
        # Postgres deploys). No-ops on non-SQLite / FTS5-absent engines.
        await conn.run_sync(ensure_session_search_fts)
        # In-place bundle upgrade: an EXISTING SQLite file gets the empty table
        # here but no historical rows (only alembic backfills, and the bundle
        # never runs alembic). Seed past meetings once when the table is empty;
        # warm starts are a single cheap COUNT with no writes.
        await conn.run_sync(backfill_if_empty)
        # Packaged-app additive migration: Alembic은 Postgres 전용이고 create_all은
        # 기존 테이블에 컬럼을 추가하지 못한다. 번들 SQLite 경로에서 신규 컬럼을
        # 여기서 보강한다 (idempotent — 새 설치는 create_all이 이미 최신).
        if conn.dialect.name == "sqlite":
            rows = (await conn.exec_driver_sql("PRAGMA table_info(video_job)")).fetchall()
            existing = {row[1] for row in rows}
            if existing:
                if "translate_provider" not in existing:
                    await conn.exec_driver_sql(
                        "ALTER TABLE video_job ADD COLUMN translate_provider VARCHAR(32)")
                if "translate_cli_model" not in existing:
                    await conn.exec_driver_sql(
                        "ALTER TABLE video_job ADD COLUMN translate_cli_model VARCHAR(128)")
# === ANCHOR: SEED_CREATE_SCHEMA_END ===


# === ANCHOR: SEED_BOOTSTRAP_ADMIN_START ===
async def bootstrap_admin(email: str, password: str, *, name: str = ADMIN_NAME) -> bool:
    """Seed the FIRST operator from an explicitly-provided email + password.

    Secure first-run path (AC1.6): unlike ``seed()`` (which carries the dev
    default ``change-me-now`` for Postgres/dev fixtures), this function has NO
    usable default credential — the caller (Slice 4 GUI) MUST supply both
    values. Refuses to seed an empty password so the console can never come up
    with a blank login. No-op (returns False) once any operator row exists, so
    the known default pair can never be planted on the packaged path.

    AC1.7 (JWT_SECRET <-> disposable-SQLite coupling): the keychain JWT_SECRET
    persists while the SQLite DB may be wiped. That is safe because tokens are
    short-lived and re-issued on login: wiping the SQLite file removes the
    operator row, the next launch re-runs create_schema() + bootstrap_admin(),
    and login mints fresh tokens against the persistent JWT_SECRET. No stale
    password hash survives the wipe, so there is no stale-token error state.
    """
    if not email or not password:
        raise ValueError("bootstrap_admin requires a non-empty email and password")

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(select(AppUser))).first()
        if existing is not None:
            return False
        session.add(
            AppUser(
                email=email,
                name=name,
                password_hash=hash_password(password),
                role=ADMIN_ROLE,
                is_active=True,
            )
        )
        await session.commit()
        return True
# === ANCHOR: SEED_BOOTSTRAP_ADMIN_END ===


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
