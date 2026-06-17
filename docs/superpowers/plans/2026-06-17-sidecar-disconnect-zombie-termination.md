# Sidecar Disconnect Zombie Termination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Force-end a `live` meeting whose sidecar WebSocket has stayed disconnected past a configurable grace period (default 5 min), closing the zombie-session gap left by the wall-clock-only safety watchdog.

**Architecture:** Server-only, event-driven. A new nullable `Session.disconnected_at` column is stamped when the sidecar WS drops and cleared when it (re)connects. The existing 60s background watchdog gains a second per-session check (`enforce_sidecar_disconnect_limit`) alongside the shipped wall-clock check. A one-shot startup re-stamp makes post-restart zombies eligible. No sidecar / Rust / desktop changes → no new Windows CI build.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy async, Alembic, asyncpg, pytest / pytest-asyncio. Test DB schema is built from `Base.metadata.create_all` (conftest), so a model change is visible to tests immediately; the Alembic migration is for production deploy.

**Spec:** `docs/superpowers/specs/2026-06-17-sidecar-disconnect-zombie-termination-design.md`

---

## File Structure

- `apps/server/db/models.py` — add `disconnected_at` column to `Session` (MODELS_SESSION anchor).
- `apps/server/db/alembic/versions/0002_session_disconnected_at.py` — **create**, prod migration.
- `apps/server/ops/alerts.py` — add `MEETING_SIDECAR_DISCONNECTED` constant + `raise_meeting_disconnect_alert`.
- `apps/server/ops/session_safety.py` — add `disconnect_grace`, `session_disconnect_exceeds_grace`, `enforce_sidecar_disconnect_limit`, `stamp_sidecar_disconnected`. **Do not touch** the shipped wall-clock functions.
- `apps/server/ops/session_safety_scheduler.py` — extend `_sweep_once`; add `stamp_live_sessions_disconnected`.
- `apps/server/ws/sidecar.py` — clear stamp on accept; stamp on disconnect (finally).
- `apps/server/main.py` — call `stamp_live_sessions_disconnected` once at lifespan startup.
- `apps/server/tests/test_session_safety.py` — unit tests for column default, grace config, predicate, enforce, stamp helper, alert.
- `apps/server/tests/test_session_safety_scheduler.py` — unit tests for extended sweep + startup re-stamp helper.
- `docs/ARCHITECTURE.md`, `docs/ROADMAP.md` — env knob doc + Slice 4 checkbox.

---

## Task 1: Add `Session.disconnected_at` column + Alembic migration

**Files:**
- Modify: `apps/server/db/models.py` (MODELS_SESSION anchor, after `ended_at`, ~line 95)
- Create: `apps/server/db/alembic/versions/0002_session_disconnected_at.py`
- Test: `apps/server/tests/test_session_safety.py`

- [ ] **Step 1: Write the failing test**

Add to `apps/server/tests/test_session_safety.py` (uses the existing `_create_meeting` helper and `db_session` fixture):

```python
@pytest.mark.asyncio
async def test_new_session_disconnected_at_defaults_none(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(minutes=1))
    assert meeting.disconnected_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/server/tests/test_session_safety.py::test_new_session_disconnected_at_defaults_none -v`
Expected: FAIL — `AttributeError: 'Session' object has no attribute 'disconnected_at'`

- [ ] **Step 3: Add the column to the model**

In `apps/server/db/models.py`, inside the `MODELS_SESSION` anchor, immediately after the `ended_at` mapped_column (line ~93-95) and before `created_at`:

```python
    disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest apps/server/tests/test_session_safety.py::test_new_session_disconnected_at_defaults_none -v`
Expected: PASS (the session-scoped `setup_schema` fixture rebuilds the test schema with the new column).

- [ ] **Step 5: Create the Alembic migration (production)**

Create `apps/server/db/alembic/versions/0002_session_disconnected_at.py`:

```python
"""add session.disconnected_at for sidecar disconnect zombie termination

Revision ID: 0002_session_disconnected_at
Revises: 0001_initial
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_session_disconnected_at"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session",
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session", "disconnected_at")
```

- [ ] **Step 6: Sanity-check the migration is well-formed**

Run: `uv run python -c "import importlib.util,sys; s=importlib.util.spec_from_file_location('m','apps/server/db/alembic/versions/0002_session_disconnected_at.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.revision, m.down_revision)"`
Expected: `0002_session_disconnected_at 0001_initial`

