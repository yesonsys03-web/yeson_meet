# Windows Default-Device-Change Tracking (device_watch) — Implementation Plan (Phase 2b ②)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the Windows default output device changes mid-meeting (speakers → headphones/BT/HDMI), the WASAPI helper rebuilds its loopback on the new default **in-process** so subtitles don't silently stop. The sidecar/server/WS are untouched; the only sidecar-visible artifact is one `device_changed` stderr line.

**Architecture:** A small **polling** loop, inline in the helper's existing worker loop. Every ~1.5 s (clock-gated, so it costs nothing in the audio path) the worker re-queries `cpal::default_output_device().name()` and compares it to the device it is currently capturing. A pure decision module (`device_watch.rs`, no cpal/windows types, clock injected) decides Rebuild vs Ignore (with a min-rebuild-interval throttle against flapping). On Rebuild the worker drops the old `Capture`, calls `capture::start()` again (which opens the new default), swaps `rx`/`err_rx`/`Capture`/`PcmConverter` synchronously, and emits `device_changed`. Because the swap is synchronous and the superseded receivers are never read again, the old stream's `Disconnected` is never observed — no explicit epoch counter needed (spec §4 intent achieved structurally).

**Tech Stack:** Rust (existing `apps/native_helper_win` crate). **No new crates, no COM, no `windows` dependency** — `cpal` is already a Windows dep and handles its own COM init. Decision logic is pure Rust unit-tested on macOS.

**Spec:** `docs/superpowers/specs/2026-06-10-windows-default-device-watch-design.md`. Read §0 (bug distinction), §2 (in-process invariant), §3 (flow), §4 (rebuild swap) before implementing.

---

## Environment Legend

The dev machine is macOS; WASAPI capture + device switching only exist on Windows.

- **[MAC]** — runs on the macOS dev machine (`cargo test` of the pure module, `cargo check`, docs).
- **[WIN]** — runs on a real Windows 10/11 x86_64 machine/VM (cpal capture, device switching, E2E).
- **[BOTH]** — compiles on either.

Module-isolation rule (spec §5): `device_watch.rs` must NOT import any `cpal`/`windows` type — it takes device-name strings + an injected `now` integer, so it compiles and unit-tests on macOS. All cpal calls stay in `capture.rs` (and the `#[cfg(windows)] fn main`). `main.rs` already has a `#[cfg(not(windows))]` stub so the crate builds on macOS; the new poll/rebuild code lives only in the `#[cfg(windows)]` entry.

---

## File Structure

```
apps/native_helper_win/src/
  device_watch.rs   # NEW [MAC-testable] DeviceWatcher::decide(active, polled, now_ms) → Rebuild|Ignore + throttle
  lib.rs            # MODIFY add `pub mod device_watch;` (modules are declared here, not in main.rs)
  capture.rs        # MODIFY add current_default_device_name() -> Option<String> (fresh re-query; keeps cpal in this module)
  main.rs           # MODIFY extend `use yeson_win_audio_helper::{…}` with device_watch; inline poll (1.5s clock gate) → decide → synchronous rebuild swap + device_changed
```

> 📌 The crate is a **lib + bin**: pure/shared modules live in `src/lib.rs` (`pub mod ipc; pub mod pcm; #[cfg(windows)] pub mod capture; …`) and `main.rs` consumes them via `use yeson_win_audio_helper::{…}` (so the `stream_dump` tool can reuse them too). New modules are declared in `lib.rs`, NOT `main.rs`.

Unchanged: `ipc.rs`, `pcm.rs`, `source.rs`, `Cargo.toml` (no new deps). Python (`native_pipe_source.py`) unchanged — the `device_changed` event is logged by the existing `_drain_stderr` INFO path (verify in Task 3, no code change).

**Scope guard (spec §7):** This plan implements **only slice (a)** — switch on default-device *change* (demotion, the reported gap). Device *removal* (cpal stream error → `fatal:stream_error`) **stays as-is** per the prior WASAPI spec §7. The "self-heal in place on removal" extension (b) is explicitly NOT in this plan.

**Commit convention:** every commit message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Work happens on branch `topyeson` (already checked out).

---

## Task 1: `device_watch.rs` — pure decision state machine [MAC TDD]

The only unit-testable piece. Pure: device-name strings + an injected monotonic `now_ms` (no `Instant`, no cpal). Holds the last-rebuild timestamp for the anti-flap throttle.

**Files:**
- Create: `apps/native_helper_win/src/device_watch.rs`
- Modify: `apps/native_helper_win/src/lib.rs` (declare the module so it builds + `cargo test` runs on macOS)

