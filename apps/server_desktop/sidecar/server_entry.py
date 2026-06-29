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
import signal
import sys
import threading
import time
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


def _search_selftest_mode() -> int:
    """Frozen-bundle search smoke test (S4): verify FTS5 + index seeding.

    Triggered by ``YESON_SEARCH_SELFTEST=1``. Instead of starting uvicorn this
    (a) asserts the bundled SQLite has the FTS5 engine compiled in, and (b)
    creates a cold throwaway SQLite, runs ``create_schema()`` (which creates the
    standalone ``session_search_fts`` table on the bundle path), seeds known
    ``is_final`` utterances + a summary, runs the index hook, and asserts the
    seeded backfill row counts match — ``kind='utterance'`` rows against the
    is_final utterances and ``kind='summary'`` rows separately (a present-but-
    empty index is the more likely production failure than an absent table).
    Prints machine-readable markers and returns non-zero on any failure. Needs
    no network or auth. Mirrors the report selftest's dependency-guard intent.
    """
    import sqlite3
    import tempfile
    from pathlib import Path

    failures: list[str] = []

    # (a) FTS5 engine present in the bundled sqlite3?
    try:
        probe = sqlite3.connect(":memory:")
        probe.execute("CREATE VIRTUAL TABLE _p USING fts5(x)")
        probe.close()
        print("SEARCH_SELFTEST fts5_engine=ok")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"fts5_engine: {type(exc).__name__}: {exc}")
        print("SEARCH_SELFTEST fts5_engine=FAIL", file=sys.stderr)

    # (b) create_schema creates the table + seeded counts match.
    async def _seed_and_assert() -> None:
        from datetime import datetime, timezone

        from sqlalchemy import text as _t
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        import apps.server.db.search as search_mod
        import apps.server.db.seed as seed_mod
        import apps.server.db.session as session_mod
        from apps.server.db.models import AppUser, Session as MSession, Utterance

        tmp = Path(tempfile.mkdtemp(prefix="yeson-search-selftest-"))
        url = f"sqlite+aiosqlite:///{tmp / 'search.db'}"
        engine = create_async_engine(url, echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        # Bind seed/session module globals so create_schema targets our engine.
        seed_mod.engine = engine
        seed_mod.AsyncSessionLocal = factory
        session_mod.AsyncSessionLocal = factory

        await seed_mod.create_schema()

        n_utter = 3
        now = datetime.now(timezone.utc)
        async with factory() as db:
            user = AppUser(email="s@s", name="S", password_hash="x", role="operator")
            db.add(user)
            await db.flush()
            from uuid import uuid4

            meeting = MSession(
                external_id=uuid4(), owner_user_id=user.id, title="T", status="ended",
                started_at=now, ended_at=now,
            )
            db.add(meeting)
            await db.flush()
            for i in range(n_utter):
                db.add(
                    Utterance(
                        session_id=meeting.id, seq=i + 1, speaker=None,
                        text_en=f"line {i}", text_ko=f"줄 {i}",
                        started_at=now, ended_at=now, is_final=True,
                    )
                )
            await db.commit()
            utts = [(f"줄 {i}", f"line {i}") for i in range(n_utter)]
            await search_mod.reindex_session_fts(db, meeting.id, utts, "요약 본문")
            await db.commit()

            u_rows = (
                await db.execute(
                    _t("SELECT count(*) FROM session_search_fts WHERE kind='utterance'")
                )
            ).scalar()
            s_rows = (
                await db.execute(
                    _t("SELECT count(*) FROM session_search_fts WHERE kind='summary'")
                )
            ).scalar()

        print(f"SEARCH_SELFTEST utterance_rows={u_rows} (expected {n_utter})")
        print(f"SEARCH_SELFTEST summary_rows={s_rows} (expected 1)")
        if u_rows != n_utter:
            failures.append(f"utterance_rows {u_rows} != {n_utter}")
        if s_rows != 1:
            failures.append(f"summary_rows {s_rows} != 1")
        await engine.dispose()

    try:
        asyncio.run(_seed_and_assert())
    except Exception as exc:  # noqa: BLE001
        failures.append(f"seed_and_assert: {type(exc).__name__}: {exc}")
        print("SEARCH_SELFTEST seed=FAIL", file=sys.stderr)

    if failures:
        for f in failures:
            print(f"SEARCH_SELFTEST_FAIL {f}", file=sys.stderr)
        print("SEARCH_SELFTEST_RESULT=FAIL", file=sys.stderr)
        return 1
    print("SEARCH_SELFTEST_RESULT=PASS")
    return 0


def _inspect_backup_mode() -> int:
    """Read-only backup preview (YESON_INSPECT_BACKUP=1). Prints INSPECT_RESULT=json."""
    import json
    from pathlib import Path
    from apps.server.domain.restore import inspect_backup, validate_restore

    snap = os.environ.get("YESON_SNAPSHOT_PATH", "")
    if not snap:
        print("INSPECT_ERROR=missing YESON_SNAPSHOT_PATH", file=sys.stderr)
        return 2
    # Catch EVERY exception (not just RestoreError): a raw exception escaping a
    # frozen one-shot surfaces only as the opaque "[PYI-...] Failed to execute
    # script" bootloader crash. Convert it to a readable INSPECT_ERROR (+ a
    # traceback on stderr the desktop log captures) so the cause is visible.
    try:
        info = inspect_backup(Path(snap))
        v = validate_restore(info, os.environ.get("YESON_CURRENT_VERSION") or None)
        print("INSPECT_RESULT=" + json.dumps({
            "stamp": info.stamp, "integrity_ok": info.integrity_ok,
            "app_version": info.app_version, "session_count": info.session_count,
            "utterance_count": info.utterance_count, "snapshot_bytes": info.snapshot_bytes,
            "has_storage_zip": info.storage_zip_path is not None,
            "validation": {"ok": v.ok, "level": v.level, "reason": v.reason},
        }, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 — never crash as PYI-16632
        import traceback
        traceback.print_exc()
        print(f"INSPECT_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _restore_mode() -> int:
    """Swap a backup into the live DB+storage (YESON_RESTORE=1). Server must be stopped."""
    import json
    from datetime import datetime
    from pathlib import Path
    from apps.server.domain.backup import db_path_from_url
    from apps.server.domain.restore import perform_restore

    snap = os.environ.get("YESON_SNAPSHOT_PATH", "")
    if not snap:
        print("RESTORE_ERROR=missing YESON_SNAPSHOT_PATH", file=sys.stderr)
        return 2
    db_path = db_path_from_url(_resolve_database_url())
    storage_root = Path(os.environ.get("STORAGE_ROOT", str(db_path.parent / "storage")))
    zip_env = os.environ.get("YESON_STORAGE_ZIP", "")
    safety_dir = Path(os.environ.get("YESON_SAFETY_DIR", str(db_path.parent / "pre-restore")))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # Catch EVERY exception (Windows os.replace PermissionError, path/URI errors,
    # etc.) so the frozen one-shot reports a readable RESTORE_ERROR instead of the
    # opaque "[PYI-...] Failed to execute script" bootloader crash.
    try:
        result = perform_restore(
            snapshot_path=Path(snap),
            storage_zip_path=Path(zip_env) if zip_env else None,
            db_path=db_path, storage_root=storage_root,
            safety_dir=safety_dir, stamp=stamp,
        )
        print("RESTORE_RESULT=" + json.dumps({
            "integrity_ok": result.integrity_ok,
            "restored_bytes": result.restored_bytes,
            "storage_restored": result.storage_restored,
            "safety_dir": str(result.safety_dir),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 — never crash as PYI-16632
        import traceback
        traceback.print_exc()
        print(f"RESTORE_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _install_parent_death_watchdog() -> None:
    """Exit gracefully if our spawning parent dies (macOS dev orphan guard).

    The Tauri console spawns this server as a child. On a clean window-close the
    Rust RunEvent handler reaps it, but an abrupt parent exit (e.g. Ctrl+C of
    ``tauri:dev``) leaves the server orphaned, still holding port 8000 — the next
    start then fails with ``[Errno 48] address already in use``. macOS has no
    ``PR_SET_PDEATHSIG``, so poll the parent pid: once we are reparented (ppid
    changes / -> 1) the parent is gone, so SIGTERM ourselves for uvicorn's
    graceful shutdown (releases the port + closes SQLite). Only armed when we have
    a real parent (ppid > 1) — true for the desktop-spawned case but NOT for
    init/systemd-managed deployments (ppid == 1), which are left untouched.
    """
    initial_ppid = os.getppid()
    if initial_ppid <= 1:
        return

    def _watch() -> None:
        while True:
            time.sleep(2.0)
            if os.getppid() != initial_ppid:
                print(
                    "PARENT_EXIT detected (ppid changed) -- shutting down server",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    os.kill(os.getpid(), signal.SIGTERM)
                except Exception:  # noqa: BLE001
                    os._exit(0)
                # Backstop: force-exit if graceful shutdown stalls.
                time.sleep(5.0)
                os._exit(0)

    threading.Thread(
        target=_watch, name="parent-death-watchdog", daemon=True
    ).start()


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

    # Frozen-bundle search smoke test (S4): assert FTS5 + index seeding and exit.
    if os.environ.get("YESON_SEARCH_SELFTEST") == "1":
        return _search_selftest_mode()

    # One-shot backup inspect mode: read a snapshot's metadata and exit (no uvicorn).
    if os.environ.get("YESON_INSPECT_BACKUP") == "1":
        return _inspect_backup_mode()

    # One-shot restore mode: swap a backup into the live DB+storage and exit (no uvicorn).
    if os.environ.get("YESON_RESTORE") == "1":
        return _restore_mode()

    # Step 3: ensure the schema exists on a cold file (idempotent create_all).
    from apps.server.db.seed import create_schema

    asyncio.run(create_schema())

    # Step 3.5: arm the parent-death watchdog so a dev-mode parent exit (Ctrl+C
    # of tauri:dev) doesn't leave this server orphaned holding the port.
    _install_parent_death_watchdog()

    # Step 4: single boot path — uvicorn via apps.server.main.run() (reads
    # HOST/PORT from env, defaults 0.0.0.0:8000).
    from apps.server.main import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
# === ANCHOR: SERVER_ENTRY_END ===