- [ ] **Step 7: Commit**

```bash
git add apps/server/db/models.py apps/server/db/alembic/versions/0002_session_disconnected_at.py apps/server/tests/test_session_safety.py
git commit -m "feat(safety): add Session.disconnected_at column + migration"
```

---

## Task 2: Operator alert for sidecar disconnect

**Files:**
- Modify: `apps/server/ops/alerts.py` (constants near line 90, function after `raise_meeting_max_duration_alert`)
- Test: `apps/server/tests/test_session_safety.py`

- [ ] **Step 1: Write the failing test**

Add to `apps/server/tests/test_session_safety.py` (add `raise_meeting_disconnect_alert` and `MEETING_SIDECAR_DISCONNECTED` to the existing alerts import line):

```python
def test_raise_meeting_disconnect_alert_records_critical() -> None:
    raise_meeting_disconnect_alert("abc-123")
    alerts = operator_alerts.active()
    assert len(alerts) == 1
    assert alerts[0].code == f"{MEETING_SIDECAR_DISCONNECTED}:abc-123"
    assert alerts[0].severity == "critical"
```

Update the import at the top of the file:

```python
from apps.server.ops.alerts import (
    MEETING_MAX_DURATION_EXCEEDED,
    MEETING_SIDECAR_DISCONNECTED,
    operator_alerts,
    raise_meeting_disconnect_alert,
)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/server/tests/test_session_safety.py::test_raise_meeting_disconnect_alert_records_critical -v`
Expected: FAIL — `ImportError: cannot import name 'MEETING_SIDECAR_DISCONNECTED'`

- [ ] **Step 3: Implement the alert**

In `apps/server/ops/alerts.py`, add the constant next to the existing ones (line ~90):

```python
MEETING_SIDECAR_DISCONNECTED = "meeting_sidecar_disconnected"
```

Add the function inside the `ALERTS` anchor, after `raise_meeting_max_duration_alert` (before `# === ANCHOR: ALERTS_END ===`):

```python
def raise_meeting_disconnect_alert(session_id: str) -> None:
    """Raise a non-secret alert when a meeting is force-ended because its
    sidecar stayed disconnected past YESON_MEETING_DISCONNECT_GRACE_SECONDS."""
    _ = operator_alerts.raise_alert(
        code=f"{MEETING_SIDECAR_DISCONNECTED}:{session_id}",
        severity="critical",
        message=(
            "Meeting sidecar disconnected past "
            "YESON_MEETING_DISCONNECT_GRACE_SECONDS and the meeting was "
            f"automatically ended: session={session_id}."
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest apps/server/tests/test_session_safety.py::test_raise_meeting_disconnect_alert_records_critical -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/ops/alerts.py apps/server/tests/test_session_safety.py
git commit -m "feat(safety): add sidecar-disconnect operator alert"
```

---

## Task 3: Grace config + disconnect predicate

**Files:**
- Modify: `apps/server/ops/session_safety.py` (add `from sqlalchemy import select`; new constants + two functions; leave wall-clock code untouched)
- Test: `apps/server/tests/test_session_safety.py`

- [ ] **Step 1: Write the failing tests**

Add to `apps/server/tests/test_session_safety.py` (add the new names to the `session_safety` import: `disconnect_grace`, `session_disconnect_exceeds_grace`):

```python
def test_disconnect_grace_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YESON_MEETING_DISCONNECT_GRACE_SECONDS", raising=False)
    assert disconnect_grace() == timedelta(seconds=300)


def test_disconnect_grace_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YESON_MEETING_DISCONNECT_GRACE_SECONDS", "30")
    assert disconnect_grace() == timedelta(seconds=30)


def test_disconnect_grace_non_positive_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YESON_MEETING_DISCONNECT_GRACE_SECONDS", "0")
    assert disconnect_grace() == timedelta.max


def test_disconnect_grace_malformed_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YESON_MEETING_DISCONNECT_GRACE_SECONDS", "xyz")
    assert disconnect_grace() == timedelta(seconds=300)


def test_session_disconnect_exceeds_grace_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YESON_MEETING_DISCONNECT_GRACE_SECONDS", "300")
    now = datetime.now(timezone.utc)
    assert session_disconnect_exceeds_grace(now - timedelta(seconds=301), now) is True
    assert session_disconnect_exceeds_grace(now - timedelta(seconds=299), now) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_session_safety.py -k "disconnect_grace or exceeds_grace" -v`
