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


def _report_selftest_mode() -> int:
    """Frozen-bundle smoke test (S7): run every report builder on dummy data.

    Triggered by ``YESON_REPORT_SELFTEST=1``. Instead of starting uvicorn this
    imports and runs the md/html/docx (and summary html/docx) builders, plus the
    LibreOffice PDF conversion when soffice is present. It exists to catch deps
    that pass in the dev venv but are missing from the PyInstaller bundle
    (python-docx / lxml are the usual suspects). Prints machine-readable markers
    and returns non-zero on any failure. Needs no DB, network, or auth.
    """
    from datetime import datetime, timezone
    from types import SimpleNamespace

    base = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
    meeting = SimpleNamespace(
        title="Selftest 회의",
        external_id="selftest",
        status="ended",
        started_at=base,
        ended_at=base,
        client_label="SELFTEST",
    )
    utts = [
        SimpleNamespace(
            seq=1, speaker="A", text_en="Hello", text_ko="안녕하세요",
            started_at=base, ended_at=base,
        )
    ]
    summary = "요약 본문 한 줄."
    failures: list[str] = []

    def _check(name: str, fn) -> None:
        try:
            out = fn()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"SELFTEST {name}=FAIL", file=sys.stderr)
            return
        ok = len(out) > 0 if isinstance(out, (str, bytes, bytearray)) else bool(out)
        print(f"SELFTEST {name}={'ok' if ok else 'EMPTY'}")
        if not ok:
            failures.append(f"{name}: empty output")

    from apps.server.domain.report_docx import build_session_report_docx, build_summary_docx
    from apps.server.domain.report_html import build_session_report_html, build_summary_html
    from apps.server.domain.report_pdf import convert_docx_to_pdf, find_soffice
    from apps.server.domain.reports import build_session_report

    _check("md", lambda: build_session_report(meeting, utts, summary=summary))
    _check("html", lambda: build_session_report_html(meeting, utts, summary=summary))
    _check("docx", lambda: build_session_report_docx(meeting, utts, summary=summary))
    _check("summary_html", lambda: build_summary_html(meeting, summary))
    _check("summary_docx", lambda: build_summary_docx(meeting, summary))

    # PDF needs LibreOffice (external, not bundled) — verify only when present.
    if find_soffice():
        def _pdf() -> bytes:
            pdf = convert_docx_to_pdf(build_session_report_docx(meeting, utts))
            if not (pdf and pdf[:5] == b"%PDF-"):
                raise RuntimeError("soffice returned no/invalid PDF")
            return pdf

        _check("pdf", _pdf)
    else:
        print("SELFTEST pdf=skip (soffice not installed)")

    if failures:
        for f in failures:
            print(f"SELFTEST_FAIL {f}", file=sys.stderr)
        print("SELFTEST_RESULT=FAIL", file=sys.stderr)
        return 1
    print("SELFTEST_RESULT=PASS")
    return 0


def main() -> int:
    # Step 1+2: resolve and pin DATABASE_URL BEFORE importing the app, because
    # apps.server.db.session binds the engine from this env var at import time.
    os.environ["DATABASE_URL"] = _resolve_database_url()

    # One-shot bootstrap mode: seed the first operator and exit (no uvicorn).
    if os.environ.get("YESON_BOOTSTRAP_ADMIN") == "1":
        return _bootstrap_admin_mode()

    # Frozen-bundle report smoke test (S7): exercise builders and exit (no uvicorn).
    if os.environ.get("YESON_REPORT_SELFTEST") == "1":
        return _report_selftest_mode()

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
