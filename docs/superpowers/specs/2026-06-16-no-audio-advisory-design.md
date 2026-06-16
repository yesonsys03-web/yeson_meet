# No-Audio Advisory — Design

**Date:** 2026-06-16
**Branch:** topyeson
**Status:** Approved (design), pending implementation plan
**Track:** Native audio capture UX (Phase 3 follow-up)

## Problem

When a meeting runs but no audio reaches the capture path, the operator gets
no actionable signal about *why* subtitles aren't appearing. The canonical
case (2026-06-08): on Windows the default output device was the speakers, but
the operator was listening on headphones, so the WASAPI loopback of the
speakers was silent forever. The helper reported `started`, the WebSocket
connected, and then — nothing. No error, no guidance.

Two facts make this hard:

1. **A genuinely quiet meeting and a misconfigured/no-audio capture look
   identical at the signal level.** Both produce "no loud audio chunks." There
   is no reliable helper-level discriminator between "nobody is talking right
   now" and "audio is routed somewhere we can't hear." The only levers we have
   are **time** (a long stretch with no audio is more likely a setup problem
   than a pause) and **actionable guidance** (suggest checking the output
   device).

2. **On Windows, "no audio from the start" never even reaches the silent
   chip.** The current state machine keys "connecting → not-connecting" on
   *chunk presence* (`last_chunk_at`). Windows WASAPI loopback delivers **zero
   packets** during silence, so if the default device is silent from the
   moment the meeting starts, `last_chunk_at` stays `None` and the chip is
   **stuck on ⚪ connecting forever** — it never escalates to 🟡 silent. This
   is exactly the 2026-06-08 blind spot.

Note: "silence is informational, never an error" is **already** satisfied by
the existing `silent` state (slice 2): it fires only after 10 s with no *loud*
chunk (well above conversational pauses), shows a 🟡 informational chip, and
clears instantly when sound returns. This design does **not** change that. It
adds a second, longer-threshold tier on top of it.

## Goals

- After a long stretch (30 s) with no loud audio — whether audio was ever
  received or not — surface a **gentle, actionable advisory** that tells the
  operator to check their output device routing.
- Cover the "stuck on ⚪ connecting from the start" case (Windows, zero
  packets), not just the "was active, then went quiet" case.
- Never present as an error. Auto-clear the instant loud audio resumes.

## Non-Goals (YAGNI)

- Helper-side (Rust) structural-failure detection. Structural failures (device
  absent, permission denied, stream fails to open) are already handled by the
  fatal `NativeCaptureBanner` path. The 2026-06-08 case was **not** a
  structural failure — the device was valid and the stream opened cleanly — so
  helper-side detection would not catch it anyway.
- Differentiated thresholds (e.g. shorter for never-had-audio, longer for
  went-quiet). Single 30 s threshold.
- Env-configurable threshold, a distinct escalated color (🟠), or a separate
  banner. The advisory reuses the existing 🟡 silent chip visual; only the
  label/tooltip escalate.

## Design

### State machine — `apps/client_sidecar/transport/capture_status.py`

Add a fifth state, `no_audio`, and a new constant
`STALE_THRESHOLD_S = 30.0`.

To cover the "no loud audio ever since connecting" case, the reporter must
track when it first connected. Add `connected_at: float | None`, set on the
first successful `set_connected(True)`. The silence reference becomes:

```
reference = last_loud_at if last_loud_at is not None else connected_at
```

`compute_state` gains a `connected_at: float | None` parameter (still pure,
injected clock) and applies this priority, when `ws_connected`:

| Condition (ws connected) | State | Chip |
| --- | --- | --- |
| `reference is not None` and `now - reference >= STALE_THRESHOLD_S` | **`no_audio`** | 🟡 + actionable tooltip |
| `last_chunk_at is None` (no chunk yet, < stale) | `connecting` | ⚪ |
| `last_loud_at is None` or `now - last_loud_at >= SILENCE_THRESHOLD_S` | `silent` | 🟡 |
| otherwise | `active` | 🟢 |

When not connected: `transport_down` if `ever_connected` else `connecting`
(unchanged).

Full priority: `transport_down` > `no_audio` > `connecting` > `silent` >
`active`.

Behavior this produces:

- **Windows, silent from start** (zero packets): `last_chunk_at` is `None`, so
  ⚪ `connecting` for the first 30 s, then → 🟡 `no_audio` advisory. Rescues
  the stuck-connecting blind spot.
- **Was active, then quiet**: `last_loud_at` set → `active` (0–10 s) →
  `silent` (10–30 s) → `no_audio` (≥ 30 s).
- **Recovery**: a loud chunk sets `last_loud_at = now`, so the next poll
  returns `active` immediately (asymmetric hysteresis, unchanged).

`connected_at` is the first connect time; because `last_loud_at` dominates
`reference` whenever audio has ever been loud, `connected_at` only governs the
never-had-loud-audio case, where first-connect is the correct baseline.

The watchdog (`run_watchdog`) and marker emission are unchanged: it already
emits `CAPTURE_STATUS <state>` on each transition, so `no_audio` flows through
the existing path with no new wiring.

### Desktop — additive, two files

`apps/desktop/src/console/captureStatus.ts`:
- Add `"no_audio"` to the `CaptureState` union and the `KNOWN` array. The
  parser is otherwise unchanged.

`apps/desktop/src/console/CaptureStatusChip.tsx`:
- Add a `no_audio` entry to `PRESENTATION` using the **same colors as
  `silent`** (🟡: `color #fde047`, `bg #2a2408`, `border #a16207`), with an
  escalated label, e.g. `"오디오 없음"`.
- Add a `no_audio` entry to `TITLE` (tooltip) with actionable guidance, draft:
  > "30초 넘게 오디오가 안 들어왔어요. 회의 오디오가 PC 기본 출력장치로 가는지
  > 확인하세요(헤드폰/외부 출력으로 빠지면 캡처가 조용합니다)."

The level meter (`CaptureLevelMeter`) needs no change: in `no_audio` the
stream is stale, `reporter.level()` returns `None`, and the meter renders
empty bars exactly as it does for `silent`.

### Data flow / Rust

`CAPTURE_STATUS no_audio` is forwarded by the existing Rust app-log forwarder
(`sidecar.rs::spawn_output_forwarder`) with **no change** — it is not a
`CAPTURE_LEVEL` line, so it stays on the app-log path the desktop parser
already reads. No helper, Rust, CI, or tauri-config change.

## Error Handling / Edge Cases

- **False positives** (legitimate long quiet: silent presentation, break,
  muted screen-share): accepted and made cheap. The advisory only changes the
  text/tooltip of an already-present 🟡 chip and auto-clears on the next loud
  chunk, so a false positive is low-cost and self-correcting.
- **Disconnect/reconnect**: `transport_down` takes priority while
  disconnected. `connected_at` is the first-ever connect; reconnection edge
  cases are degenerate (only matters when audio was never loud) and not
  specially handled.
- **30 s boundary**: covered by a unit test at the threshold.

## Testing

Sidecar (`pytest`), pure `compute_state` + reporter:
- zero chunks: `connecting` → (`30 s`) → `no_audio`
- `active` → (`10 s`) `silent` → (`30 s`) `no_audio`
- `no_audio` → `active` immediately on a loud chunk (recovery)
- boundary just under / at `STALE_THRESHOLD_S`

Desktop (`vitest`):
- `parseCaptureStatus` accepts `no_audio`
- chip `PRESENTATION`/`TITLE` map has a `no_audio` entry with the silent
  colors and an actionable tooltip

Verification gate: sidecar `pytest`, desktop `vitest` + `tsc`, host
`cargo check` (no Rust change, but keep the gate green). Windows live E2E:
start a meeting with audio routed to a non-default device (e.g. headphones)
and confirm the chip escalates ⚪/🟡 → 🟡 `no_audio` advisory within ~30 s,
then clears to 🟢 when audio returns to the default device.

## Affected Files

- `apps/client_sidecar/transport/capture_status.py` (state machine + constant
  + `connected_at`)
- `apps/desktop/src/console/captureStatus.ts` (union + KNOWN)
- `apps/desktop/src/console/CaptureStatusChip.tsx` (PRESENTATION + TITLE)
- tests: `apps/client_sidecar/tests/...` (compute_state),
  `apps/desktop/src/console/captureStatus.test.ts` /
  chip test (whichever holds the presentation assertions)

No Rust, helper, CI, or config changes.