Expected: FAIL — `ImportError: cannot import name 'disconnect_grace'`

- [ ] **Step 3: Implement config + predicate**

In `apps/server/ops/session_safety.py`, add `from sqlalchemy import select` to the imports (after the `from sqlalchemy.ext.asyncio import AsyncSession` line). Then, after the existing `MAX_DURATION_ENV` line (~line 16), add:

```python
DEFAULT_DISCONNECT_GRACE_SECONDS = 300.0
DISCONNECT_GRACE_ENV = "YESON_MEETING_DISCONNECT_GRACE_SECONDS"
```

Add these functions just before the `_as_utc` helper (so `_as_utc` stays the last helper in the anchor):

```python
def disconnect_grace() -> timedelta:
    """Grace before a disconnected live meeting is force-ended; non-positive disables it."""
    raw = os.environ.get(DISCONNECT_GRACE_ENV, str(DEFAULT_DISCONNECT_GRACE_SECONDS))
    try:
        seconds = float(raw)
    except ValueError:
        seconds = DEFAULT_DISCONNECT_GRACE_SECONDS
    if seconds <= 0:
        return timedelta.max
    return timedelta(seconds=seconds)


def session_disconnect_exceeds_grace(
    disconnected_at: datetime,
    now: datetime | None = None,
) -> bool:
    """Return True when a disconnected live meeting should be force-ended."""
    current = now or datetime.now(timezone.utc)
    return _as_utc(current) - _as_utc(disconnected_at) >= disconnect_grace()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/server/tests/test_session_safety.py -k "disconnect_grace or exceeds_grace" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/server/ops/session_safety.py apps/server/tests/test_session_safety.py
git commit -m "feat(safety): add disconnect grace config + predicate"
```

---

## Task 4: `enforce_sidecar_disconnect_limit`

**Files:**
- Modify: `apps/server/ops/session_safety.py` (new async function; wall-clock function untouched)
- Test: `apps/server/tests/test_session_safety.py`

- [ ] **Step 1: Write the failing tests**

Add `enforce_sidecar_disconnect_limit` to the `session_safety` import. Append these tests (the `_create_meeting` helper sets no `disconnected_at`, so set it explicitly per test):

```python
@pytest.mark.asyncio
async def test_enforce_disconnect_ends_long_gone_session(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESON_MEETING_DISCONNECT_GRACE_SECONDS", "300")
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(hours=1))
    meeting.disconnected_at = now - timedelta(seconds=301)
    await db_session.flush()

    enforced = await enforce_sidecar_disconnect_limit(db_session, meeting, now=now)

    assert enforced is True
    assert meeting.status == "ended"
    assert meeting.ended_at == now
    alerts = operator_alerts.active()
    assert len(alerts) == 1
    assert alerts[0].code == f"{MEETING_SIDECAR_DISCONNECTED}:{meeting.external_id}"


@pytest.mark.asyncio
async def test_enforce_disconnect_ignores_connected_session(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESON_MEETING_DISCONNECT_GRACE_SECONDS", "300")
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(hours=1))
    # disconnected_at is None -> still connected
    enforced = await enforce_sidecar_disconnect_limit(db_session, meeting, now=now)

    assert enforced is False
    assert meeting.status == "live"
    assert operator_alerts.active() == []


@pytest.mark.asyncio
async def test_enforce_disconnect_ignores_within_grace(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESON_MEETING_DISCONNECT_GRACE_SECONDS", "300")
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(hours=1))
    meeting.disconnected_at = now - timedelta(seconds=120)
    await db_session.flush()

    enforced = await enforce_sidecar_disconnect_limit(db_session, meeting, now=now)

    assert enforced is False
    assert meeting.status == "live"


@pytest.mark.asyncio
async def test_enforce_disconnect_noop_on_ended_session(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESON_MEETING_DISCONNECT_GRACE_SECONDS", "300")
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(hours=1))
    meeting.status = "ended"
    meeting.disconnected_at = now - timedelta(seconds=301)
    await db_session.flush()

    enforced = await enforce_sidecar_disconnect_limit(db_session, meeting, now=now)

    assert enforced is False


@pytest.mark.asyncio
async def test_enforce_disconnect_publishes_session_ended(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.server.ws.bus import bus

    monkeypatch.setenv("YESON_MEETING_DISCONNECT_GRACE_SECONDS", "300")
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(hours=1))
    meeting.disconnected_at = now - timedelta(seconds=301)
    await db_session.flush()

    queue = bus.subscribe(meeting.external_id)
    try:
        enforced = await enforce_sidecar_disconnect_limit(db_session, meeting, now=now)
        assert enforced is True
        payload = queue.get_nowait()
    finally:
        bus.unsubscribe(meeting.external_id, queue)

    assert payload["type"] == "session.ended"
    assert payload["session_id"] == str(meeting.external_id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_session_safety.py -k "enforce_disconnect" -v`
