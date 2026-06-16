# Meeting Safety Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Force-end over-duration live meetings even when no audio flows, via a background watchdog, and notify viewers consistently on every force-end.

**Architecture:** Add a `SessionEnded` bus publish into the shared `enforce_meeting_duration_limit` so both the audio-ingress path and the new scheduler notify viewers identically. Add a new `session_safety_scheduler` module with a pure-ish sweep (`_sweep_once`) and a long-running loop (`run_meeting_safety_watchdog`), injecting the DB `session_factory` for testability. Wire the loop into the FastAPI `lifespan` startup/shutdown.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async (asyncpg), pytest + pytest-asyncio, Postgres test DB (conftest-managed).

---

## File Structure

- Modify: `apps/server/ops/session_safety.py` — add viewer (`SessionEnded`) bus publish to `enforce_meeting_duration_limit`.
- Create: `apps/server/ops/session_safety_scheduler.py` — `safety_poll_interval()`, `_sweep_once(session_factory)`, `run_meeting_safety_watchdog(interval_seconds, *, session_factory)`.
- Modify: `apps/server/main.py` — start watchdog task in `lifespan`, cancel on shutdown.
- Modify: `apps/server/tests/test_session_safety.py` — add bus-publish test.
- Create: `apps/server/tests/test_session_safety_scheduler.py` — sweep + loop tests.

**Design refinement vs spec:** the spec showed `_sweep_once() -> int` with no params. The plan injects `session_factory` into `_sweep_once`/`run_meeting_safety_watchdog` (default `AsyncSessionLocal`). Reason: conftest deliberately builds a fresh async engine per test to avoid event-loop reuse on the module-level engine (conftest fixture 7), so tests must pass their own factory. Production behavior is unchanged (default is the production sessionmaker).

**Test DB note:** all tests use the real Postgres test DB via the `db_session` fixture. `enforce`/`_sweep_once` open their own session, so seeded rows MUST be committed (not just flushed) to be visible across sessions. Tests build the injected factory from `db_session.bind` so it shares the per-test engine and event loop.

---

### Task 1: Viewer notification in shared enforce

**Files:**
- Modify: `apps/server/ops/session_safety.py`
- Test: `apps/server/tests/test_session_safety.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/server/tests/test_session_safety.py`:

```python
async def test_enforce_meeting_duration_limit_publishes_session_ended(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.server.ws.bus import bus

    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(hours=3, minutes=1))

    queue = bus.subscribe(meeting.external_id)
    try:
        enforced = await enforce_meeting_duration_limit(db_session, meeting, now=now)
        assert enforced is True
        payload = queue.get_nowait()
    finally:
        bus.unsubscribe(meeting.external_id, queue)

    assert payload["type"] == "session.ended"
    assert payload["session_id"] == str(meeting.external_id)


async def test_enforce_meeting_duration_limit_active_session_does_not_publish(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.server.ws.bus import bus

    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    now = datetime.now(timezone.utc)
    meeting = await _create_meeting(db_session, now - timedelta(hours=2))

    queue = bus.subscribe(meeting.external_id)
    try:
        enforced = await enforce_meeting_duration_limit(db_session, meeting, now=now)
        assert enforced is False
        assert queue.empty()
    finally:
        bus.unsubscribe(meeting.external_id, queue)
```

Note: both new tests need `@pytest.mark.asyncio` like the existing ones — add the decorator above each.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/server/tests/test_session_safety.py -k publishes_session_ended -q`
Expected: FAIL — `queue.get_nowait()` raises `asyncio.QueueEmpty` (enforce does not publish yet).

- [ ] **Step 3: Write minimal implementation**

In `apps/server/ops/session_safety.py`, add imports near the top (after the existing `from apps.server.ops.alerts import raise_meeting_max_duration_alert`):

```python
from apps.server.domain.events import SessionEnded, serialize
from apps.server.ws.bus import bus
```

Inside `enforce_meeting_duration_limit`, after `raise_meeting_max_duration_alert(str(meeting.external_id))` and before `return True`, add:

```python
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
```

The function body now reads:

```python
    meeting.status = "ended"
    meeting.ended_at = ended_at
    await db.commit()
    raise_meeting_max_duration_alert(str(meeting.external_id))
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

