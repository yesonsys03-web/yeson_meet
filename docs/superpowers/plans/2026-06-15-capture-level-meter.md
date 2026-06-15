# dBFS Capture Level Meter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 1 Hz segmented loudness meter beside the capture-status chip, fed by a dedicated `capture-level` Tauri event so the diagnostic app-log stays clean.

**Architecture:** The sidecar already computes per-chunk dBFS (`pcm16_dbfs`, slice 2). The capture-status watchdog (1 s poll) gains a rolling 1 s mean and emits a `CAPTURE_LEVEL <dbfs>` stdout line each tick. The Rust forwarder routes those lines to a dedicated `capture-level` event (not the app-log). The desktop subscribes and renders a 6-segment bar that shows only in `active`/`silent` states.

**Tech Stack:** Python (asyncio sidecar, pytest auto-asyncio), Rust (Tauri `Emitter`, serde), React/TypeScript (vitest).

---

## File Structure

**Modify:**
- `apps/client_sidecar/transport/capture_status.py` — rolling level buffer, `level()`, `level_marker()`, watchdog emits full marker lines (anchor `CAPTURE_STATUS`).
- `apps/client_sidecar/main.py` — emit callback prints the full line; drop now-unused `MARKER` import (anchor `MAIN_AUDIO_MAIN`).
- `apps/desktop/src-tauri/src/sidecar.rs` — `CaptureLevelEvent` struct + forwarder branch routing `CAPTURE_LEVEL` to `capture-level` (anchor `SIDECAR`).
- `apps/desktop/src/console/LiveSubtitlePreview.tsx` — wire `useCaptureLevel` + render meter in header (anchor `LIVE_SUBTITLE_PREVIEW`).

**Create:**
- `apps/desktop/src/console/captureLevel.ts` — pure dBFS→segment mapping + `useCaptureLevel` hook (new anchor `CAPTURE_LEVEL`).
- `apps/desktop/src/console/CaptureLevelMeter.tsx` — segmented bar component (new anchor `CAPTURE_LEVEL_METER`).
- `apps/desktop/src/console/captureLevel.test.ts` — vitest for the pure mappers.

**Test (existing, extend):**
- `apps/client_sidecar/tests/test_capture_status.py` — level + watchdog tests.

**Commands:**
- Sidecar tests: `uv run pytest apps/client_sidecar/tests -q` (root `testpaths` only covers server, so pass the path explicitly; `asyncio_mode = "auto"` is set so `async def test_*` works directly).
- Desktop tests: `pnpm --filter @yeson-meet/desktop test`
- Desktop types: `pnpm --filter @yeson-meet/desktop exec tsc --noEmit`
- Rust check: `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml` (if it fails at the build-script externalBin stage on a clean checkout, that is the pre-existing env quirk noted in project memory, unrelated to this change).

---

## Task 1: Sidecar reporter rolling level + `level_marker`

**Files:**
- Modify: `apps/client_sidecar/transport/capture_status.py`
- Test: `apps/client_sidecar/tests/test_capture_status.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/client_sidecar/tests/test_capture_status.py`. First extend the import at the top of the file to add the new names:

```python
from apps.client_sidecar.transport.capture_status import (
    ACTIVE,
    CONNECTING,
    SILENT,
    TRANSPORT_DOWN,
    CaptureStatusReporter,
    compute_state,
    level_marker,
)
```

Then append these tests at the end of the file:

```python
def test_level_none_before_any_chunk():
    r = CaptureStatusReporter(threshold=T)
    assert r.level(now=1.0) is None


def test_level_mean_of_recent_chunks():
    r = CaptureStatusReporter(threshold=T)
    r.note_chunk(now=100.0, dbfs=-20.0)
    r.note_chunk(now=100.5, dbfs=-30.0)
    assert r.level(now=100.6) == -25.0  # mean of chunks within the 1s window


def test_level_excludes_chunks_outside_window():
    r = CaptureStatusReporter(threshold=T)
    r.note_chunk(now=100.0, dbfs=-60.0)  # >1s before now=101.2 → excluded
    r.note_chunk(now=101.0, dbfs=-20.0)
    assert r.level(now=101.2) == -20.0


def test_level_none_when_stale():
    r = CaptureStatusReporter(threshold=T)
    r.note_chunk(now=100.0, dbfs=-20.0)
    assert r.level(now=102.0) is None  # >1.5s since last chunk → no signal


def test_level_marker_format():
    assert level_marker(-28.37) == "CAPTURE_LEVEL -28.4"
    assert level_marker(-6.0) == "CAPTURE_LEVEL -6.0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/client_sidecar/tests/test_capture_status.py -q`
