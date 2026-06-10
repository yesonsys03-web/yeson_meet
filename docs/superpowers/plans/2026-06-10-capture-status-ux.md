# Real-time Capture Status + Coarse Activity — Implementation Plan (Phase 3 slice 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the operator a live capture-status chip (⚪ connecting / 🟢 active / 🟡 silent / 🔴 transport_down) so "is audio being captured right now" is always visible.

**Architecture:** The sidecar runs a 1 Hz watchdog task that reads a `CaptureStatusReporter` (updated by the WS send loop) and prints `CAPTURE_STATUS <state>` to stdout on each transition. Rust forwards stdout to the desktop app-log (unchanged). The desktop parses the marker (reusing the existing `NATIVE_STATUS` pattern) and renders a chip in the live-subtitle header. Capture *failures* keep using the existing `NativeCaptureBanner`; the chip is the live status.

**Tech Stack:** Python sidecar (`asyncio`, `websockets`), pytest. Desktop React/TypeScript, vitest. The decision logic is a pure clock-injected function unit-tested on macOS; only the watchdog wiring needs Windows E2E.

**Spec:** `docs/superpowers/specs/2026-06-10-capture-status-ux-design.md`. Read §2 (states), §3 (data flow), §4 (decider) before implementing.

---

## Environment Legend

- **[MAC]** — runs on the macOS dev machine (pytest, vitest, tsc, ruff, docs).
- **[WIN]** — runs on a real Windows 10/11 machine (live capture E2E of the 4 states).
- **[BOTH]** — runs on either.

Almost everything is **[MAC]**: the decider, reporter, parser, hook, and chip are all unit-testable on macOS. Only Task 6 (live 4-state E2E) is **[WIN]**.

---

## File Structure

```
apps/client_sidecar/
  transport/capture_status.py        # NEW [pure] compute_state + CaptureStatusReporter + run_watchdog + SILENCE_THRESHOLD_S
  transport/audio_ws.py              # MODIFY stream_audio: optional reporter param + 3 update points
  main.py                            # MODIFY audio_main: create reporter, spawn/cancel watchdog, print CAPTURE_STATUS
  tests/test_capture_status.py       # NEW pytest for the decider + reporter

apps/desktop/src/console/
  captureStatus.ts                   # NEW parseCaptureStatus + latestCaptureStatus + useCaptureStatus
  captureStatus.test.ts              # NEW vitest for parser + latest-wins
  CaptureStatusChip.tsx              # NEW state → color/label chip
  LiveSubtitlePreview.tsx            # MODIFY call useCaptureStatus(), render chip in the header (active path)
```

**Commit convention:** every commit message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Work happens on branch `topyeson` (already checked out).

**Marker contract (single source of truth):** the sidecar prints `CAPTURE_STATUS <state>` where `<state>` ∈ `{connecting, active, silent, transport_down}`, one line per transition. The desktop parses exactly that.

---

## Task 1: `capture_status.py` — pure decider + reporter + watchdog [MAC TDD]

The testable core. No `websockets`/cpal; the decider takes timestamps + booleans so every transition is unit-tested (mirrors the device_watch decider pattern).

**Files:**
- Create: `apps/client_sidecar/transport/capture_status.py`
- Create: `apps/client_sidecar/tests/test_capture_status.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/client_sidecar/tests/test_capture_status.py`:

```python
"""Pure capture-status decider + reporter coalescing."""
from apps.client_sidecar.transport.capture_status import (
    ACTIVE,
    CONNECTING,
    SILENT,
    TRANSPORT_DOWN,
    CaptureStatusReporter,
    compute_state,
)

T = 10.0  # silence threshold used in tests


def test_connecting_before_first_connect():
    assert (
        compute_state(ws_connected=False, ever_connected=False, last_chunk_at=None, now=5.0, threshold=T)
        == CONNECTING
    )


def test_connecting_after_connect_before_first_chunk():
    assert (
        compute_state(ws_connected=True, ever_connected=True, last_chunk_at=None, now=5.0, threshold=T)
        == CONNECTING
    )


def test_active_on_recent_chunk():
    assert (
        compute_state(ws_connected=True, ever_connected=True, last_chunk_at=100.0, now=105.0, threshold=T)
        == ACTIVE
    )


def test_silent_after_threshold():
    # gap exactly at threshold counts as silent
    assert (
        compute_state(ws_connected=True, ever_connected=True, last_chunk_at=100.0, now=110.0, threshold=T)
        == SILENT
    )


def test_transport_down_after_disconnect():
    assert (
        compute_state(ws_connected=False, ever_connected=True, last_chunk_at=100.0, now=101.0, threshold=T)
        == TRANSPORT_DOWN
    )


def test_transport_down_takes_priority_over_silence():
    # ws down + recent chunk → transport_down, not active/silent
    assert (
        compute_state(ws_connected=False, ever_connected=True, last_chunk_at=100.0, now=100.5, threshold=T)
        == TRANSPORT_DOWN
    )


def test_reporter_emits_only_on_transition():
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0)
    assert r.poll(now=101.0) == ACTIVE   # first active → emit
    assert r.poll(now=102.0) is None     # still active → coalesced
    # 10s with no new chunk → silent
    assert r.poll(now=111.0) == SILENT
    assert r.poll(now=112.0) is None     # still silent → coalesced


def test_reporter_instant_recovery_from_silent():
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0)
    assert r.poll(now=111.0) == SILENT
    # a single new chunk → next poll is active immediately (asymmetric hysteresis)
    r.note_chunk(now=111.5)
    assert r.poll(now=111.6) == ACTIVE


def test_reporter_transport_down_then_reconnect():
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0)
    assert r.poll(now=100.1) == ACTIVE
    r.set_connected(False)               # ws dropped
    assert r.poll(now=100.2) == TRANSPORT_DOWN
    r.set_connected(True)                # reconnected; recent chunk still within threshold
    assert r.poll(now=100.3) == ACTIVE


def test_reporter_starts_connecting():
    r = CaptureStatusReporter(threshold=T)
    assert r.poll(now=0.5) == CONNECTING
```

- [ ] **Step 2: Run the tests to verify they FAIL**

Run: `uv run pytest apps/client_sidecar/tests/test_capture_status.py -q`
Expected: FAIL to import — `capture_status` module / `compute_state` not defined.

- [ ] **Step 3: Write the implementation**

Create `apps/client_sidecar/transport/capture_status.py`:

```python
# === ANCHOR: CAPTURE_STATUS_START ===
"""Live capture-status state machine (pure) + reporter + stdout watchdog.

The sidecar can't tell the operator "audio is flowing" today. This emits a
`CAPTURE_STATUS <state>` stdout marker on each transition so the desktop can
show a live chip. Decision logic is pure (timestamps + booleans, injected
clock) so it unit-tests without websockets/asyncio.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

# Silence threshold: well above natural conversational pauses so we flag
# "audio genuinely stopped for a while" (informational), never speech cadence.
SILENCE_THRESHOLD_S = 10.0

CONNECTING = "connecting"
ACTIVE = "active"
SILENT = "silent"
TRANSPORT_DOWN = "transport_down"

MARKER = "CAPTURE_STATUS "


def compute_state(
    *,
    ws_connected: bool,
    ever_connected: bool,
    last_chunk_at: float | None,
    now: float,
    threshold: float = SILENCE_THRESHOLD_S,
) -> str:
    """Pure: derive the capture state from connection + chunk-flow facts.

    Priority: transport_down (ws lost after connecting) > connecting (pre-first
    chunk) > silent (gap >= threshold) > active. `ever_connected` distinguishes
    startup (connecting) from a mid-session drop (transport_down).
    """
    if not ws_connected:
        return TRANSPORT_DOWN if ever_connected else CONNECTING
    if last_chunk_at is None:
        return CONNECTING
    if now - last_chunk_at >= threshold:
        return SILENT
    return ACTIVE


class CaptureStatusReporter:
    """Mutable capture facts + transition-coalescing. Updated by the WS loop,
    polled by the watchdog."""

    def __init__(self, threshold: float = SILENCE_THRESHOLD_S) -> None:
        self._threshold = threshold
        self._ws_connected = False
        self._ever_connected = False
        self._last_chunk_at: float | None = None
        self._emitted: str | None = None

    def set_connected(self, ok: bool) -> None:
        self._ws_connected = ok
        if ok:
            self._ever_connected = True

    def note_chunk(self, now: float) -> None:
        self._last_chunk_at = now

    def poll(self, now: float) -> str | None:
        """Return the new state iff it changed since the last emit, else None."""
        state = compute_state(
            ws_connected=self._ws_connected,
            ever_connected=self._ever_connected,
            last_chunk_at=self._last_chunk_at,
            now=now,
            threshold=self._threshold,
        )
        if state == self._emitted:
            return None
        self._emitted = state
        return state


async def run_watchdog(
    reporter: CaptureStatusReporter,
    emit: Callable[[str], None],
    *,
    interval: float = 1.0,
    now_fn: Callable[[], float] = time.monotonic,
) -> None:
    """Poll the reporter every `interval`s and `emit` each transition. Runs as a
    standalone asyncio task so it detects silence even while the send loop blocks
    awaiting the next chunk (native path emits no packets during silence)."""
    while True:
        await asyncio.sleep(interval)
        state = reporter.poll(now_fn())
        if state is not None:
            emit(state)
# === ANCHOR: CAPTURE_STATUS_END ===
```

- [ ] **Step 4: Run the tests to verify they PASS**

Run: `uv run pytest apps/client_sidecar/tests/test_capture_status.py -q`
Expected: 10 tests pass.

- [ ] **Step 5: Lint**

Run: `uv run ruff check apps/client_sidecar/transport/capture_status.py apps/client_sidecar/tests/test_capture_status.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add apps/client_sidecar/transport/capture_status.py apps/client_sidecar/tests/test_capture_status.py
git commit -m "feat(sidecar): pure capture-status decider + reporter + watchdog"
```

---

## Task 2: wire the reporter into `stream_audio` [MAC]

`stream_audio` is the only place that knows ws-connected and chunk flow. Add an optional `reporter` and update it at three points. Backward-compatible (`reporter=None` default), so existing callers/tests are unaffected.

**Files:**
- Modify: `apps/client_sidecar/transport/audio_ws.py`

- [ ] **Step 1: Add `time` import**

In `apps/client_sidecar/transport/audio_ws.py`, add `import time` to the stdlib imports (next to `import asyncio`, `import json`, `import logging`):

```python
import asyncio
import json
import logging
import time
```

- [ ] **Step 2: Add the optional `reporter` param and the three update points**

Replace the `stream_audio` function body (the block between `# === ANCHOR: AUDIO_WS_STREAM_AUDIO_START ===` and its END) with this — same control flow, plus four `reporter` lines (import-free; `CaptureStatusReporter` is duck-typed via the param):