- [ ] **Step 1: Declare the module in the crate lib**

In `src/lib.rs`, add alongside the existing `pub mod` lines (platform-agnostic, like `ipc`/`pcm` — NOT behind `#[cfg(windows)]`):

```rust
pub mod device_watch;
```

The lib's test harness compiles `device_watch` and its `#[cfg(test)]` tests on macOS regardless of `main.rs`; `main.rs` will bring it into scope via its `use yeson_win_audio_helper::{…}` line in Task 2.

- [ ] **Step 2: Write the module with tests (impl + tests land together — small module)**

`src/device_watch.rs`:

```rust
// === ANCHOR: WIN_DEVICE_WATCH_START ===
//! Pure default-device-change decision (no cpal/windows types, spec §5).
//! Inputs: the device we're currently capturing, the freshly re-queried default,
//! and a monotonic `now_ms`. Output: rebuild the loopback on the new default, or
//! ignore. A min-rebuild-interval throttle suppresses flapping (two devices
//! trading the default back and forth).

#[derive(Debug, PartialEq, Eq)]
pub enum Decision {
    /// Default differs from the active device and throttle allows → rebuild now.
    Rebuild,
    /// Same device, no default, or within throttle window → do nothing.
    Ignore,
}

pub struct DeviceWatcher {
    min_rebuild_interval_ms: u64,
    last_rebuild_ms: Option<u64>,
}

impl DeviceWatcher {
    pub fn new(min_rebuild_interval_ms: u64) -> Self {
        Self {
            min_rebuild_interval_ms,
            last_rebuild_ms: None,
        }
    }

    /// `active` = name of the device currently being captured.
    /// `polled` = freshly re-queried default output name (None if no default).
    /// `now_ms` = monotonic milliseconds.
    /// On a returned `Rebuild`, the throttle clock is stamped to `now_ms`.
    pub fn decide(&mut self, active: &str, polled: Option<&str>, now_ms: u64) -> Decision {
        let Some(polled) = polled else {
            return Decision::Ignore; // no default device to switch to
        };
        if polled == active {
            return Decision::Ignore; // unchanged
        }
        if let Some(last) = self.last_rebuild_ms {
            if now_ms.saturating_sub(last) < self.min_rebuild_interval_ms {
                return Decision::Ignore; // anti-flap throttle
            }
        }
        self.last_rebuild_ms = Some(now_ms);
        Decision::Rebuild
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const THROTTLE: u64 = 5_000;

    #[test]
    fn rebuilds_when_default_differs() {
        let mut w = DeviceWatcher::new(THROTTLE);
        assert_eq!(w.decide("Speakers", Some("Headphones"), 1_000), Decision::Rebuild);
    }

    #[test]
    fn ignores_when_same_device() {
        let mut w = DeviceWatcher::new(THROTTLE);
        assert_eq!(w.decide("Speakers", Some("Speakers"), 1_000), Decision::Ignore);
    }

    #[test]
    fn ignores_when_no_default() {
        let mut w = DeviceWatcher::new(THROTTLE);
        assert_eq!(w.decide("Speakers", None, 1_000), Decision::Ignore);
    }

    #[test]
    fn throttles_rapid_reswitch() {
        let mut w = DeviceWatcher::new(THROTTLE);
        // First switch at t=1000 → Rebuild (stamps 1000).
        assert_eq!(w.decide("Speakers", Some("Headphones"), 1_000), Decision::Rebuild);
        // A different default 2s later is within the 5s window → Ignore.
        assert_eq!(w.decide("Headphones", Some("Speakers"), 3_000), Decision::Ignore);
    }

    #[test]
    fn rebuilds_again_after_throttle_window() {
        let mut w = DeviceWatcher::new(THROTTLE);
        assert_eq!(w.decide("Speakers", Some("Headphones"), 1_000), Decision::Rebuild);
        // 6s later (> 5s window) → Rebuild allowed again.
        assert_eq!(w.decide("Headphones", Some("Speakers"), 7_000), Decision::Rebuild);
    }

    #[test]
    fn immediate_repoll_after_rebuild_is_ignored() {
        let mut w = DeviceWatcher::new(THROTTLE);
        assert_eq!(w.decide("Speakers", Some("Headphones"), 1_000), Decision::Rebuild);
        // Same poll value before `active` is updated by main → still throttled.
        assert_eq!(w.decide("Speakers", Some("Headphones"), 1_200), Decision::Ignore);
    }
}
// === ANCHOR: WIN_DEVICE_WATCH_END ===
```