Expected: FAIL — `ImportError: cannot import name 'level_marker'` (and `AttributeError: ... 'level'` once import is fixed).

- [ ] **Step 3: Implement in `capture_status.py`**

Add `deque` to the stdlib imports near the top:

```python
import asyncio
import time
from collections import deque
from collections.abc import Callable
```

Add level constants directly below the existing `RMS_SILENCE_DBFS = -45.0` line:

```python
LEVEL_MARKER = "CAPTURE_LEVEL "
LEVEL_WINDOW_S = 1.0  # rolling mean window for the meter
LEVEL_STALE_S = 1.5   # no chunk within this → no signal (None)
```

In `CaptureStatusReporter.__init__`, add the buffer after `self._last_loud_at`:

```python
        self._last_loud_at: float | None = None
        self._levels: deque[tuple[float, float]] = deque(maxlen=200)
        self._emitted: str | None = None
```

In `note_chunk`, append the sample:

```python
    def note_chunk(self, now: float, dbfs: float) -> None:
        self._last_chunk_at = now
        if dbfs >= self._rms_threshold:
            self._last_loud_at = now
        self._levels.append((now, dbfs))
```

Add the `level` method after `poll` (and before `run_watchdog`):

```python
    def level(self, now: float) -> float | None:
        """Mean dBFS over the last LEVEL_WINDOW_S, or None if no recent chunk.

        Independent of the silence state machine — this feeds the live meter.
        Returns None when the stream is stale (Windows silence = no packets) so
        the desktop never shows a frozen level."""
        if self._last_chunk_at is None or now - self._last_chunk_at > LEVEL_STALE_S:
            return None
        cutoff = now - LEVEL_WINDOW_S
        recent = [d for (t, d) in self._levels if t >= cutoff]
        if not recent:
            return None
        return sum(recent) / len(recent)
```

Add the pure helper at module scope, right after the `level` method's class (place it just before `async def run_watchdog`):

```python
def level_marker(dbfs: float) -> str:
    """Canonical CAPTURE_LEVEL stdout line for one meter sample (1 decimal)."""
    return f"{LEVEL_MARKER}{dbfs:.1f}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/client_sidecar/tests/test_capture_status.py -q`
Expected: PASS (all existing + 5 new).

- [ ] **Step 5: Commit**

```bash
git add apps/client_sidecar/transport/capture_status.py apps/client_sidecar/tests/test_capture_status.py
git commit -m "feat(sidecar): rolling 1s dBFS level + CAPTURE_LEVEL marker helper"
```

---

## Task 2: Watchdog emits full marker lines (state + level)

**Files:**
- Modify: `apps/client_sidecar/transport/capture_status.py` (`run_watchdog`)
- Modify: `apps/client_sidecar/main.py` (`audio_main`)
- Test: `apps/client_sidecar/tests/test_capture_status.py`

- [ ] **Step 1: Write the failing test**

Add `import asyncio` to the top of `test_capture_status.py` (above the `from apps...` import), then add `run_watchdog` to the import list:

```python
import asyncio

from apps.client_sidecar.transport.capture_status import (
    ACTIVE,
    CONNECTING,
    SILENT,
    TRANSPORT_DOWN,
    CaptureStatusReporter,
    compute_state,
    level_marker,
    run_watchdog,
)
```

Append this async test (auto-asyncio mode is enabled, so no decorator needed):