```python
# === ANCHOR: AUDIO_WS_STREAM_AUDIO_START ===
async def stream_audio(url: str, chunks: AsyncIterator[bytes], reporter=None) -> None:
    """Connect, send audio.started, stream binary chunks, periodic chunk_meta, audio.stopped on exit.

    `reporter` (optional CaptureStatusReporter): updated on connect / per chunk /
    disconnect so a watchdog can surface live capture status. None disables it.
    """
    backoff = 1.0
    seq = 0
    stopped_reason: str | None = None

    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                logger.info("audio ws connected: %s", _safe_url(url))
                backoff = 1.0
                if reporter is not None:
                    reporter.set_connected(True)

                # audio.started
                await ws.send(json.dumps({
                    "type": "audio.started",
                    "sample_rate": TARGET_SAMPLE_RATE,
                    "channels": TARGET_CHANNELS,
                    "format": "pcm_s16le",
                    "started_at": _iso_now(),
                }))

                try:
                    async for chunk in chunks:
                        seq += 1
                        if reporter is not None:
                            reporter.note_chunk(time.monotonic())
                        await ws.send(chunk)  # binary
                        if seq % CHUNK_META_INTERVAL == 0:
                            await ws.send(json.dumps({
                                "type": "chunk_meta",
                                "seq": seq,
                                "started_at": _iso_now(),
                            }))
                    stopped_reason = "stream exhausted"
                except (asyncio.CancelledError, KeyboardInterrupt):
                    stopped_reason = "user cancel"
                    raise
                finally:
                    try:
                        await ws.send(json.dumps({
                            "type": "audio.stopped",
                            "reason": stopped_reason,
                        }))
                    except Exception:
                        pass
                return
        except (ConnectionClosed, OSError) as e:
            logger.warning("audio ws closed: %s — reconnect in %.1fs", e, backoff)
            if reporter is not None:
                reporter.set_connected(False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
# === ANCHOR: AUDIO_WS_STREAM_AUDIO_END ===
```

- [ ] **Step 3: Verify it imports + lints (behavior covered by Task 1 decider tests + Task 6 E2E)**

Run: `uv run python -c "import apps.client_sidecar.transport.audio_ws"`
Expected: no output, exit 0.

Run: `uv run ruff check apps/client_sidecar/transport/audio_ws.py`
Expected: no errors.

- [ ] **Step 4: Run the full sidecar suite to confirm no regression**

Run: `uv run pytest apps/client_sidecar/tests -q`
Expected: all pass (existing audio_ws-related tests still green; `reporter=None` keeps old behavior).

- [ ] **Step 5: Commit**

```bash
git add apps/client_sidecar/transport/audio_ws.py
git commit -m "feat(sidecar): feed capture-status reporter from the audio WS loop"
```

---

## Task 3: spawn the watchdog in `audio_main` [MAC]

Wire the reporter + watchdog lifecycle around `stream_audio`, and print `CAPTURE_STATUS` markers. The watchdog is cancelled in `finally` so it never outlives the stream (parent `main()` cancels `audio_main` on shutdown).

**Files:**
- Modify: `apps/client_sidecar/main.py:42-65` (`audio_main`)

- [ ] **Step 1: Replace `audio_main` body**

Replace the block between `# === ANCHOR: MAIN_AUDIO_MAIN_START ===` and its END with:

```python
# === ANCHOR: MAIN_AUDIO_MAIN_START ===
async def audio_main() -> None:
    """S2 audio mode — provider factory selects source, then stream to server WS."""
    from apps.client_sidecar.audio.sources.factory import make_source
    from apps.client_sidecar.audio.sources.native_pipe_source import NativeCaptureError
    from apps.client_sidecar.transport.audio_ws import stream_audio
    from apps.client_sidecar.transport.capture_status import (
        CaptureStatusReporter,
        run_watchdog,
    )

    api_key = _required_env("YESON_DEVICE_API_KEY")
    session_id = UUID(_required_env("YESON_SESSION_ID"))

    source = make_source()
    url = f"{SERVER_WS_BASE}{SERVER_WS_PATH}?key={api_key}&session={session_id}"
    print(f"sidecar audio mode → source={type(source).__name__} url={url.split('?')[0]}?key=<redacted>")

    # Live capture-status heartbeat: the watchdog prints CAPTURE_STATUS <state>
    # on each transition (forwarded to the desktop app log → status chip).
    reporter = CaptureStatusReporter()
    watchdog = asyncio.create_task(
        run_watchdog(reporter, lambda state: print(f"CAPTURE_STATUS {state}", flush=True))
    )
    try:
        await stream_audio(url, source.chunks(), reporter)
    except NativeCaptureError as exc:
        # Native-only target: no silent death. Emit a recognizable status line
        # (forwarded to the desktop app log) so the cause is visible/actionable.
        print(f"NATIVE_STATUS {exc.reason}", flush=True)
        logger.error("native capture failed: reason=%s", exc.reason)
        raise
    finally:
        watchdog.cancel()
        try:
            await watchdog
        except asyncio.CancelledError:
            pass
        await source.close()
# === ANCHOR: MAIN_AUDIO_MAIN_END ===
```