- [ ] **Step 3: Run the tests on macOS**

Run: `cd apps/native_helper_win && cargo test device_watch`
Expected: 6 `device_watch` tests pass; existing ipc/pcm tests stay green. (For strict red-first: temporarily make `decide` always return `Ignore`, watch `rebuilds_when_default_differs` FAIL, then restore.)

- [ ] **Step 4: Confirm the crate still builds clean on macOS**

Run: `cd apps/native_helper_win && cargo build`
Expected: compiles (no cpal pulled on macOS; `device_watch` has no platform deps).

- [ ] **Step 5: Commit**

```bash
git add apps/native_helper_win/src/device_watch.rs apps/native_helper_win/src/lib.rs
git commit -m "feat(win-helper): pure device-change decision state machine"
```

---

## Task 2: `capture.rs` re-query helper + `main.rs` inline poll & rebuild [WIN]

Wire the decision into the worker loop and perform the synchronous in-process rebuild. cpal stays in `capture.rs`; `main.rs` calls `capture::current_default_device_name()` and `capture::start()`.

**Files:**
- Modify: `apps/native_helper_win/src/capture.rs`
- Modify: `apps/native_helper_win/src/main.rs` (`#[cfg(windows)] fn main` worker loop)

- [ ] **Step 1: Add the fresh-default re-query helper to `capture.rs`**

Inside the `WIN_CAPTURE` anchor, add (keeps all cpal usage in this module — `main.rs` never imports cpal traits):

```rust
/// Freshly re-query the current default output device name (each call hits
/// WASAPI `GetDefaultAudioEndpoint` — not cached). Used by the worker's device
/// poll. None if there is no default output device.
pub fn current_default_device_name() -> Option<String> {
    let host = cpal::default_host();
    host.default_output_device().and_then(|d| d.name().ok())
}
```

- [ ] **Step 2: Add poll + rebuild to the `main.rs` worker loop**

First bring the new module into scope: extend the existing import in the `#[cfg(windows)] fn main` from
`use yeson_win_audio_helper::{capture, ipc, pcm};` to
`use yeson_win_audio_helper::{capture, device_watch, ipc, pcm};`.

Then, in the `#[cfg(windows)] fn main`, (a) bind the capture handles as `mut`, (b) track the active device name + a poll clock + a `DeviceWatcher`, and (c) add a clock-gated poll/rebuild block at the top of the loop.

After `started` is emitted, replace the worker-loop preamble so the handles are reassignable:

```rust
    // Worker: drain raw blocks → pcm → 640B frames → stdout (write+flush).
    let mut conv = pcm::PcmConverter::new(fmt.sample_rate, fmt.channels);
    let mut capture = capture;          // make rebindable for in-process rebuild
    let mut rx = rx;
    let mut err_rx = err_rx;
    let mut active_device = fmt.device_name.clone();
    let mut last_dropped: u64 = 0;

    // Device-change polling (spec §3): re-query the default every ~1.5s and
    // rebuild the loopback in-process if it changed. Monotonic clock from a base
    // Instant → u64 ms for the pure DeviceWatcher.
    use std::time::Instant;
    const POLL_INTERVAL_MS: u64 = 1_500;
    const REBUILD_THROTTLE_MS: u64 = 5_000;
    const REBUILD_SETTLE_MS: u64 = 250;
    const REBUILD_MAX_ATTEMPTS: u32 = 4;
    let clock_base = Instant::now();
    let mut watcher = device_watch::DeviceWatcher::new(REBUILD_THROTTLE_MS);
    let mut last_poll_ms: u64 = 0;
```

Then, inside `loop {`, **before** the existing `dropped`/`stop`/`err_rx`/`rx.recv_timeout` block, insert the poll:

```rust
        // --- device-change poll (clock-gated; cheap, off the audio path) ---
        let now_ms = clock_base.elapsed().as_millis() as u64;
        if now_ms.saturating_sub(last_poll_ms) >= POLL_INTERVAL_MS {
            last_poll_ms = now_ms;
            let polled = capture::current_default_device_name();
            if device_watch::Decision::Rebuild
                == watcher.decide(&active_device, polled.as_deref(), now_ms)
            {
                let from = active_device.clone();
                // Drop the old stream, then re-open on the new default with retry
                // (the new device — e.g. BT — may not be ready immediately).
                // Reassigning capture/rx/err_rx drops the superseded receivers; we
                // never read them again, so their Disconnected is never observed
                // (spec §4 — synchronous swap, no epoch counter needed).
                let mut attempt = 0u32;
                let rebuilt = loop {
                    std::thread::sleep(std::time::Duration::from_millis(REBUILD_SETTLE_MS));
                    match capture::start() {
                        Ok(t) => break Some(t),
                        Err(_) => {
                            attempt += 1;
                            if attempt >= REBUILD_MAX_ATTEMPTS {
                                break None;
                            }
                        }
                    }
                };
                match rebuilt {
                    Some((new_cap, new_fmt, new_rx, new_err_rx)) => {
                        capture = new_cap;
                        rx = new_rx;
                        err_rx = new_err_rx;
                        conv = pcm::PcmConverter::new(new_fmt.sample_rate, new_fmt.channels);
                        last_dropped = 0; // new capture's drop counter starts at 0
                        active_device = new_fmt.device_name.clone();
                        ipc.emit_event(
                            "device_changed",
                            serde_json::json!({
                                "from": from,
                                "to": active_device,
                                "source_sample_rate": new_fmt.sample_rate,
                                "source_channels": new_fmt.channels,
                            }),
                        );
                        continue; // fresh rx next iteration
                    }
                    None => {
                        ipc.emit_event(
                            "fatal",
                            serde_json::json!({
                                "reason": "wasapi_init_failed",
                                "detail": "rebuild on new default device failed",
                            }),
                        );
                        std::process::exit(4);
                    }
                }
            }
        }
```

Leave the rest of the loop (dropped/stop/err_rx/rx.recv_timeout/`conv.push_f32`→`emit_chunk`) exactly as it is — it now operates on the rebound `capture`/`rx`/`err_rx`/`conv`.

> Note: `capture.dropped` is read in the existing block as `capture.dropped.load(...)` — after a rebuild `capture` is the new handle, so that keeps working. The `recv_timeout` `Disconnected` arm stays `fatal:stream_error`: it can now only fire for the *current* stream dying spontaneously (scenario ①), never for an intentional rebuild (we `continue` before reading the old rx).

- [ ] **Step 3: Build release on Windows**

Run: `cargo build --release` (on Windows, in `apps/native_helper_win`)
Expected: compiles clean.

- [ ] **Step 4: Manual smoke — event sequence + live switch**

Start a YouTube video, then run the helper piping stdout to a file so stderr is visible:

Run: `target\release\yeson-win-audio-helper.exe 1>chunks.bin`
Expected on stderr: `starting`, `started{device:A,…}`. While it runs, **switch the default output device** (Settings → Sound, or plug in headphones). Within ~1.5 s expect a `{"event":"device_changed","payload":{"from":"A","to":"B",…}}` line, and `chunks.bin` keeps growing. Ctrl-C → clean exit.

- [ ] **Step 5: Commit**

```bash
git add apps/native_helper_win/src/capture.rs apps/native_helper_win/src/main.rs
git commit -m "feat(win-helper): poll default output device + in-process rebuild on change"
```

---

## Task 3: Windows E2E — device switch mid-meeting, no regression [WIN]

Validates the reported bug is fixed end-to-end and the prior behaviors are intact.

**Prereqs:** same as the Phase 2 E2E (device API key, session UUID, `external_id`, operator JWT, reachable server). Helper built `--release` (Task 2). Sidecar launched pinned to the native helper (mirror the Phase 2 plan Task 7 Step 1 env: `SERVER_WS_BASE`, `YESON_DEVICE_API_KEY`, `YESON_SESSION_ID`, `YESON_AUDIO_PROVIDER=native`, `YESON_NATIVE_HELPER_BIN=…\yeson-win-audio-helper.exe`).

- [ ] **Step 1: Baseline subtitles on the starting device**

Play 30 s of English audio on the current default output → confirm Korean subtitles in the viewer and `chunks_per_sec_1s ≈ 50` via `GET /api/v1/sessions/<external_id>/audio_stats` (operator bearer).

- [ ] **Step 2: Switch the default output mid-meeting (the core fix)**

While audio keeps playing, switch the Windows default output device (plug in headphones/BT, or Settings → Sound → output). Route the same audio to the new device.
Expected:
- sidecar log shows `native helper event: {'event': 'device_changed', 'payload': {'from': …, 'to': …}}` within ~1.5 s,
- subtitles **resume** within a couple seconds (brief gap acceptable),
- `audio_stats` `total_chunks` keeps rising after the switch.

- [ ] **Step 3: Symmetric switch back**

Switch the default back to the original device. Expect a second `device_changed` and subtitles continue.

- [ ] **Step 4: Regression — device *removal* still fatals (scenario ①, spec §7)**

