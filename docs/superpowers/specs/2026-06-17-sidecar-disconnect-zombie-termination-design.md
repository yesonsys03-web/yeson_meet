# Sidecar Disconnect Zombie Termination (Meeting Safety scope B)

- Date: 2026-06-17
- Status: Approved (design)
- Branch: `topyeson`
- Related: ARCH §12.4 (Slice 4 회의 라이프사이클), ROADMAP Slice 4/5,
  `docs/superpowers/specs/2026-06-16-meeting-safety-scheduler-design.md` (scope A, wall-clock)

## Problem

When an operator closes the desktop app (or it crashes) without ending the
meeting, the sidecar WebSocket drops but the `session` row stays `status="live"`.
The shipped wall-clock watchdog (scope A) only force-ends a meeting once it
exceeds the max duration (default 3h), so a disconnected zombie keeps a `live`
row — and any Gemini cost it implies — for up to 3 hours.

ARCH §12.4 specifies the intended behaviour:

> 운영자가 종료 안 하고 앱 종료 → 좀비 세션 → **sidecar disconnect 감지 N분 후 자동 종료**

This spec covers **scope B: disconnect-based** zombie termination. The primary
trigger is the sidecar WebSocket connection lifecycle, detected entirely
server-side. No new sidecar heartbeat protocol is introduced.

## Goals

- Force-end a `live` meeting whose sidecar has been disconnected longer than a
  configurable grace period (default 5 min, per ARCH §12.4 / ROADMAP L215).
- Reuse the existing background watchdog and the same DB + injectable-`now`
  test pattern as scope A — deterministic, CI-independent (server-only).
- Survive a server restart: a meeting that was `live` at restart and never gets
  a sidecar reconnect is still force-ended after the grace period.

## Non-Goals (explicitly deferred)

- **Idle detection** (sidecar connected but silent for N min). Partially covered
  by the wall-clock timer (cost) and the no_audio advisory (UX). Out of scope.
- **Sidecar heartbeat frame** and **queue-flush contract** — deferred to Slice 5
  alongside the SQLite offline queue / heartbeat contract (ROADMAP L208, L215).
- **Multi-worker viewer notification gap** — `InMemoryBus` is per-process; a
  watchdog on worker A cannot publish `SessionEnded` to viewers on worker B.
  Pre-existing limitation, unchanged. DB state stays correct (enforcement is
  idempotent).
- No sidecar / Rust / desktop changes → no new Windows CI build required.

## Approach (chosen: A — DB `disconnected_at` column, event-driven)

Considered three mechanisms:

- **A) DB `disconnected_at` nullable column (event-driven)** — keyed purely on
  WS connect/disconnect events. Two DB writes per connection lifecycle. Faithful
  to the ARCH wording, reuses scope A's testable DB+injectable-`now` pattern,
  no sidecar change. **Chosen.**
- B) DB `last_sidecar_seen_at` (heartbeat-style throttled bump) — robust to
  restart but needs throttle logic and blurs the disconnect-only scope by also
  catching connected-but-silent sessions (idle, deferred). Rejected.
- C) In-memory only (no migration) — fragile across restart, monotonic-clock
  tests are non-deterministic and diverge from the existing pattern. Rejected.

A's only weakness — a hard crash skips the `finally` stamp — is closed by the
startup re-stamp (see §4) and backstopped by the already-shipped wall-clock
watchdog.

## Design

### 1. Data model + migration

Add to `Session` (`apps/server/db/models.py`, `MODELS_SESSION` anchor):

```python
disconnected_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True), nullable=True
)
```

Semantics:
- `NULL` → sidecar currently connected, **or** a freshly created session that
  has not yet seen a sidecar. Either way: not a disconnect candidate.
- non-`NULL` → the UTC instant the sidecar WS dropped.

New alembic migration `apps/server/db/alembic/versions/0002_session_disconnected_at.py`
(only `0001_initial.py` exists today):
- `upgrade`: `op.add_column("session", sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True))`
- `downgrade`: `op.drop_column("session", "disconnected_at")`
- `revision = "0002_session_disconnected_at"`, `down_revision = "0001_initial"`
  (confirmed against `0001_initial.py`).

### 2. WS lifecycle wiring (`apps/server/ws/sidecar.py`)

- **On accept** (inside the existing auth/resolve `async with AsyncSessionLocal()`
  block, where `device_id` is already committed): clear the stamp so a reconnect
  resets the grace clock —
  ```python
  if meeting.disconnected_at is not None:
      meeting.disconnected_at = None
      await db.commit()
  ```