```python
async def test_watchdog_emits_full_state_and_level_lines():
    emitted: list[str] = []
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0, dbfs=LOUD)

    task = asyncio.create_task(
        run_watchdog(r, emitted.append, interval=0.001, now_fn=lambda: 100.0)
    )
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # State line is fully formed (MARKER prepended by the watchdog, not the caller)
    assert "CAPTURE_STATUS active" in emitted
    # Level telemetry emitted every tick while the stream is live
    assert any(m.startswith("CAPTURE_LEVEL ") for m in emitted)


async def test_watchdog_skips_level_when_no_chunk():
    emitted: list[str] = []
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)  # connected but no chunk yet → connecting, no level

    task = asyncio.create_task(
        run_watchdog(r, emitted.append, interval=0.001, now_fn=lambda: 100.0)
    )
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert "CAPTURE_STATUS connecting" in emitted
    assert not any(m.startswith("CAPTURE_LEVEL ") for m in emitted)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/client_sidecar/tests/test_capture_status.py::test_watchdog_emits_full_state_and_level_lines -q`
Expected: FAIL — emitted contains bare `"active"` (not `"CAPTURE_STATUS active"`) and no `CAPTURE_LEVEL` lines.

- [ ] **Step 3: Rewrite `run_watchdog`**

Replace the existing `run_watchdog` body in `capture_status.py` with:

```python
async def run_watchdog(
    reporter: CaptureStatusReporter,
    emit: Callable[[str], None],
    *,
    interval: float = 1.0,
    now_fn: Callable[[], float] = time.monotonic,
) -> None:
    """Poll every `interval`s. On each state transition emit a full
    `CAPTURE_STATUS <state>` line; every tick the stream is live emit a
    `CAPTURE_LEVEL <dbfs>` line. Owning the full marker text here keeps marker
    formatting in one module (the caller just prints whatever it receives).
    Runs standalone so silence is detected even while the send loop blocks."""
    while True:
        await asyncio.sleep(interval)
        now = now_fn()
        state = reporter.poll(now)
        if state is not None:
            emit(f"{MARKER}{state}")
        level = reporter.level(now)
        if level is not None:
            emit(level_marker(level))
```

- [ ] **Step 4: Update `main.py` caller**

In `apps/client_sidecar/main.py`, the import block in `audio_main` currently imports `MARKER`. Change it to drop `MARKER`:

```python
    from apps.client_sidecar.transport.capture_status import (
        CaptureStatusReporter,
        run_watchdog,
    )
```

And change the watchdog creation so the callback just prints the already-formed line:

```python
    reporter = CaptureStatusReporter(rms_threshold_dbfs=RMS_DBFS_THRESHOLD)
    watchdog = asyncio.create_task(
        run_watchdog(reporter, lambda line: print(line, flush=True))
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest apps/client_sidecar/tests/test_capture_status.py -q`
Expected: PASS (existing + 2 new async tests).

Also confirm `main.py` still imports cleanly:
Run: `uv run python -c "import ast; ast.parse(open('apps/client_sidecar/main.py').read())"`
Expected: no output (syntax OK).

- [ ] **Step 6: Commit**

```bash
git add apps/client_sidecar/transport/capture_status.py apps/client_sidecar/main.py apps/client_sidecar/tests/test_capture_status.py
git commit -m "feat(sidecar): watchdog emits CAPTURE_LEVEL telemetry + full marker lines"
```

---

## Task 3: Rust forwarder routes `CAPTURE_LEVEL` to a dedicated event

**Files:**
- Modify: `apps/desktop/src-tauri/src/sidecar.rs`

(No Rust unit-test harness in this crate; verified by `cargo check` + downstream E2E.)

- [ ] **Step 1: Add the event struct**

In `apps/desktop/src-tauri/src/sidecar.rs`, directly after the `BackendLogEvent` struct (ends at the line with the closing `}` near line 148), add:

```rust
#[derive(Clone, Debug, Serialize)]
struct CaptureLevelEvent {
    dbfs: f32,
}
```

- [ ] **Step 2: Add the forwarder branch**

In `spawn_output_forwarder`, inside the `Ok(_) =>` arm, after `let message = String::from_utf8_lossy(&buf).into_owned();` and before `let inferred_level = ...`, insert:

```rust
                    // Capture-level telemetry (~1/s) goes to a dedicated event,
                    // NOT the app-log, so the diagnostic log stays readable.
                    if let Some(rest) = message.strip_prefix("CAPTURE_LEVEL ") {
                        if let Ok(dbfs) = rest.trim().parse::<f32>() {
                            let _ = app.emit("capture-level", CaptureLevelEvent { dbfs });
                            continue;
                        }
                    }
```

The resulting arm reads:

```rust
                Ok(_) => {
                    while matches!(buf.last(), Some(b'\n') | Some(b'\r')) {
                        buf.pop();
                    }
                    let message = String::from_utf8_lossy(&buf).into_owned();
                    if let Some(rest) = message.strip_prefix("CAPTURE_LEVEL ") {
                        if let Ok(dbfs) = rest.trim().parse::<f32>() {
                            let _ = app.emit("capture-level", CaptureLevelEvent { dbfs });
                            continue;
                        }
                    }
                    let inferred_level = infer_sidecar_log_level(&message).unwrap_or(level);
                    emit_backend_log(&app, inferred_level, source, message);
                }
```

- [ ] **Step 3: Type-check the Rust crate**

Run: `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml`
Expected: PASS (`Finished` with no errors). `app.emit` resolves via the already-imported `tauri::Emitter`; `Serialize` via the already-imported `serde::Serialize`.
(If it fails only at a build-script/externalBin step about a missing bundled helper binary, that is the pre-existing environment quirk from project memory — the type check of this file is what matters; note it and proceed.)

- [ ] **Step 4: Commit**

```bash
git add apps/desktop/src-tauri/src/sidecar.rs
git commit -m "feat(desktop): route CAPTURE_LEVEL to a dedicated capture-level event"
```

---

## Task 4: Desktop pure level mapping + hook (`captureLevel.ts`)

**Files:**
- Create: `apps/desktop/src/console/captureLevel.ts`
- Test: `apps/desktop/src/console/captureLevel.test.ts`

- [ ] **Step 1: Write the failing test**

Create `apps/desktop/src/console/captureLevel.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import { SEGMENTS, dbfsToSegments, segmentEdgeDbfs } from "./captureLevel";

describe("dbfsToSegments", () => {
  it("is empty at or below the floor", () => {
    expect(dbfsToSegments(-54)).toBe(0);
    expect(dbfsToSegments(-80)).toBe(0);
  });

  it("is full at or above the ceiling", () => {
    expect(dbfsToSegments(-6)).toBe(SEGMENTS);
    expect(dbfsToSegments(0)).toBe(SEGMENTS);
  });

  it("maps the mid-range proportionally", () => {
    // (-30 - -54) / (-6 - -54) = 24/48 = 0.5 → 3 of 6
    expect(dbfsToSegments(-30)).toBe(3);
  });

  it("treats non-finite input as empty", () => {
    expect(dbfsToSegments(Number.NEGATIVE_INFINITY)).toBe(0);
    expect(dbfsToSegments(Number.NaN)).toBe(0);
  });
});

describe("segmentEdgeDbfs", () => {
  it("top segment edge is the ceiling", () => {
    expect(segmentEdgeDbfs(SEGMENTS - 1)).toBeCloseTo(-6);
  });

  it("first segment edge is one step above the floor", () => {
    // -54 + (1/6)*48 = -46
    expect(segmentEdgeDbfs(0)).toBeCloseTo(-46);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --filter @yeson-meet/desktop test`
Expected: FAIL — cannot resolve `./captureLevel`.

- [ ] **Step 3: Create `captureLevel.ts`**