- [ ] **Step 2: Verify it imports + lints**

Run: `uv run python -c "import apps.client_sidecar.main"`
Expected: no output, exit 0.

Run: `uv run ruff check apps/client_sidecar/main.py`
Expected: no errors.

- [ ] **Step 3: Run the full sidecar suite**

Run: `uv run pytest apps/client_sidecar/tests -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add apps/client_sidecar/main.py
git commit -m "feat(sidecar): spawn capture-status watchdog in audio_main"
```

---

## Task 4: desktop parser + hook [MAC TDD]

Mirror `nativeCaptureStatus.ts`: parse `CAPTURE_STATUS <state>` from the app log, expose the latest state via a hook. Pure parser + latest-wins are unit-tested (the hook is a thin wrapper, like the existing one).

**Files:**
- Create: `apps/desktop/src/console/captureStatus.ts`
- Create: `apps/desktop/src/console/captureStatus.test.ts`

- [ ] **Step 1: Write the failing test**

Create `apps/desktop/src/console/captureStatus.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { AppLogEntry } from "../diagnostics/appLog";
import { latestCaptureStatus, parseCaptureStatus } from "./captureStatus";

function entry(id: number, message: string): AppLogEntry {
  return { id, ts: "", level: "info", source: "sidecar:stdout", message };
}

describe("parseCaptureStatus", () => {
  it("extracts a known state token", () => {
    expect(parseCaptureStatus("CAPTURE_STATUS active")).toBe("active");
    expect(parseCaptureStatus("CAPTURE_STATUS silent")).toBe("silent");
    expect(parseCaptureStatus("CAPTURE_STATUS transport_down")).toBe("transport_down");
    expect(parseCaptureStatus("CAPTURE_STATUS connecting")).toBe("connecting");
  });

  it("returns null for non-markers and unknown states", () => {
    expect(parseCaptureStatus("hello world")).toBeNull();
    expect(parseCaptureStatus("CAPTURE_STATUS")).toBeNull();
    expect(parseCaptureStatus("CAPTURE_STATUS bogus")).toBeNull();
  });
});

describe("latestCaptureStatus", () => {
  it("returns the most recent capture status in the log", () => {
    const entries = [
      entry(1, "sidecar audio mode"),
      entry(2, "CAPTURE_STATUS connecting"),
      entry(3, "CAPTURE_STATUS active"),
      entry(4, "CAPTURE_STATUS silent"),
    ];
    expect(latestCaptureStatus(entries)).toBe("silent");
  });

  it("returns null when there is no capture status", () => {
    expect(latestCaptureStatus([entry(1, "nothing here")])).toBeNull();
  });
});
```

- [ ] **Step 2: Run the test to verify it FAILS**

Run: `cd apps/desktop && pnpm vitest run src/console/captureStatus.test.ts`
Expected: FAIL — `captureStatus` module not found.

- [ ] **Step 3: Write the implementation**

Create `apps/desktop/src/console/captureStatus.ts`:

```ts
// === ANCHOR: CAPTURE_STATUS_START ===
import { useEffect, useState } from "react";

import { subscribeAppLogs, type AppLogEntry } from "../diagnostics/appLog";

// The sidecar prints `CAPTURE_STATUS <state>` on each capture-state transition
// (connecting/active/silent/transport_down). Rust forwards it into the app log;
// we promote the latest one into a live status chip. Mirrors nativeCaptureStatus.
const MARKER = "CAPTURE_STATUS ";

export type CaptureState = "connecting" | "active" | "silent" | "transport_down";

const KNOWN: readonly CaptureState[] = ["connecting", "active", "silent", "transport_down"];

/** Extract a known capture state from a `CAPTURE_STATUS <state>` line, else null. */
export function parseCaptureStatus(message: string): CaptureState | null {
  if (!message.startsWith(MARKER)) return null;
  const token = message.slice(MARKER.length).trim().split(/\s+/)[0] ?? "";
  return (KNOWN as readonly string[]).includes(token) ? (token as CaptureState) : null;
}

/** Most recent capture state in the log, or null if none. */
export function latestCaptureStatus(entries: AppLogEntry[]): CaptureState | null {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const item = entries[i];
    if (!item) continue;
    const state = parseCaptureStatus(item.message);
    if (state) return state;
  }
  return null;
}

/** Subscribe to the app log and expose the latest capture state. */
export function useCaptureStatus(): CaptureState | null {
  const [state, setState] = useState<CaptureState | null>(null);
  useEffect(() => subscribeAppLogs((entries) => setState(latestCaptureStatus(entries))), []);
  return state;
}
// === ANCHOR: CAPTURE_STATUS_END ===
```

- [ ] **Step 4: Run the test to verify it PASSES**

Run: `cd apps/desktop && pnpm vitest run src/console/captureStatus.test.ts`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/console/captureStatus.ts apps/desktop/src/console/captureStatus.test.ts
git commit -m "feat(desktop): parse CAPTURE_STATUS markers into a live capture-state hook"
```

---

## Task 5: status chip + placement in the subtitle header [MAC]

A small chip component, placed in the live-subtitle header next to the existing stream badge. Rendered only on the active path (sessionId + operatorToken present), which naturally gates it to a running meeting.

**Files:**
- Create: `apps/desktop/src/console/CaptureStatusChip.tsx`
- Modify: `apps/desktop/src/console/LiveSubtitlePreview.tsx`

- [ ] **Step 1: Write the chip component**

Create `apps/desktop/src/console/CaptureStatusChip.tsx`:

```tsx
// === ANCHOR: CAPTURE_STATUS_CHIP_START ===
import type { CSSProperties } from "react";

import type { CaptureState } from "./captureStatus";

const PRESENTATION: Record<CaptureState, { label: string; color: string; bg: string; border: string }> = {
  connecting: { label: "연결 중", color: "#cbd5e1", bg: "#1e293b", border: "#475569" },
  active: { label: "정상", color: "#86efac", bg: "#0f2a1a", border: "#15803d" },
  silent: { label: "무음", color: "#fde047", bg: "#2a2408", border: "#a16207" },
  transport_down: { label: "전송 끊김", color: "#fca5a5", bg: "#2a0f0f", border: "#b91c1c" },
};

const TITLE: Record<CaptureState, string> = {
  connecting: "오디오 캡처 연결 중",
  active: "오디오가 정상 캡처·전송되고 있습니다",
  silent: "10초 이상 오디오가 없습니다 (캡처는 정상) — 예상 밖이면 소스/장치를 확인하세요",
  transport_down: "서버로의 오디오 전송이 끊겼습니다 (재연결 시도 중)",
};

export function CaptureStatusChip({ state }: { state: CaptureState }) {
  const p = PRESENTATION[state];
  const chipStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "2px 8px",
    borderRadius: 999,
    border: `1px solid ${p.border}`,
    background: p.bg,
    color: p.color,
    fontSize: 12,
    fontWeight: 600,
    whiteSpace: "nowrap",
  };
  const dotStyle: CSSProperties = {
    width: 7,
    height: 7,
    borderRadius: "50%",
    background: p.color,
    flexShrink: 0,
  };
  return (
    <span role="status" title={TITLE[state]} style={chipStyle}>
      <span style={dotStyle} />
      {p.label}
    </span>
  );
}
// === ANCHOR: CAPTURE_STATUS_CHIP_END ===
```

- [ ] **Step 2: Wire it into `LiveSubtitlePreview`**

In `apps/desktop/src/console/LiveSubtitlePreview.tsx`:

(a) Add imports near the top (after the existing `./consoleStyles` import):

```tsx
import { CaptureStatusChip } from "./CaptureStatusChip";
import { useCaptureStatus } from "./captureStatus";
```

(b) Inside `LiveSubtitlePreview`, right after `const stream = useLiveSubtitleStream(sessionId, operatorToken);`, add:

```tsx
  const captureStatus = useCaptureStatus();