Run: `uv run pytest apps/server/tests/test_session_safety.py -q`
Expected: PASS (all existing + 2 new tests).

- [ ] **Step 5: Commit**

```bash
git add apps/server/ops/session_safety.py apps/server/tests/test_session_safety.py
git commit -m "feat(safety): publish SessionEnded to viewers on force-end"
```

---

### Task 2: Sweep + poll-interval config

**Files:**
- Create: `apps/server/ops/session_safety_scheduler.py`
- Test: `apps/server/tests/test_session_safety_scheduler.py`

- [ ] **Step 1: Write the failing test**

Create `apps/server/tests/test_session_safety_scheduler.py`:

```python
"""Background meeting-safety watchdog tests."""
from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.server.auth.password import hash_password
from apps.server.db.models import AppUser, Session
from apps.server.ops.alerts import operator_alerts
from apps.server.ops.session_safety_scheduler import (
    _sweep_once,
    run_meeting_safety_watchdog,
    safety_poll_interval,
)


@pytest.fixture(autouse=True)
def reset_operator_alerts() -> Generator[None]:
    operator_alerts.reset()
    yield
    operator_alerts.reset()


async def _create_live_meeting(db_session: AsyncSession, started_at: datetime) -> Session:
    admin = AppUser(
        email=f"sched-{uuid4()}@test.example",
        name="Scheduler Test",
        password_hash=hash_password("pw"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    await db_session.flush()
    meeting = Session(
        external_id=uuid4(),
        owner_user_id=admin.id,
        title="Scheduler Max Duration",
        status="live",
        started_at=started_at,
    )
    db_session.add(meeting)
    await db_session.commit()
    return meeting


def _factory(db_session: AsyncSession) -> async_sessionmaker:
    return async_sessionmaker(
        db_session.bind, expire_on_commit=False, class_=AsyncSession
    )


def test_safety_poll_interval_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YESON_MEETING_SAFETY_POLL_SECONDS", raising=False)
    assert safety_poll_interval() == 60.0


def test_safety_poll_interval_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YESON_MEETING_SAFETY_POLL_SECONDS", "5")
    assert safety_poll_interval() == 5.0


@pytest.mark.asyncio
async def test_sweep_ends_overdue_session(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    now = datetime.now(timezone.utc)
    meeting = await _create_live_meeting(db_session, now - timedelta(hours=3, minutes=1))

    ended = await _sweep_once(_factory(db_session))

    assert ended == 1
    async with _factory(db_session)() as db2:
        refreshed = (
            await db2.execute(select(Session).where(Session.id == meeting.id))
        ).scalar_one()
    assert refreshed.status == "ended"
    assert len(operator_alerts.active()) == 1


@pytest.mark.asyncio
async def test_sweep_keeps_fresh_session_live(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    now = datetime.now(timezone.utc)
    meeting = await _create_live_meeting(db_session, now - timedelta(hours=1))

    ended = await _sweep_once(_factory(db_session))

    assert ended == 0
    async with _factory(db_session)() as db2:
        refreshed = (
            await db2.execute(select(Session).where(Session.id == meeting.id))
        ).scalar_one()
    assert refreshed.status == "live"
    assert operator_alerts.active() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/server/tests/test_session_safety_scheduler.py -q`
Expected: FAIL at import — `ModuleNotFoundError: apps.server.ops.session_safety_scheduler`.

- [ ] **Step 3: Write minimal implementation**

Create `apps/server/ops/session_safety_scheduler.py`:

```python
# === ANCHOR: SESSION_SAFETY_SCHEDULER_START ===
"""Background watchdog that force-ends over-duration live meetings.

The ingress enforcement in ``apps/server/ws/sidecar.py`` only fires while audio
chunks flow. A zombie session (sidecar silent or hung) is never re-checked, so
Gemini cost could accrue indefinitely. This watchdog polls live sessions on a
fixed interval and applies the same ``enforce_meeting_duration_limit``, closing
the gap.

Single-process assumption: ``InMemoryBus`` is per-process. Under multiple workers
each worker would run its own watchdog; ``enforce_meeting_duration_limit`` is
idempotent so DB state stays correct, but viewers on other workers would miss the
``SessionEnded`` publish — a pre-existing multi-worker limitation, out of scope.
"""
from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.server.db.models import Session
from apps.server.db.session import AsyncSessionLocal
from apps.server.ops.session_safety import enforce_meeting_duration_limit

logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 60.0
POLL_SECONDS_ENV = "YESON_MEETING_SAFETY_POLL_SECONDS"


# === ANCHOR: SESSION_SAFETY_SCHEDULER_POLL_INTERVAL_START ===
def safety_poll_interval() -> float:
    """Watchdog poll interval in seconds; non-positive disables the watchdog."""
    return float(os.environ.get(POLL_SECONDS_ENV, str(DEFAULT_POLL_SECONDS)))
# === ANCHOR: SESSION_SAFETY_SCHEDULER_POLL_INTERVAL_END ===


# === ANCHOR: SESSION_SAFETY_SCHEDULER_SWEEP_ONCE_START ===
async def _sweep_once(session_factory: async_sessionmaker) -> int:
    """Force-end every over-duration live meeting; return how many were ended."""
    ended = 0
    async with session_factory() as db:
        live = (
            await db.execute(select(Session).where(Session.status == "live"))
        ).scalars().all()
        for meeting in live:
            if await enforce_meeting_duration_limit(db, meeting):
                ended += 1
    return ended
# === ANCHOR: SESSION_SAFETY_SCHEDULER_SWEEP_ONCE_END ===


# === ANCHOR: SESSION_SAFETY_SCHEDULER_RUN_WATCHDOG_START ===
async def run_meeting_safety_watchdog(
    interval_seconds: float,
    *,
    session_factory: async_sessionmaker = AsyncSessionLocal,
) -> None:
    """Poll live meetings forever, force-ending over-duration ones each cycle."""
    logger.info(
        "Meeting safety watchdog started", extra={"interval_seconds": interval_seconds}
    )
    while True:
        try:
            ended = await _sweep_once(session_factory)
            if ended:
                logger.info(
                    "Meeting safety watchdog ended sessions", extra={"count": ended}
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Meeting safety watchdog sweep failed")
        await asyncio.sleep(interval_seconds)
# === ANCHOR: SESSION_SAFETY_SCHEDULER_RUN_WATCHDOG_END ===
# === ANCHOR: SESSION_SAFETY_SCHEDULER_END ===
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/server/tests/test_session_safety_scheduler.py -q`
Expected: PASS (4 tests: 2 interval, 2 sweep). `run_meeting_safety_watchdog` is imported but not yet exercised — covered in Task 3.

- [ ] **Step 5: Commit**

```bash
git add apps/server/ops/session_safety_scheduler.py apps/server/tests/test_session_safety_scheduler.py
git commit -m "feat(safety): add meeting-safety sweep + poll-interval config"
```

---

### Task 3: Watchdog loop behavior

**Files:**
- Test: `apps/server/tests/test_session_safety_scheduler.py` (append)

This task adds no production code — `run_meeting_safety_watchdog` already exists from Task 2. It locks in loop behavior: a real sweep happens and cancellation is clean.

- [ ] **Step 1: Write the failing test**

Append to `apps/server/tests/test_session_safety_scheduler.py`:

```python
@pytest.mark.asyncio
async def test_watchdog_sweeps_then_cancels_cleanly(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESON_MEETING_MAX_DURATION_HOURS", "3")
    now = datetime.now(timezone.utc)
    meeting = await _create_live_meeting(db_session, now - timedelta(hours=3, minutes=1))

    task = asyncio.create_task(
        run_meeting_safety_watchdog(0.01, session_factory=_factory(db_session))
    )
    # Give the loop time to run at least one sweep.
    for _ in range(50):
        await asyncio.sleep(0.01)
        async with _factory(db_session)() as db2:
            refreshed = (
                await db2.execute(select(Session).where(Session.id == meeting.id))
            ).scalar_one()
        if refreshed.status == "ended":
            break

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert refreshed.status == "ended"
    assert len(operator_alerts.active()) == 1
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest apps/server/tests/test_session_safety_scheduler.py::test_watchdog_sweeps_then_cancels_cleanly -q`
Expected: PASS. (This is a behavior-lock test against existing code; it should pass immediately. If it fails, the bug is real — fix before committing.)

- [ ] **Step 3: Commit**