```typescript
// === ANCHOR: CAPTURE_LEVEL_START ===
import { useEffect, useState } from "react";

import { listen } from "@tauri-apps/api/event";

// The sidecar emits `CAPTURE_LEVEL <dbfs>` ~1×/s. The Rust forwarder routes it
// to a dedicated `capture-level` Tauri event (NOT the app log) so the diagnostic
// log stays clean. We map dBFS → meter segments for the live loudness bar.

export const SEGMENTS = 6;
export const LEVEL_FLOOR_DBFS = -54; // empty bar at/below
export const LEVEL_CEIL_DBFS = -6; // full bar at/above
export const WARN_DBFS = -12; // a lit segment whose edge exceeds this → yellow
export const CLIP_DBFS = -6; // a lit segment whose edge exceeds this → red

/** Map a dBFS value to a count of filled segments in [0, segments]. */
export function dbfsToSegments(dbfs: number, segments: number = SEGMENTS): number {
  if (!Number.isFinite(dbfs)) return 0;
  const span = LEVEL_CEIL_DBFS - LEVEL_FLOOR_DBFS;
  const filled = Math.round(((dbfs - LEVEL_FLOOR_DBFS) / span) * segments);
  return Math.max(0, Math.min(segments, filled));
}

/** The dBFS at the top edge of segment `index` (0-based). Used for coloring. */
export function segmentEdgeDbfs(index: number, segments: number = SEGMENTS): number {
  const span = LEVEL_CEIL_DBFS - LEVEL_FLOOR_DBFS;
  return LEVEL_FLOOR_DBFS + ((index + 1) / segments) * span;
}

type CaptureLevelPayload = { dbfs: number };
type TauriWindow = Window & { __TAURI_INTERNALS__?: unknown };

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

/** Latest dBFS from the `capture-level` event, or null (no signal / non-Tauri). */
export function useCaptureLevel(): number | null {
  const [dbfs, setDbfs] = useState<number | null>(null);
  useEffect(() => {
    if (!hasTauriRuntime()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void listen<CaptureLevelPayload>("capture-level", (event) => {
      setDbfs(event.payload.dbfs);
    }).then((fn) => {
      if (cancelled) fn();
      else unlisten = fn;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);
  return dbfs;
}
// === ANCHOR: CAPTURE_LEVEL_END ===
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm --filter @yeson-meet/desktop test`
Expected: PASS (new mapping tests + existing suite).

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/console/captureLevel.ts apps/desktop/src/console/captureLevel.test.ts
git commit -m "feat(desktop): captureLevel dBFS→segment mapping + capture-level hook"
```

---

## Task 5: Desktop meter component (`CaptureLevelMeter.tsx`)

**Files:**
- Create: `apps/desktop/src/console/CaptureLevelMeter.tsx`

(Thin presentation over the Task 4 pure functions; verified by `tsc` and downstream E2E. The testable logic lives in `captureLevel.ts`.)

- [ ] **Step 1: Create `CaptureLevelMeter.tsx`**

```tsx
// === ANCHOR: CAPTURE_LEVEL_METER_START ===
import type { CSSProperties } from "react";

import { CLIP_DBFS, SEGMENTS, WARN_DBFS, dbfsToSegments, segmentEdgeDbfs } from "./captureLevel";
import type { CaptureState } from "./captureStatus";

const GREEN = "#22c55e";
const YELLOW = "#fde047";
const RED = "#f87171";
const EMPTY_BG = "#1e293b";
const EMPTY_BORDER = "#334155";

function litColor(index: number): string {
  const edge = segmentEdgeDbfs(index);
  if (edge > CLIP_DBFS) return RED;
  if (edge > WARN_DBFS) return YELLOW;
  return GREEN;
}

/**
 * Live loudness meter shown beside the capture-status chip.
 * Renders only in active/silent states (the chip carries connecting/transport_down);
 * silent always shows an empty bar, which also covers Windows silence (no chunks → dbfs null).
 */