```

(c) In the **active return** (the final `return (`), pass the chip to the header by replacing the existing non-fullscreen `<SubtitleHeader … />` (the one with `status={stream.ended ? "ended" : stream.connected ? "live" : "connecting"}`) with:

```tsx
      {!fullscreen.isFullscreen ? (
        <SubtitleHeader
          isFullscreen={fullscreen.isFullscreen}
          onToggleFullscreen={fullscreen.toggleFullscreen}
          status={stream.ended ? "ended" : stream.connected ? "live" : "connecting"}
          captureStatus={captureStatus}
        />
      ) : null}
```

(d) Extend `SubtitleHeader` to accept and render the chip. Replace the `SubtitleHeader` function with:

```tsx
function SubtitleHeader({
  isFullscreen,
  onToggleFullscreen,
  status,
  captureStatus = null,
}: {
  isFullscreen: boolean;
  onToggleFullscreen: () => Promise<void>;
  status: "idle" | "connecting" | "live" | "ended";
  captureStatus?: CaptureState | null;
}) {
  return (
    <div style={consoleStyles.subtitleHeader}>
      <div style={consoleStyles.subtitleHeadingGroup}>
        <strong>Live subtitles</strong>
        <span style={consoleStyles.subtitleShortcutHint}>F 자막 전용 전체화면 · Esc/F 종료</span>
      </div>
      <div style={consoleStyles.subtitleHeaderActions}>
        {captureStatus ? <CaptureStatusChip state={captureStatus} /> : null}
        <button type="button" onClick={() => void onToggleFullscreen()} style={consoleStyles.subtitleFullscreenButton}>
          {isFullscreen ? "전체화면 종료" : "전체화면"}
        </button>
        <span style={status === "live" ? consoleStyles.liveBadge : consoleStyles.idleBadge}>{status}</span>
      </div>
    </div>
  );
}
```

(e) Add the `CaptureState` type import (used in the `SubtitleHeader` prop type). Update the `useCaptureStatus` import line to also import the type:

```tsx
import { useCaptureStatus, type CaptureState } from "./captureStatus";
```

(Remove the separate `import { useCaptureStatus } from "./captureStatus";` line from step (a) so there is exactly one import from `./captureStatus`.)

The idle / no-token returns (which render `<SubtitleHeader … status="idle" />` without `captureStatus`) default to `captureStatus = null` → no chip, so the chip only shows during an active meeting.

- [ ] **Step 3: Typecheck**

Run: `cd apps/desktop && pnpm tsc --noEmit`
Expected: no type errors.

- [ ] **Step 4: Run the desktop test suite (no regression)**

Run: `cd apps/desktop && pnpm vitest run`
Expected: all tests pass (captureStatus + existing).

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/console/CaptureStatusChip.tsx apps/desktop/src/console/LiveSubtitlePreview.tsx
git commit -m "feat(desktop): live capture-status chip in the subtitle header"
```

---

## Task 6: Windows live E2E — the 4 states [WIN]

Verify the marker→chip path end to end on real capture. (Mac can't exercise the WASAPI capture loop.)

**Prereqs:** install a fresh Windows CI build that includes Tasks 1–5; start a meeting so audio is captured.

- [ ] **Step 1: 정상 (active)**

Play audio on the default output. Within ~1–2 s the subtitle header shows **🟢 정상**. Subtitles appear.

- [ ] **Step 2: 무음 (silent)**

Pause the audio (true silence). After ~10 s the chip turns **🟡 무음**. Resume audio → chip returns to **🟢 정상** within ~1 s (instant recovery).

- [ ] **Step 3: 전송 끊김 (transport_down)**

Stop the server (or disconnect network). The chip turns **🔴 전송 끊김** while the sidecar retries. Restart the server → chip returns to 🟢/⚪.

- [ ] **Step 4: 캡처 실패 → 배너 (not chip)**

Disable the default output device entirely. The helper fatals → the existing **NativeCaptureBanner** appears (장치/권한 메시지); the chip is not the surface for terminal failure.

- [ ] **Step 5: Record results**

Note pass/fail for each of the 4 states + the silence latency feel in the spec §8 checklist.

---

## Task 7: Docs sync [MAC]

**Files:**
- Modify: `docs/NATIVE_DESKTOP_HELPER_PLAN.md` (Phase 3 section)
- Modify: `docs/ROADMAP.md` (native track)

- [ ] **Step 1: Mark the slice in the native plan**

In `docs/NATIVE_DESKTOP_HELPER_PLAN.md` under "### Phase 3 — 데스크톱 앱 통합", add a dated `> 📌 (2026-06-10)` callout: real-time capture-status chip (connecting/active/silent/transport_down) via sidecar `CAPTURE_STATUS` heartbeat landed; capture failures stay on the existing banner; dBFS level meter + source selection deferred.

- [ ] **Step 2: Update the ROADMAP native-track line**

In `docs/ROADMAP.md`, add a 2026-06-10 line recording Phase 3 slice 1 (capture status + coarse activity) status.

- [ ] **Step 3: Commit**

```bash
git add docs/NATIVE_DESKTOP_HELPER_PLAN.md docs/ROADMAP.md
git commit -m "docs(native-audio): record Phase 3 capture-status chip slice"
```

---

## Self-Review (author checklist)

**Spec coverage:**
- §1 data source = sidecar heartbeat → Tasks 1–3. ✓
- §2 states (connecting/active/silent/transport_down) + failure→banner → Task 1 (decider), Task 5 (chip), Task 6 Step 4. ✓
- §3 data flow (reporter updated by WS loop, watchdog prints marker, desktop parses) → Tasks 2,3,4. ✓
- §4 pure decider + reporter + run_watchdog + 10s threshold + asymmetric hysteresis → Task 1 (tests: silence threshold, instant recovery, transport priority, ever_connected, coalesce). ✓
- §5 desktop parser + hook + chip + lifecycle-gated visibility → Tasks 4,5 (chip only on active path). ✓
- §6 non-scope (no RMS meter, no source selection, no server audio_stats) → respected; none added. ✓
- §7 risks (marker spam→coalesce in `poll`; watchdog leak→cancel in finally; stale chip→active-path gate; monotonic clock) → Tasks 1,3,5. ✓
- §8 verification (pytest decider, vitest parser, Windows 4-state E2E) → Tasks 1,4,6. ✓
- §9 deliverables → Tasks map 1:1. ✓

**Placeholder scan:** no TBD/TODO; every code step shows full code; commands have expected output. ✓

**Type/name consistency:** `compute_state(*, ws_connected, ever_connected, last_chunk_at, now, threshold)`, `CaptureStatusReporter.{set_connected,note_chunk,poll}`, `run_watchdog(reporter, emit, *, interval, now_fn)`, marker `CAPTURE_STATUS `, states `connecting|active|silent|transport_down`, `parseCaptureStatus`/`latestCaptureStatus`/`useCaptureStatus`, `CaptureState`, `CaptureStatusChip state` prop — consistent across Tasks 1–5. ✓

**Mac-testability:** decider (pytest) + parser (vitest) + tsc cover everything except the live capture loop; only Task 6 is Windows. Matches the project's Mac-test / Windows-E2E split.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-10-capture-status-ux.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Tasks 1–5 + 7 run here on macOS; Task 6 is Windows.

**2. Inline Execution** — execute Tasks 1–5 + 7 in this session (pytest/vitest/tsc all run on macOS), then hand Task 6 to the Windows machine.

**Which approach?**