Expected: FAIL — `ImportError: cannot import name 'enforce_sidecar_disconnect_limit'`

- [ ] **Step 3: Implement the enforcement**

In `apps/server/ops/session_safety.py`, add this function after `enforce_meeting_duration_limit` (and its `_END` anchor), before `_as_utc`. It must import `raise_meeting_disconnect_alert` — extend the existing alerts import:

```python
from apps.server.ops.alerts import (
    raise_meeting_disconnect_alert,
    raise_meeting_max_duration_alert,
)
```

Then add:

```python
async def enforce_sidecar_disconnect_limit(
    db: AsyncSession,
    meeting: Session,
    now: datetime | None = None,
) -> bool:
    """Mark a live meeting ended once its sidecar has been gone past the grace period."""
    if meeting.status == "ended":
        return False
    if meeting.disconnected_at is None:
        return False
    ended_at = _as_utc(now or datetime.now(timezone.utc))
    if not session_disconnect_exceeds_grace(meeting.disconnected_at, ended_at):
        return False

    meeting.status = "ended"
    meeting.ended_at = ended_at
    await db.commit()
    raise_meeting_disconnect_alert(str(meeting.external_id))
    await bus.publish(
        meeting.external_id,
        serialize(
            SessionEnded(
                session_id=meeting.external_id,
                occurred_at=ended_at,
                ended_at=ended_at,
            )
        ),
    )
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/server/tests/test_session_safety.py -k "enforce_disconnect" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the whole file to confirm no wall-clock regression**

Run: `uv run pytest apps/server/tests/test_session_safety.py -q`
Expected: PASS (all existing + new tests)

- [ ] **Step 6: Commit**

```bash
git add apps/server/ops/session_safety.py apps/server/tests/test_session_safety.py
git commit -m "feat(safety): enforce sidecar-disconnect zombie termination"
```

---

## Task 5: `stamp_sidecar_disconnected` helper (WS finally path)

**Files:**
- Modify: `apps/server/ops/session_safety.py` (new async helper)
- Test: `apps/server/tests/test_session_safety.py`

- [ ] **Step 1: Write the failing tests**

Add `stamp_sidecar_disconnected` to the `session_safety` import. Append:

```python
@pytest.mark.asyncio
async def test_stamp_sidecar_disconnected_sets_timestamp_on_live(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(minutes=5))
    await db_session.commit()

    await stamp_sidecar_disconnected(db_session, meeting.id, now=now)

    await db_session.refresh(meeting)
    assert meeting.disconnected_at == now


@pytest.mark.asyncio
async def test_stamp_sidecar_disconnected_skips_ended(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(minutes=5))
    meeting.status = "ended"
    await db_session.commit()

    await stamp_sidecar_disconnected(db_session, meeting.id, now=now)

    await db_session.refresh(meeting)
    assert meeting.disconnected_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_session_safety.py -k "stamp_sidecar" -v`
Expected: FAIL — `ImportError: cannot import name 'stamp_sidecar_disconnected'`

- [ ] **Step 3: Implement the helper**

In `apps/server/ops/session_safety.py`, add after `enforce_sidecar_disconnect_limit`:

```python
async def stamp_sidecar_disconnected(
    db: AsyncSession,
    session_pk: int,
    now: datetime | None = None,
) -> None:
    """Record the disconnect instant on a still-live meeting; no-op if ended."""
    meeting = (
        await db.execute(select(Session).where(Session.id == session_pk))
    ).scalar_one_or_none()
    if meeting is None or meeting.status != "live":
        return
    meeting.disconnected_at = _as_utc(now or datetime.now(timezone.utc))
    await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/server/tests/test_session_safety.py -k "stamp_sidecar" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/server/ops/session_safety.py apps/server/tests/test_session_safety.py