export function CaptureLevelMeter({ dbfs, state }: { dbfs: number | null; state: CaptureState }) {
  if (state === "connecting" || state === "transport_down") return null;
  const filled = state === "silent" ? 0 : dbfsToSegments(dbfs ?? -120);

  const wrap: CSSProperties = { display: "inline-flex", alignItems: "center", gap: 2 };
  return (
    <span role="img" aria-label={`캡처 레벨 ${filled}/${SEGMENTS}`} title="실시간 캡처 음량" style={wrap}>
      {Array.from({ length: SEGMENTS }, (_, i) => {
        const on = i < filled;
        const cell: CSSProperties = {
          width: 4,
          height: 11,
          borderRadius: 1,
          boxSizing: "border-box",
          background: on ? litColor(i) : EMPTY_BG,
          border: on ? "none" : `1px solid ${EMPTY_BORDER}`,
        };
        return <span key={i} style={cell} />;
      })}
    </span>
  );
}
// === ANCHOR: CAPTURE_LEVEL_METER_END ===
```

- [ ] **Step 2: Type-check**

Run: `pnpm --filter @yeson-meet/desktop exec tsc --noEmit`
Expected: PASS (no type errors). The component is not wired in yet, but it must compile standalone.

- [ ] **Step 3: Commit**

```bash
git add apps/desktop/src/console/CaptureLevelMeter.tsx
git commit -m "feat(desktop): CaptureLevelMeter segmented loudness bar"
```

---

## Task 6: Wire the meter into the subtitle header

**Files:**
- Modify: `apps/desktop/src/console/LiveSubtitlePreview.tsx`

- [ ] **Step 1: Add imports**

In `LiveSubtitlePreview.tsx`, add below the existing `CaptureStatusChip` import:

```tsx
import { CaptureLevelMeter } from "./CaptureLevelMeter";
import { CaptureStatusChip } from "./CaptureStatusChip";
import { useCaptureLevel, type CaptureState } from "./captureStatus";
```

Wait — `captureStatus` already exports `useCaptureStatus`/`CaptureState`, and `useCaptureLevel` lives in `captureLevel`. Use the correct sources:

```tsx
import { CaptureLevelMeter } from "./CaptureLevelMeter";
import { CaptureStatusChip } from "./CaptureStatusChip";
import { useCaptureLevel } from "./captureLevel";
import { useCaptureStatus, type CaptureState } from "./captureStatus";
```

(The `useCaptureStatus`/`CaptureState` import already exists — only add the two new lines for `CaptureLevelMeter` and `useCaptureLevel`; keep the existing `useCaptureStatus` import as-is.)

- [ ] **Step 2: Read the level in the component**

In the `LiveSubtitlePreview` function body, right after `const captureStatus = useCaptureStatus();`, add:

```tsx
  const captureLevel = useCaptureLevel();
```

- [ ] **Step 3: Pass the level to the header**

In the non-fullscreen `SubtitleHeader` render (the one that already passes `captureStatus={captureStatus}`), add the `level` prop:

```tsx
        <SubtitleHeader
          isFullscreen={fullscreen.isFullscreen}
          onToggleFullscreen={fullscreen.toggleFullscreen}
          status={stream.ended ? "ended" : stream.connected ? "live" : "connecting"}
          captureStatus={captureStatus}
          level={captureLevel}
        />