With the helper capturing, **remove/disable the active default device entirely** (no fallback). Expect the existing behavior: a `fatal:stream_error` (cpal invalidation) and helper exit — NOT a silent hang. This confirms slice (a) didn't alter the removal path.

- [ ] **Step 5: Regression — orphan cleanup intact (Phase 2b ①)**

After a device switch, pause audio (true silence) → `Stop-Process -Name <bundled sidecar/python> -Force` → `Get-Process yeson-win-audio-helper -ErrorAction SilentlyContinue` must list nothing (Job Object still reaps the rebuilt helper — the rebuild replaced the cpal stream, not the process, so the job binding from `_spawn` still holds).

- [ ] **Step 6: Record results**

Note in the spec §9 checklist: switch latency feel, `device_changed` count vs switch count (debounce sanity), regression results for removal-fatal and orphan-cleanup.

---

## Task 4: Docs sync [MAC]

Per the "docs after each slice" rule.

**Files:**
- Modify: `docs/NATIVE_DESKTOP_HELPER_PLAN.md` (Phase 2b section)
- Modify: `docs/ROADMAP.md` (Native track lines)

- [ ] **Step 1: Mark Phase 2b ② in the native plan**

In `docs/NATIVE_DESKTOP_HELPER_PLAN.md`, add a dated `> 📌 (2026-06-10)` callout under the Phase 2b notes: device_watch landed (polling, in-process rebuild, slice (a) only), E2E result, and that removal-self-heal (b) remains deferred.

- [ ] **Step 2: Update the ROADMAP native-track line**

In `docs/ROADMAP.md`, add a 2026-06-10 line recording Phase 2b ② (default-device-change tracking) done, with the (b) extension noted as future.

- [ ] **Step 3: Commit**

```bash
git add docs/NATIVE_DESKTOP_HELPER_PLAN.md docs/ROADMAP.md
git commit -m "docs(native-audio): record Phase 2b ② device-change tracking"
```

---

## Self-Review (author checklist)

**Spec coverage:**
- §0 bug distinction (fix demotion ②, leave removal ① fatal) → Task 2 logic + Task 3 Steps 2/4. ✓
- §1 polling, inline, ~1.5s, name compare, no new crate → Tasks 1,2. ✓
- §2 in-process invariant (stdout open, sidecar/server untouched) → Task 2 (rebuild swaps cpal stream only) + Task 3 Step 5. ✓
- §3 flow (clock-gated poll → decide → rebuild → device_changed) → Task 2 Step 2. ✓
- §4 superseded-stream "expected death" not fatal → achieved structurally by synchronous swap + `continue` (no read of old rx); documented in Task 2 note. ✓
- §5 module isolation (device_watch no cpal/windows; cpal stays in capture.rs) → Task 1 + Task 2 Step 1. ✓
- §6 polling cost/freshness assumption → Task 2 helper (fresh re-query) + Task 3 (hardware confirms switch detected). ✓
- §7 non-scope: (b) removal self-heal NOT implemented; UI/no_audio watchdog/input-device excluded. ✓
- §8 risks: rebuild race→synchronous swap; BT-not-ready→settle+retry; flapping→throttle; format change→conv rebuilt; null default→Ignore. ✓
- §9 deliverables → Tasks map 1:1. ✓

**Type consistency:** `DeviceWatcher::new(u64)`, `decide(&str, Option<&str>, u64) -> Decision`; `capture::current_default_device_name() -> Option<String>`; `capture::start() -> (Capture, CaptureFormat, Receiver<RawBlock>, Receiver<String>)` reused unchanged; `PcmConverter::new(u32,u16)`. Consistent across Tasks 1–2. ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code; commands have expected output. ✓

**Mac-testability:** only `device_watch.rs` is unit-tested (pure). The poll/rebuild wiring is `#[cfg(windows)]` and verified by Task 3 hardware E2E — matches the crate's existing Mac-cargo-test / Windows-HW split.

---

## Execution Handoff

**Plan saved to `docs/superpowers/plans/2026-06-10-windows-default-device-watch.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent for Task 1 (Mac TDD) here, review, then hand Tasks 2–3 to the Windows machine.

**2. Inline Execution** — execute Task 1 in this session (Mac `cargo test`), commit, then move to Windows for Tasks 2–3.

Note: Task 1 + Task 4 are **[MAC]** (run here). Tasks 2–3 are **[WIN]** (cpal rebuild + device switching + E2E run on your Windows machine/VM). Natural split: land + `cargo test` the pure `device_watch.rs` here, then build and E2E on Windows.