git commit -m "feat(safety): add stamp_sidecar_disconnected helper"
```

---

## Task 6: Extend watchdog sweep + startup re-stamp helper

**Files:**
- Modify: `apps/server/ops/session_safety_scheduler.py`
- Test: `apps/server/tests/test_session_safety_scheduler.py`

- [ ] **Step 1: Write the failing tests**

In `apps/server/tests/test_session_safety_scheduler.py`, extend the scheduler import:

```python
from apps.server.ops.session_safety_scheduler import (
    _sweep_once,
    run_meeting_safety_watchdog,
    safety_poll_interval,
    stamp_live_sessions_disconnected,
)
```

The existing `_create_live_meeting` helper does not set `disconnected_at`; tests set it directly. Append:

```python
async def _create_disconnected_meeting(
    db_session: AsyncSession, disconnected_at: datetime
) -> Session:
    now = datetime.now(timezone.utc)
    meeting = await _create_live_meeting(db_session, now - timedelta(minutes=10))
    meeting.disconnected_at = disconnected_at
    await db_session.commit()
    return meeting


@pytest.mark.asyncio
async def test_sweep_ends_long_disconnected_session(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    monkeypatch.setenv("YESON_MEETING_DISCONNECT_GRACE_SECONDS", "300")
    now = datetime.now(timezone.utc)
    meeting = await _create_disconnected_meeting(db_session, now - timedelta(seconds=301))

    ended = await _sweep_once(_factory(db_session))

    assert ended == 1
    async with _factory(db_session)() as db2:
        refreshed = (
            await db2.execute(select(Session).where(Session.id == meeting.id))
        ).scalar_one()
    assert refreshed.status == "ended"


@pytest.mark.asyncio
async def test_sweep_keeps_connected_and_within_grace(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    monkeypatch.setenv("YESON_MEETING_DISCONNECT_GRACE_SECONDS", "300")
    now = datetime.now(timezone.utc)
    connected = await _create_live_meeting(db_session, now - timedelta(minutes=10))
    within = await _create_disconnected_meeting(db_session, now - timedelta(seconds=60))

    ended = await _sweep_once(_factory(db_session))

    assert ended == 0
    async with _factory(db_session)() as db2:
        for m in (connected, within):
            refreshed = (
                await db2.execute(select(Session).where(Session.id == m.id))
            ).scalar_one()
            assert refreshed.status == "live"


@pytest.mark.asyncio
async def test_stamp_live_sessions_disconnected_stamps_only_null_live(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(timezone.utc)
    null_live = await _create_live_meeting(db_session, now - timedelta(minutes=5))
    already = await _create_disconnected_meeting(db_session, now - timedelta(minutes=2))

    stamped = await stamp_live_sessions_disconnected(_factory(db_session), now=now)

    assert stamped == 1
    async with _factory(db_session)() as db2:
        refreshed_null = (
            await db2.execute(select(Session).where(Session.id == null_live.id))
        ).scalar_one()
        refreshed_already = (
            await db2.execute(select(Session).where(Session.id == already.id))
        ).scalar_one()
    assert refreshed_null.disconnected_at == now
    assert refreshed_already.disconnected_at == now - timedelta(minutes=2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/server/tests/test_session_safety_scheduler.py -k "disconnect or stamp_live" -v`
Expected: FAIL — `ImportError: cannot import name 'stamp_live_sessions_disconnected'`

- [ ] **Step 3: Implement the sweep extension + startup helper**

In `apps/server/ops/session_safety_scheduler.py`:

Extend the imports — add `datetime`/`timezone` and the new enforce function:

```python
from datetime import datetime, timezone
```

```python
from apps.server.ops.session_safety import (
    enforce_meeting_duration_limit,
    enforce_sidecar_disconnect_limit,
)
```

Replace the body of `_sweep_once` so each live meeting is checked against both limits in the same loop (wall-clock first; `or` short-circuits if it already ended the row):

```python
async def _sweep_once(session_factory: async_sessionmaker) -> int:
    """Force-end every over-duration or long-disconnected live meeting."""
    ended = 0
    async with session_factory() as db:
        live = (
            await db.execute(select(Session).where(Session.status == "live"))
        ).scalars().all()
        for meeting in live:
            if await enforce_meeting_duration_limit(db, meeting) or (
                await enforce_sidecar_disconnect_limit(db, meeting)
            ):
                ended += 1
    return ended
```

Add the startup re-stamp helper after `_sweep_once`:

```python
async def stamp_live_sessions_disconnected(
    session_factory: async_sessionmaker,
    now: datetime | None = None,
) -> int:
    """Stamp disconnected_at=now on every live session lacking it (startup only).

    On a fresh boot no sidecar is connected yet, so live rows with a NULL
    disconnected_at are made eligible for the disconnect watchdog. A genuinely
    live sidecar reconnects within its backoff and clears the stamp well inside
    the grace period, so no active meeting is wrongly ended.
    """
    when = now or datetime.now(timezone.utc)
    stamped = 0
    async with session_factory() as db:
        live = (
            await db.execute(
                select(Session).where(
                    Session.status == "live",
                    Session.disconnected_at.is_(None),
                )
            )
        ).scalars().all()
        for meeting in live:
            meeting.disconnected_at = when
            stamped += 1
        if stamped:
            await db.commit()
    return stamped
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/server/tests/test_session_safety_scheduler.py -k "disconnect or stamp_live" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the whole scheduler file (no wall-clock regression)**

Run: `uv run pytest apps/server/tests/test_session_safety_scheduler.py -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add apps/server/ops/session_safety_scheduler.py apps/server/tests/test_session_safety_scheduler.py
git commit -m "feat(safety): sweep sidecar-disconnect + startup re-stamp"
```

---

## Task 7: Wire WS lifecycle (clear on accept, stamp on disconnect)

**Files:**
- Modify: `apps/server/ws/sidecar.py`

No new unit test: `test_ws_sidecar_binary.py` is module-skipped (TestClient×asyncpg deadlock), so the WS path is verified by the helper unit tests (Task 5) plus a full-suite run and manual review. This task is pure wiring of already-tested functions.

- [ ] **Step 1: Import the stamp helper**

In `apps/server/ws/sidecar.py`, extend the existing `session_safety` import block (currently importing `enforce_meeting_duration_limit`, `session_started_at_exceeds_max_duration`):

```python
from apps.server.ops.session_safety import (
    enforce_meeting_duration_limit,
    session_started_at_exceeds_max_duration,
    stamp_sidecar_disconnected,
)
```

- [ ] **Step 2: Clear the stamp on accept (reconnect resets grace)**

Inside the auth/resolve `async with AsyncSessionLocal() as db:` block, immediately after the `if meeting.device_id is None:` device-binding commit and **before** the `if await enforce_meeting_duration_limit(db, meeting):` check (around line 273-276), add:

```python
        if meeting.disconnected_at is not None:
            meeting.disconnected_at = None
            await db.commit()
```

- [ ] **Step 3: Stamp on disconnect in the finally block**

In the handler's `finally:` block (currently stops `ai_session`), after the existing `await ai_session.stop()` lines, add a guarded stamp that must never raise out of `finally`:

```python
    finally:
        if ai_session is not None:
            # Only deregister if a later handler hasn't already replaced us.
            if _active_ai_sessions.get(session_uuid) is ai_session:
                del _active_ai_sessions[session_uuid]
            await ai_session.stop()
        try:
            async with AsyncSessionLocal() as db:
                await stamp_sidecar_disconnected(db, session_pk)
        except Exception:
            logger.exception("Failed to stamp sidecar disconnect", extra=trace_extra)
```

(`session_pk` and `trace_extra` are already defined in the handler scope above the `try`.)

- [ ] **Step 4: Verify the module imports and the server test suite is green**

Run: `uv run python -c "import apps.server.ws.sidecar"`
Expected: no output, exit 0.

Run: `uv run pytest apps/server/tests -q`
Expected: PASS (same pass/skip counts as before, plus the new tests).

- [ ] **Step 5: Commit**

```bash
git add apps/server/ws/sidecar.py
git commit -m "feat(safety): stamp/clear Session.disconnected_at on sidecar WS lifecycle"
```

---

## Task 8: Startup re-stamp wiring in lifespan

**Files:**
- Modify: `apps/server/main.py` (lifespan, imports)

Lifespan is exercised through the deadlock-prone TestClient path, so it is not unit-tested here; the `stamp_live_sessions_disconnected` helper is covered by Task 6. This is minimal wiring.

- [ ] **Step 1: Extend imports**

In `apps/server/main.py`, extend the scheduler import:

```python
from apps.server.ops.session_safety_scheduler import (
    run_meeting_safety_watchdog,
    safety_poll_interval,
    stamp_live_sessions_disconnected,
)
```

Add the session factory import (after the scheduler import block):

```python
from apps.server.db.session import AsyncSessionLocal
```

- [ ] **Step 2: Call the re-stamp once before starting the watchdog**

In `lifespan`, replace the `if interval > 0:` branch so the re-stamp runs first, guarded so a transient DB hiccup at boot does not crash startup:

```python
    interval = safety_poll_interval()
    if interval > 0:
        try:
            stamped = await stamp_live_sessions_disconnected(AsyncSessionLocal)
            if stamped:
                logger.info(
                    "Stamped live sessions disconnected at startup",
                    extra={"count": stamped},
                )
        except Exception:
            logger.exception("Startup disconnect re-stamp failed")
        watchdog = asyncio.create_task(run_meeting_safety_watchdog(interval))
    else:
        watchdog = None
        logger.info("Meeting safety watchdog disabled (poll interval <= 0)")
```

- [ ] **Step 3: Verify the module imports**

Run: `uv run python -c "import apps.server.main"`
Expected: no output, exit 0.

- [ ] **Step 4: Run the full server suite**

Run: `uv run pytest apps/server/tests -q`
Expected: PASS (all green; skips unchanged).

- [ ] **Step 5: Commit**

```bash
git add apps/server/main.py
git commit -m "feat(safety): re-stamp live sessions disconnected at startup"
```

---

## Task 9: Docs — env knob + ROADMAP checkbox

**Files:**
- Modify: `docs/ARCHITECTURE.md` (where `YESON_MEETING_SAFETY_POLL_SECONDS` / `YESON_MEETING_MAX_DURATION_HOURS` are documented)
- Modify: `docs/ROADMAP.md` (Slice 4 zombie-session lines ~199 / ~215)

- [ ] **Step 1: Document the new env knob**

In `docs/ARCHITECTURE.md`, the safety env knobs live in the §12.3 risk table at line ~591 (the `비용 폭주 (좀비 세션)` row). Add a new table row immediately after it documenting the disconnect path, matching the existing `| 케이스 | 영향 | 처리 |` column format:

```markdown
| 좀비 세션 (사이드카 끊김) | 비용/좀비 | 사이드카 WS 끊김 `YESON_MEETING_DISCONNECT_GRACE_SECONDS`(기본 300s·≤0 비활성) 초과 시 lifespan watchdog가 자동 종료 + operator alert + viewer `SessionEnded` 통지 (`disconnected_at` 컬럼 + WS lifecycle stamp/clear + 재시작 re-stamp) |
```

- [ ] **Step 2: Update the ROADMAP Slice 4 checkbox**

In `docs/ROADMAP.md`, update the "좀비 세션 자동 종료" line (~199) and the Slice-4 acceptance line (~215) to record that the disconnect scheduler has landed (code-complete; server-only; live E2E note), per the docs-after-slice rule. Keep the wall-clock note intact; add the disconnect path:

> `disconnect N분 scheduler`: `disconnected_at` 컬럼 + WS lifecycle stamp/clear + 공유 워치독 sweep(`enforce_sidecar_disconnect_limit`, env `YESON_MEETING_DISCONNECT_GRACE_SECONDS` 기본 300s) + 재시작 re-stamp. 단위/sweep 테스트 완료. 라이브 disconnect E2E는 수동 검증 남음.

- [ ] **Step 3: Commit**

```bash
git add docs/ARCHITECTURE.md docs/ROADMAP.md
git commit -m "docs(safety): document disconnect grace knob + ROADMAP Slice 4"
```

---

## Final verification

- [ ] **Run the full server test suite**

Run: `uv run pytest apps/server/tests -q`
Expected: all pass; skip count unchanged from baseline (`test_ws_sidecar_binary.py` stays skipped).

- [ ] **Confirm lint is clean on touched files**

Run: `uv run ruff check apps/server`
Expected: no errors.

- [ ] **Confirm the working tree has only intended changes**

Run: `git status --short`
Expected: only the prior-session leftovers (`PROJECT_CONTEXT.md`, `apps/desktop/scripts/vm_dump.py`, `bun.lock`) remain unstaged — do not commit those.