```

- [ ] **Step 4: Render the meter in `SubtitleHeader`**

Update the `SubtitleHeader` signature to accept `level`:

```tsx
function SubtitleHeader({
  isFullscreen,
  onToggleFullscreen,
  status,
  captureStatus = null,
  level = null,
}: {
  isFullscreen: boolean;
  onToggleFullscreen: () => Promise<void>;
  status: "idle" | "connecting" | "live" | "ended";
  captureStatus?: CaptureState | null;
  level?: number | null;
}) {
```

In its `subtitleHeaderActions` block, render the meter right after the chip:

```tsx
      <div style={consoleStyles.subtitleHeaderActions}>
        {captureStatus ? <CaptureStatusChip state={captureStatus} /> : null}
        {captureStatus ? <CaptureLevelMeter dbfs={level} state={captureStatus} /> : null}
        <button type="button" onClick={() => void onToggleFullscreen()} style={consoleStyles.subtitleFullscreenButton}>
          {isFullscreen ? "전체화면 종료" : "전체화면"}
        </button>
        <span style={status === "live" ? consoleStyles.liveBadge : consoleStyles.idleBadge}>{status}</span>
      </div>
```

- [ ] **Step 5: Type-check + tests**

Run: `pnpm --filter @yeson-meet/desktop exec tsc --noEmit`
Expected: PASS.

Run: `pnpm --filter @yeson-meet/desktop test`
Expected: PASS (full suite, no regressions).

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/console/LiveSubtitlePreview.tsx
git commit -m "feat(desktop): show capture level meter beside the status chip"
```

---

## Task 7: Full verification + docs/memory sync

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Run the full sidecar + desktop suites**

Run: `uv run pytest apps/client_sidecar/tests -q`
Expected: PASS (prior 56 + 7 new ≈ 63).

Run: `pnpm --filter @yeson-meet/desktop test`
Expected: PASS (prior 12 + new captureLevel suite).

Run: `pnpm --filter @yeson-meet/desktop exec tsc --noEmit`
Expected: PASS.

Run: `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml`
Expected: PASS (or pre-existing externalBin env quirk only).

- [ ] **Step 2: Update ROADMAP**

Add a native-track note in `docs/ROADMAP.md` (alongside the other `(2026-06-xx)` Phase 3 lines) recording: Phase 3 slice 3 dBFS capture level meter — code complete, Mac/unit verified, **Windows E2E pending next CI build**. Mention the dedicated `capture-level` event keeps the app-log clean; meter shows only in active/silent.

Example line to add after the slice-1+2 PASS line:

```markdown
> - **(2026-06-15) Phase 3 slice 3 — dBFS 캡처 레벨미터: 코드 완료·단위 검증·Windows E2E 대기**: 상태칩 옆 6칸 세그먼트 음량 미터. 사이드카 워치독이 slice 2의 `pcm16_dbfs`를 1초 평균내 `CAPTURE_LEVEL <dbfs>` stdout 마커로 emit → Rust 포워더가 **전용 `capture-level` Tauri 이벤트**로 라우팅(app-log 미적재, 진단 로그 청결) → 데스크톱 `CaptureLevelMeter`. 미터는 active/silent에서만 렌더(connecting/transport_down은 칩이 전달, silent=빈 막대로 Windows 무음=청크0 케이스 흡수). dBFS [-54,-6]→6칸, 상위 칸 과대 시 노랑/빨강. 검증: 사이드카 pytest(레벨 평균/staleness/마커 + 워치독 async) + 데스크톱 vitest(dbfsToSegments 매핑) + tsc 클린. Windows 4상태 라이브 미터 E2E 대기. 설계·계획: `docs/superpowers/specs|plans/2026-06-15-capture-level-meter*.md`.
```

- [ ] **Step 3: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs(native-audio): record dBFS capture level meter (Phase 3 slice 3)"
```

- [ ] **Step 4: Hand back for Windows E2E**

Report: code complete + all local checks green. Next gate = new Windows CI build, then live 4-state meter check (playing → bar tracks loudness with yellow/red on loud peaks; pause → empty bar; network off → meter gone, chip only; diagnostic log shows no `CAPTURE_LEVEL` lines). Update memory `[[project_win_wasapi_helper_status]]` after the Windows result.

---

## Self-Review

**Spec coverage:**
- §4.1 sidecar level + marker + watchdog refactor → Tasks 1, 2. ✓
- §4.2 Rust dedicated event → Task 3. ✓
- §4.3 captureLevel.ts (mapping + hook), CaptureLevelMeter.tsx, LiveSubtitlePreview wiring → Tasks 4, 5, 6. ✓
- §4.4 mapping constants → Task 4 (`captureLevel.ts`). ✓
- §5 tests (sidecar level/watchdog, desktop mapping, tsc) → Tasks 1, 2, 4, 7. ✓
- §6 E2E expectations → Task 7 Step 4 handoff. ✓
- §7 risks (parse-fail falls through to log; render churn) → Task 3 defensive parse; 1 Hz churn accepted (noted, no extra task needed). ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**Type consistency:** `dbfsToSegments`, `segmentEdgeDbfs`, `SEGMENTS`, `WARN_DBFS`, `CLIP_DBFS` consistent across Tasks 4–5; `CaptureLevelEvent { dbfs: f32 }` (Rust) ↔ `CaptureLevelPayload { dbfs: number }` (TS) ↔ `level_marker` 1-decimal format consistent; `useCaptureLevel(): number | null` consumed as `dbfs={level}` with `level?: number | null` prop. ✓