```bash
git add apps/server/tests/test_session_safety_scheduler.py
git commit -m "test(safety): lock watchdog loop sweep + clean cancellation"
```

---

### Task 4: Wire watchdog into FastAPI lifespan

**Files:**
- Modify: `apps/server/main.py`

- [ ] **Step 1: Update imports**

In `apps/server/main.py`, change the existing line:

```python
from contextlib import asynccontextmanager
```

to:

```python
import asyncio
from contextlib import asynccontextmanager, suppress
```

Add, alongside the other `apps.server` imports (after `from apps.server.ops.alerts import sync_gemini_config_alert`):

```python
from apps.server.ops.session_safety_scheduler import (
    run_meeting_safety_watchdog,
    safety_poll_interval,
)
```

- [ ] **Step 2: Start/stop the watchdog in lifespan**

Replace the body of `lifespan` from the existing `yield` line. The current end is:

```python
    else:
        logger.warning("Gemini Live disabled: GEMINI_API_KEY is not configured")
    yield
```

Change to:

```python
    else:
        logger.warning("Gemini Live disabled: GEMINI_API_KEY is not configured")

    interval = safety_poll_interval()
    if interval > 0:
        watchdog = asyncio.create_task(run_meeting_safety_watchdog(interval))
    else:
        watchdog = None
        logger.info("Meeting safety watchdog disabled (poll interval <= 0)")
    try:
        yield
    finally:
        if watchdog is not None:
            watchdog.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog
```

- [ ] **Step 3: Verify the server module imports cleanly**

Run: `uv run python -c "import apps.server.main"`
Expected: no output, exit 0 (no import errors, no circular import).

- [ ] **Step 4: Run the full server test suite (no regressions; lifespan not run by ASGITransport)**

Run: `uv run pytest apps/server/tests -q`
Expected: PASS. (The `client` fixture uses `ASGITransport` which does not run lifespan, so the watchdog does not start during tests — existing tests are unaffected.)

- [ ] **Step 5: Commit**

```bash
git add apps/server/main.py
git commit -m "feat(safety): start meeting-safety watchdog in server lifespan"
```

---

### Task 5: Docs + final verification

**Files:**
- Modify: `docs/ROADMAP.md`
- Modify: `docs/superpowers/plans/2026-06-16-meeting-safety-scheduler.md` (check off completed steps)

- [ ] **Step 1: Locate the roadmap slice**

Run: `grep -n "안전 타이머\|safety\|scheduler\|Slice 3\|max.duration\|좀비" docs/ROADMAP.md`
Expected: find the meeting-safety / Slice 3 line that currently notes the scheduler/3h E2E as incomplete.

- [ ] **Step 2: Update the roadmap**

Mark the background-scheduler item as code-complete (per `feedback_docs_after_slice`: update ROADMAP/PRD checkboxes in the same slice). Note the wall-clock scheduler + viewer-notification parity is implemented; the live 3h E2E remains a manual/operator check. Keep wording consistent with sibling entries.

- [ ] **Step 3: Full verification sweep**

Run: `uv run pytest apps/server/tests -q`
Expected: PASS (all server tests, including the new safety + scheduler tests).

Run: `uv run ruff check apps/server/ops/session_safety_scheduler.py apps/server/ops/session_safety.py apps/server/main.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add docs/ROADMAP.md docs/superpowers/plans/2026-06-16-meeting-safety-scheduler.md
git commit -m "docs(safety): mark meeting-safety scheduler code-complete"
```

---

## Notes for the implementer

- **Idempotency:** `enforce_meeting_duration_limit` returns `False` when `status == "ended"`, so re-sweeping an already-ended session is a no-op (no duplicate alert, no duplicate publish).
- **No Rust/sidecar/desktop changes:** this is server-only. Do not touch `apps/client_sidecar`, `apps/native_helper_*`, or `apps/desktop`.
- **Env knobs:** `YESON_MEETING_MAX_DURATION_HOURS` (existing, default 3h, `<=0` disables limit), `YESON_MEETING_SAFETY_POLL_SECONDS` (new, default 60s, `<=0` disables watchdog).
- **Anchor discipline (CLAUDE.md):** edits to `session_safety.py` and `main.py` stay inside existing anchor spans; the new module defines its own anchors as shown.
