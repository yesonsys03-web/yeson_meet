# === ANCHOR: SERVER_ENTRY_START ===
"""Frozen-bundle entrypoint for the packaged yeson-server console.

Boot order (CRITICAL):
1. Read injected env. ``DATABASE_URL`` defaults to a SQLite file under the
   per-user app-data dir (the Tauri layer overrides it later); ``HOST``/``PORT``
   are read by ``apps.server.main.run()``.
2. Set ``DATABASE_URL`` in ``os.environ`` *before* importing anything that
   imports ``apps.server.db.session`` — that module binds the SQLAlchemy engine
   from ``DATABASE_URL`` at import time, so the env must be in place first.
3. Call ``create_schema()`` to create the ORM tables on a cold SQLite file
   (NOT Alembic; Alembic stays Postgres-only — see seed.create_schema docstring).
4. Hand off to the single boot path ``apps.server.main.run()`` (uvicorn).

Works both as ``python -m apps.server_desktop.sidecar.server_entry`` and when
frozen by PyInstaller (``getattr(sys, "frozen", False)``).

Gemini-only bundle: with ``YESON_AI_PROVIDER=gemini_live`` (the server default)
nothing imports ``google.cloud.speech``/``translate`` — those imports are lazy
(import-on-use inside the Google provider), so the binary boots without them.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


def _default_appdata_dir() -> Path:
    """Per-user writable dir for the cold SQLite file when DATABASE_URL is unset.

    Mirrors the platform conventions the Tauri layer uses later; here it only
    serves as a fallback so ``python -m`` runs and smoke tests have a home.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "yeson-meet"


def _resolve_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    appdata = _default_appdata_dir()
    appdata.mkdir(parents=True, exist_ok=True)
    db_path = appdata / "yeson-meet.db"
    return f"sqlite+aiosqlite:///{db_path}"


def _bootstrap_admin_mode() -> int:
    """One-shot secure first-operator seeder (AC1.6) — NO network endpoint.

    When ``YESON_BOOTSTRAP_ADMIN=1`` the Tauri layer runs this binary ONCE with
    ``BOOTSTRAP_ADMIN_EMAIL`` + ``BOOTSTRAP_ADMIN_PASSWORD`` injected as env
    (never written to disk). We create the schema, seed the first operator via
    the Slice-1 secure ``bootstrap_admin`` (which has NO usable default
    credential and no-ops once any operator row exists, so the known
    ``admin@yeson.local``/``change-me-now`` pair can never be planted), print a
    machine-readable marker, and EXIT WITHOUT starting uvicorn. Keeping admin
    creation entirely local means it can never leak over the Slice 5 tunnel.
    """
    email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "")
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not email or not password:
        print("BOOTSTRAP_ADMIN_ERROR=missing email or password", file=sys.stderr)
        return 2

    from apps.server.db.seed import bootstrap_admin, create_schema

    async def _run() -> bool:
        await create_schema()
        return await bootstrap_admin(email, password)

    try:
        created = asyncio.run(_run())
    except ValueError as exc:
        print(f"BOOTSTRAP_ADMIN_ERROR={exc}", file=sys.stderr)
        return 2
    # Marker the Tauri side greps to tell "created" from "already existed". The
    # password is NEVER echoed.
    print(f"BOOTSTRAP_ADMIN_CREATED={1 if created else 0}")
    return 0


def main() -> int:
    # Step 1+2: resolve and pin DATABASE_URL BEFORE importing the app, because
    # apps.server.db.session binds the engine from this env var at import time.
    os.environ["DATABASE_URL"] = _resolve_database_url()

    # One-shot bootstrap mode: seed the first operator and exit (no uvicorn).
    if os.environ.get("YESON_BOOTSTRAP_ADMIN") == "1":
        return _bootstrap_admin_mode()

    # Step 3: ensure the schema exists on a cold file (idempotent create_all).
    from apps.server.db.seed import create_schema

    asyncio.run(create_schema())

    # Step 4: single boot path — uvicorn via apps.server.main.run() (reads
    # HOST/PORT from env, defaults 0.0.0.0:8000).
    from apps.server.main import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
# === ANCHOR: SERVER_ENTRY_END ===