- **On disconnect** (the handler's `finally` block, after `ai_session.stop()`):
  open a fresh `AsyncSessionLocal()`, re-read the session by `session_pk`, and
  **only if `status == "live"`** set `disconnected_at = datetime.now(timezone.utc)`
  and commit. If the operator already ended it (`status == "ended"`), leave it
  untouched. The stamp must not raise out of `finally` — wrap and log on failure.

### 3. Enforcement logic (`apps/server/ops/session_safety.py`)

Add **new** functions; **do not modify** the shipped, tested
`enforce_meeting_duration_limit` / `session_started_at_exceeds_max_duration`.

```python
DEFAULT_DISCONNECT_GRACE_SECONDS = 300.0
DISCONNECT_GRACE_ENV = "YESON_MEETING_DISCONNECT_GRACE_SECONDS"

def disconnect_grace() -> timedelta:
    """Grace before a disconnected live meeting is force-ended; ≤0 disables."""
    # parse env, malformed → default + warn, ≤0 → timedelta.max

def session_disconnect_exceeds_grace(
    disconnected_at: datetime, now: datetime | None = None
) -> bool:
    """True when a disconnected live meeting should be force-ended."""

async def enforce_sidecar_disconnect_limit(
    db: AsyncSession, meeting: Session, now: datetime | None = None
) -> bool:
    """Force-end a meeting whose sidecar has been gone past the grace period."""
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
        serialize(SessionEnded(
            session_id=meeting.external_id,
            occurred_at=ended_at,
            ended_at=ended_at,
        )),
    )
    return True
```

Idempotent (re-entry on an already-`ended` session is a no-op). Mirrors the
scope A function's structure deliberately so the two read identically. The
`SessionEnded` publish block is duplicated rather than refactored, to keep the
shipped scope A path byte-for-byte unchanged (safety-critical code).

New alert in `apps/server/ops/alerts.py`, sibling to
`raise_meeting_max_duration_alert`:

```python
def raise_meeting_disconnect_alert(session_id: str) -> None:
    """Operator alert: a live meeting was auto-ended after its sidecar stayed
    disconnected past the grace period."""
```

A distinct alert kind (not the max-duration one) so the operator can tell *why*
the meeting ended. Follow the existing `OperatorAlertStore` raise pattern.

### 4. Watchdog integration (`apps/server/ops/session_safety_scheduler.py`)

- Extend the existing `_sweep_once` live-session loop to also apply
  `enforce_sidecar_disconnect_limit` to each `live` meeting, in the **same loop
  and the same watchdog task** (no second background task). Count both kinds of
  termination. The 60s default poll resolution is fine against a 5-min grace.
  Wall-clock and disconnect enforcement are both idempotent and independent;
  whichever condition trips first ends the session.
- **Startup re-stamp (hardening, approved):** in `apps/server/main.py` lifespan,
  immediately before `create_task(run_meeting_safety_watchdog(...))`, run a
  one-shot UPDATE stamping `disconnected_at = now()` for every session where
  `status = 'live' AND disconnected_at IS NULL`. On a fresh boot nothing is
  connected yet, so this makes post-restart zombies eligible for termination
  after the grace period. A genuinely-live sidecar reconnects within its 1→30s
  backoff and clears the stamp well inside the 5-min grace, so no live meeting is
  wrongly ended. Implement as a small helper (e.g. `stamp_live_sessions_disconnected(session_factory)`)
  in the scheduler module so it is unit-testable. Gate it on the watchdog being
  enabled (poll interval > 0), consistent with the existing wiring.

### 5. Testing (pytest only — CI-independent)

- `apps/server/tests/test_session_safety.py`: `disconnect_grace()` env parsing
  (default / explicit / ≤0 disabled / malformed); `session_disconnect_exceeds_grace`
  boundary; `enforce_sidecar_disconnect_limit` four paths — already-`ended`
  (no-op), `disconnected_at is None` (connected → False), within grace (→ False),
  past grace (→ ends, raises disconnect alert, publishes `SessionEnded`).
- `apps/server/tests/test_session_safety_scheduler.py`: `_sweep_once` ends a
  past-grace disconnected session, ignores a connected (`NULL`) and a
  within-grace one, and coexists with wall-clock enforcement in one sweep;
  `stamp_live_sessions_disconnected` stamps only `live AND NULL` rows.
- `apps/server/tests/test_ws_sidecar_binary.py` (or a new
  `test_ws_sidecar_disconnect.py`): on WS disconnect the session's
  `disconnected_at` is stamped; on a subsequent accept (reconnect) it is cleared;
  an operator-ended session is not re-stamped on disconnect.

### 6. Config & docs

- New env knob `YESON_MEETING_DISCONNECT_GRACE_SECONDS` (default 300, ≤0
  disables). Document alongside `YESON_MEETING_SAFETY_POLL_SECONDS` and
  `YESON_MEETING_MAX_DURATION_HOURS` in ARCHITECTURE.md.
- Update ROADMAP Slice 4 zombie-session checkbox (L199 / L215) to reflect the
  disconnect scheduler landing (code-complete; live E2E note), per the
  docs-after-slice rule.

## Open questions

None. (Grace default = 5 min from ARCH §12.4; N-min reconnect reset is implicit
in the clear-on-accept behaviour.)
