# Windows WASAPI Native Audio Helper — Implementation Plan (Phase 2, 1차 PoC)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Windows `.exe` that captures system audio via WASAPI loopback and emits the exact same stdout-PCM + stderr-JSON contract as the macOS helper, so the Python sidecar's `NativePipeSource` is reused unchanged.

**Architecture:** A standalone Rust crate `apps/native_helper_win`. cpal opens the default output device as a loopback input stream; its real-time callback only enqueues raw frames into a bounded channel. A worker thread drains the channel, downmixes + resamples to 16 kHz mono s16le (`pcm.rs`), frames into 640-byte chunks, and writes+flushes them to stdout (`ipc.rs`). All failures converge to a `fatal` stderr event + non-zero exit; `panic = "abort"` guarantees a worker panic aborts the process; a broken stdout pipe (parent gone) exits promptly to avoid orphaning.

**Tech Stack:** Rust (edition 2021), `cpal` 0.15+ (WASAPI loopback, Windows-only dep), `rubato` (arbitrary-ratio resampling), `serde_json` (events), `ctrlc` (graceful Ctrl-C for manual testing). Python sidecar (`uv`) for E2E only.

---

## Environment Legend

Each task says where its steps run. The dev machine is macOS; WASAPI capture only exists on Windows.

- **[MAC]** — runs on the macOS dev machine (`cargo test` of pure modules, pytest, docs).
- **[WIN]** — runs on a real Windows 10/11 x86_64 machine or VM (cpal capture, smoke, E2E).
- **[BOTH]** — compiles/runs on either.

Module-isolation rule (from spec §4): `ipc.rs` and `pcm.rs` must NOT import any `cpal` type, so they compile and unit-test on macOS. `capture.rs` is `#[cfg(windows)]` and imports cpal. `main.rs` has a `#[cfg(windows)]` real entry and a `#[cfg(not(windows))]` stub so the crate builds on macOS.

---

## File Structure

```
apps/native_helper_win/
  .gitignore            # /target
  Cargo.toml            # crate + deps; cpal is a [target.'cfg(windows)'] dep; release panic=abort
  src/
    main.rs             # #[cfg(windows)] real entry + #[cfg(not(windows))] stub; module decls
    ipc.rs              # [MAC-testable] ByteSink trait, JSON event emitter, PCM writer+flush
    pcm.rs              # [MAC-testable] input→f32 normalize, downmix, rubato resample, 640B framing
    capture.rs          # #[cfg(windows)] cpal loopback → bounded channel of RawBlock
  scripts/
    (build-release.ps1 is Phase 2b — not in this plan)
```

Python touch (1차 dev convenience):
- `apps/client_sidecar/config/audio.py` — `NATIVE_HELPER_BIN_PATH` Windows dev default branch (anchor `AUDIO_PROVIDER`).

Out of scope for this plan (Phase 2b, separate plan): `device_watch.rs`, `scripts/build-release.ps1`, `package.json` `build:native-helper-win`, `sidecar.rs::locate_bundled_native_helper()` Windows x86_64, `tauri.windows.conf.json` externalBin.

**Commit convention:** every commit message ends with the repo footer line `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` (omitted from the short messages below for brevity — add it). Work happens on branch `topyeson` (already checked out).

---

## Task 0: cpal loopback spike — GATE [WIN]

This is an exploratory spike, not TDD. It proves cpal's WASAPI loopback contract on real hardware **before** any production code. If it fails, stop and redesign around the raw `windows`/`wasapi` crate (spec §4) — do not proceed to Task 1 on the cpal assumption.

**Files:**
- No repo file. Use a scratch crate only; record the result back into the spec checklist.

- [ ] **Step 1: Scaffold a throwaway crate just for the spike**

On the Windows machine, in a scratch dir (not the repo yet), run:

```
cargo new --bin wasapi_spike
cd wasapi_spike
cargo add cpal@0.15
```

- [ ] **Step 2: Write the spike**

`src/main.rs`:

```rust
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::SampleFormat;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

fn main() {
    let host = cpal::default_host();
    let device = host
        .default_output_device()
        .expect("no default output device");
    println!("device: {:?}", device.name());

    // Loopback: build an INPUT stream on the OUTPUT device.
    let config = device
        .default_output_config()
        .expect("no default output config");
    println!("format: {:?}", config);
    if config.sample_format() != SampleFormat::F32 {
        println!(
            "NO-GO for this 1차 plan: sample_format={:?}; amend RawBlock normalization first",
            config.sample_format()
        );
        return;
    }

    let count = Arc::new(AtomicU64::new(0));
    let samples = Arc::new(AtomicU64::new(0));
    let c2 = count.clone();
    let s2 = samples.clone();
    let stream = device
        .build_input_stream(
            &config.clone().into(),
            move |data: &[f32], _| {
                c2.fetch_add(1, Ordering::Relaxed);
                s2.fetch_add(data.len() as u64, Ordering::Relaxed);
            },
            |err| eprintln!("stream error: {err}"),
            None,
        )
        .expect("build_input_stream (loopback) failed");
    stream.play().expect("play failed");

    println!("Play audio in a browser/Zoom now. Observing 10s...");
    std::thread::sleep(std::time::Duration::from_secs(10));
    println!("total callbacks: {}", count.load(Ordering::Relaxed));
    println!("total samples: {}", samples.load(Ordering::Relaxed));
}
```

- [ ] **Step 3: Run with audio playing**

Start a YouTube video, then:

Run: `cargo run`
Expected: prints device name, a `format:` line (record the `sample_rate`, `channels`, `sample_format` — e.g. `48000`, `2`, `F32`), `total callbacks` > 0, and `total samples` > 0. If the format is not F32, the spike exits with the NO-GO message from Step 2.

- [ ] **Step 4: Observe silence behavior**

Pause the video, run again, watch callbacks during silence. Record whether callbacks stop (expected: WASAPI loopback delivers no/zero packets during silence — acceptable per spec §8).

- [ ] **Step 5: Record findings + go/no-go**

Append to the spec's deliverable checklist (`docs/superpowers/specs/2026-05-28-windows-wasapi-helper-design.md` §9 Task 0 item) a short note: device name, source sample_rate / channels / sample_format, callbacks-per-second observed, silence behavior, and **GO** (cpal loopback works) or **NO-GO** (switch to `wasapi` crate).

Gate: only continue to Task 1 on **GO + F32** for this 1차 PoC plan. If the observed `sample_format` is `I16`/`U16`, pause before Task 3 and amend Tasks 3–5 to use a `RawBlock` enum + `push_i16`/`push_u16` normalization path. Any other format is **NO-GO** for this plan until the capture/PCM design is revised.

---

## Task 1: Crate scaffold [BOTH]

**Files:**
- Create: `apps/native_helper_win/Cargo.toml`
- Create: `apps/native_helper_win/.gitignore`
- Create: `apps/native_helper_win/src/main.rs`
- Create: `apps/native_helper_win/src/ipc.rs` (empty stub this task)
- Create: `apps/native_helper_win/src/pcm.rs` (empty stub this task)
- Create: `apps/native_helper_win/src/capture.rs` (empty stub this task)

- [ ] **Step 1: Write `Cargo.toml`**

```toml
[package]
name = "yeson-win-audio-helper"
version = "0.1.0"
edition = "2021"
description = "Windows WASAPI loopback audio helper for yeson-meet (stdout PCM, stderr JSON)"

[[bin]]
name = "yeson-win-audio-helper"
path = "src/main.rs"

[dependencies]
serde_json = "1"
rubato = "0.15"

# cpal + ctrlc only on Windows so macOS `cargo test` of pure modules stays lean.
[target.'cfg(windows)'.dependencies]
cpal = "0.15"
ctrlc = "3"

# A worker-thread panic must abort the whole process AFTER the panic hook
# emits `fatal` (spec §3 panic propagation).
[profile.release]
panic = "abort"
```

- [ ] **Step 2: Write `.gitignore`**

```
/target
```

- [ ] **Step 3: Write `src/main.rs` scaffold (module decls + dual entry)**

```rust
// yeson-win-audio-helper: Windows WASAPI loopback → stdout 16k mono s16le PCM.
// Pure modules (ipc, pcm) build everywhere; capture is Windows-only.
mod ipc;
mod pcm;
#[cfg(windows)]
mod capture;

#[cfg(not(windows))]
fn main() {
    eprintln!("yeson-win-audio-helper is Windows-only");
    std::process::exit(2);
}

#[cfg(windows)]
fn main() {
    // Real entry implemented in Task 5.
    eprintln!("not yet implemented");
    std::process::exit(2);
}
```

- [ ] **Step 4: Write empty module stubs**

`src/ipc.rs`:
```rust
// === ANCHOR: WIN_IPC_START ===
// IPC implemented in Task 2.
// === ANCHOR: WIN_IPC_END ===
```

`src/pcm.rs`:
```rust
// === ANCHOR: WIN_PCM_START ===
// PCM conversion implemented in Task 3.
// === ANCHOR: WIN_PCM_END ===
```

`src/capture.rs`:
```rust
// === ANCHOR: WIN_CAPTURE_START ===
// cpal loopback capture implemented in Task 4 (Windows-only).
// === ANCHOR: WIN_CAPTURE_END ===
```

- [ ] **Step 5: Verify it builds on macOS**

Run: `cd apps/native_helper_win && cargo build`
Expected: compiles clean (cpal/ctrlc not pulled on macOS; only serde_json + rubato).

- [ ] **Step 6: Commit**

```bash
git add apps/native_helper_win
git commit -m "feat(win-helper): scaffold Windows WASAPI helper crate"
```

---

## Task 2: `ipc.rs` — stdout PCM + stderr JSON events [MAC TDD]

Mirrors `apps/native_helper_mac/.../IPC.swift`: stdout = binary PCM, stderr = one JSON object per line. Pure (no cpal). Writer flushes after every write (spec §8 stdout-buffering risk).

**Files:**
- Modify: `apps/native_helper_win/src/ipc.rs`

- [ ] **Step 1: Write failing tests**

Replace `src/ipc.rs` body with the tests first (implementation comes in Step 3):

```rust
// === ANCHOR: WIN_IPC_START ===
use std::io::{self, Write};

/// A flushable byte sink. Abstracted so tests use an in-memory buffer
/// and production uses locked stdout/stderr.
pub trait ByteSink {
    fn write_all(&mut self, data: &[u8]) -> io::Result<()>;
    fn flush(&mut self) -> io::Result<()>;
}

impl<W: Write> ByteSink for W {
    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        Write::write_all(self, data)
    }
    fn flush(&mut self) -> io::Result<()> {
        Write::flush(self)
    }
}

/// stdout = PCM binary stream, stderr = JSON line events.
pub struct Ipc<D: ByteSink, C: ByteSink> {
    data: D,
    control: C,
}

impl<D: ByteSink, C: ByteSink> Ipc<D, C> {
    pub fn new(data: D, control: C) -> Self {
        Self { data, control }
    }

    /// Write one PCM chunk to the data sink and flush immediately.
    /// Returns the io::Result so the caller can detect a broken pipe.
    pub fn emit_chunk(&mut self, chunk: &[u8]) -> io::Result<()> {
        self.data.write_all(chunk)?;
        self.data.flush()
    }

    /// Emit a `{"event":name,"payload":...}` line + '\n' to the control sink.
    pub fn emit_event(&mut self, name: &str, payload: serde_json::Value) {
        let obj = serde_json::json!({ "event": name, "payload": payload });
        // Best-effort: if stderr is gone there is nothing useful to do.
        if let Ok(mut line) = serde_json::to_vec(&obj) {
            line.push(b'\n');
            let _ = self.control.write_all(&line);
            let _ = self.control.flush();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn emit_chunk_passes_bytes_through() {
        let mut data = Vec::new();
        let mut ctrl = Vec::new();
        {
            let mut ipc = Ipc::new(&mut data, &mut ctrl);
            ipc.emit_chunk(&[1, 2, 3, 4]).unwrap();
        }
        assert_eq!(data, vec![1, 2, 3, 4]);
        assert!(ctrl.is_empty());
    }

    #[test]
    fn emit_event_writes_one_json_line() {
        let mut data = Vec::new();
        let mut ctrl = Vec::new();
        {
            let mut ipc = Ipc::new(&mut data, &mut ctrl);
            ipc.emit_event("started", serde_json::json!({"source_sample_rate": 48000}));
        }
        assert!(data.is_empty());
        assert_eq!(*ctrl.last().unwrap(), b'\n');
        let v: serde_json::Value = serde_json::from_slice(&ctrl[..ctrl.len() - 1]).unwrap();
        assert_eq!(v["event"], "started");
        assert_eq!(v["payload"]["source_sample_rate"], 48000);
    }

    #[test]
    fn fatal_event_shape_matches_contract() {
        let mut data = Vec::new();
        let mut ctrl = Vec::new();
        {
            let mut ipc = Ipc::new(&mut data, &mut ctrl);
            ipc.emit_event(
                "fatal",
                serde_json::json!({"reason": "wasapi_init_failed", "detail": "x"}),
            );
        }
        let v: serde_json::Value = serde_json::from_slice(&ctrl[..ctrl.len() - 1]).unwrap();
        assert_eq!(v["event"], "fatal");
        assert_eq!(v["payload"]["reason"], "wasapi_init_failed");
    }
}
// === ANCHOR: WIN_IPC_END ===
```

- [ ] **Step 2: Run tests to verify they pass**

The implementation is inline above (this module is small enough that test + impl land together). Run:

Run: `cd apps/native_helper_win && cargo test`
Expected: 3 tests pass. (If you prefer strict red-first, temporarily stub `emit_event` to a no-op, watch `emit_event_writes_one_json_line` FAIL, then restore.)

- [ ] **Step 3: Commit**

```bash
git add apps/native_helper_win/src/ipc.rs
git commit -m "feat(win-helper): ipc stdout PCM + stderr JSON events"
```

---

## Task 3: `pcm.rs` — downmix + resample + 640-byte framing [MAC TDD]

The core pure module. Input: interleaved samples (f32 real path; i16 defensive) at a source rate/channel-count. Output: complete 640-byte frames of 16 kHz mono s16le, buffering any remainder across calls. Pure (no cpal). u16 is intentionally omitted (WASAPI shared-mode mix format is f32 in practice; add later only if Task 0 observed it — YAGNI).

**Files:**
- Modify: `apps/native_helper_win/src/pcm.rs`

- [ ] **Step 1: Write the failing tests**

Put these at the bottom of `src/pcm.rs` (implementation in Step 3):

```rust
#[cfg(test)]
mod tests {
    use super::*;

    const FRAME_BYTES: usize = 640;

    // ~1s of 48k stereo f32 → ~16000 mono samples → ~50 frames of 640B.
    #[test]
    fn resamples_48k_stereo_to_16k_mono_frames() {
        let mut conv = PcmConverter::new(48_000, 2);
        let frames_in = 48_000; // 1 second
        let mut interleaved = Vec::with_capacity(frames_in * 2);
        for n in 0..frames_in {
            let s = (n as f32 * 0.01).sin() * 0.5;
            interleaved.push(s); // L
            interleaved.push(s); // R
        }
        let out = conv.push_f32(&interleaved);
        // Every emitted buffer is exactly one 640-byte frame.
        assert!(out.iter().all(|f| f.len() == FRAME_BYTES));
        // ~16000 samples / 320 per frame ≈ 50 frames (allow resampler edge tolerance).
        assert!(
            (45..=52).contains(&out.len()),
            "got {} frames",
            out.len()
        );
    }

    #[test]
    fn carries_remainder_across_calls() {
        let mut conv = PcmConverter::new(16_000, 1); // no resample, mono
        // 100 mono samples < 320 → 0 full frames, remainder buffered.
        let chunk: Vec<f32> = (0..100).map(|_| 0.1).collect();
        assert_eq!(conv.push_f32(&chunk).len(), 0);
        // Feed enough to cross 320 total → exactly one frame.
        let chunk2: Vec<f32> = (0..240).map(|_| 0.1).collect();
        let out = conv.push_f32(&chunk2);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].len(), FRAME_BYTES);
    }

    #[test]
    fn downmixes_stereo_to_mono_average() {
        let mut conv = PcmConverter::new(16_000, 2);
        // L=+0.5, R=-0.5 → mono 0.0. 320 frames → exactly one silent 640B frame.
        let mut interleaved = Vec::new();
        for _ in 0..320 {
            interleaved.push(0.5);
            interleaved.push(-0.5);
        }
        let out = conv.push_f32(&interleaved);
        assert_eq!(out.len(), 1);
        assert!(out[0].iter().all(|&b| b == 0), "expected silence");
    }

    #[test]
    fn quantizes_full_scale_little_endian() {
        let mut conv = PcmConverter::new(16_000, 1);
        let chunk: Vec<f32> = (0..320).map(|_| 1.0).collect(); // +full scale
        let out = conv.push_f32(&chunk);
        assert_eq!(out.len(), 1);
        // i16 32767 = 0x7FFF, little-endian → [0xFF, 0x7F].
        assert_eq!(&out[0][0..2], &[0xFF, 0x7F]);
    }

    #[test]
    fn i16_input_matches_f32_path() {
        let mut conv = PcmConverter::new(16_000, 1);
        let chunk: Vec<i16> = (0..320).map(|_| 8192).collect(); // 0.25 full-scale
        let out = conv.push_i16(&chunk);
        assert_eq!(out.len(), 1);
        // 8192/32768 = 0.25 → 0.25*32767 = 8191.75 → f32::round (half away from
        // zero) → 8192 = 0x2000 → LE [0x00, 0x20].
        assert_eq!(&out[0][0..2], &[0x00, 0x20]);
    }

    #[test]
    fn handles_44100_and_96000() {
        for rate in [44_100u32, 96_000u32] {
            let mut conv = PcmConverter::new(rate, 2);
            let mut interleaved = Vec::new();
            for n in 0..rate as usize {
                let s = (n as f32 * 0.01).sin() * 0.3;
                interleaved.push(s);
                interleaved.push(s);
            }
            let out = conv.push_f32(&interleaved);
            // ~1s → ~16000 samples → ~50 frames; loose bound for edge effects.
            assert!((44..=53).contains(&out.len()), "rate {rate}: {} frames", out.len());
        }
    }
}
```

- [ ] **Step 2: Run tests to verify they FAIL**

Run: `cd apps/native_helper_win && cargo test`
Expected: FAIL to compile — `PcmConverter` not defined.

- [ ] **Step 3: Write the implementation**

Put this ABOVE the test module in `src/pcm.rs`:

```rust
// === ANCHOR: WIN_PCM_START ===
//! Interleaved source samples → 16 kHz mono s16le, framed to 640 bytes.
//! No cpal types (module-isolation rule, spec §4): inputs are plain slices + ints.
use rubato::{
    Resampler, SincFixedIn, SincInterpolationParameters, SincInterpolationType, WindowFunction,
};

const TARGET_RATE: usize = 16_000;
const FRAME_SAMPLES: usize = 320; // 20 ms @ 16 kHz
const FRAME_BYTES: usize = FRAME_SAMPLES * 2; // 640
const RESAMPLER_CHUNK: usize = 1024; // input frames per rubato call

pub struct PcmConverter {
    source_rate: usize,
    channels: usize,
    resampler: Option<SincFixedIn<f32>>,
    in_mono: Vec<f32>,     // downmixed mono awaiting resample (source rate)
    out_i16le: Vec<u8>,    // 16k s16le bytes awaiting framing
}

impl PcmConverter {
    pub fn new(source_rate: u32, channels: u16) -> Self {
        let source_rate = source_rate as usize;
        let channels = channels.max(1) as usize;
        let resampler = if source_rate == TARGET_RATE {
            None // passthrough, no resampling needed
        } else {
            let params = SincInterpolationParameters {
                sinc_len: 256,
                f_cutoff: 0.95,
                interpolation: SincInterpolationType::Linear,
                oversampling_factor: 256,
                window: WindowFunction::BlackmanHarris2,
            };
            Some(
                SincFixedIn::<f32>::new(
                    TARGET_RATE as f64 / source_rate as f64, // resample_ratio
                    2.0,                                     // max relative
                    params,
                    RESAMPLER_CHUNK,
                    1, // mono (after downmix)
                )
                .expect("rubato resampler init"),
            )
        };
        Self {
            source_rate,
            channels,
            resampler,
            in_mono: Vec::new(),
            out_i16le: Vec::new(),
        }
    }

    /// Feed interleaved f32 frames. Returns any complete 640-byte frames.
    pub fn push_f32(&mut self, interleaved: &[f32]) -> Vec<[u8; FRAME_BYTES]> {
        self.downmix_into_mono(interleaved);
        self.drain_to_frames()
    }

    /// Feed interleaved i16 frames (normalized to f32 internally).
    pub fn push_i16(&mut self, interleaved: &[i16]) -> Vec<[u8; FRAME_BYTES]> {
        let as_f32: Vec<f32> = interleaved.iter().map(|&s| s as f32 / 32768.0).collect();
        self.push_f32(&as_f32)
    }

    fn downmix_into_mono(&mut self, interleaved: &[f32]) {
        let ch = self.channels;
        // cpal normally delivers whole interleaved frames; a non-aligned buffer
        // would make chunks_exact silently DROP the trailing partial frame
        // (slow desync). Assert the assumption (no-op in release, spec §4).
        debug_assert!(
            interleaved.len() % ch == 0,
            "callback buffer not frame-aligned: len={} channels={}",
            interleaved.len(),
            ch
        );
        for frame in interleaved.chunks_exact(ch) {
            let sum: f32 = frame.iter().sum();
            self.in_mono.push(sum / ch as f32);
        }
    }

    fn drain_to_frames(&mut self) -> Vec<[u8; FRAME_BYTES]> {
        match self.resampler.as_mut() {
            None => {
                // Passthrough: quantize all mono samples directly.
                for &s in &self.in_mono {
                    push_i16le(&mut self.out_i16le, s);
                }
                self.in_mono.clear();
            }
            Some(rs) => {
                while self.in_mono.len() >= RESAMPLER_CHUNK {
                    let block: Vec<f32> = self.in_mono.drain(..RESAMPLER_CHUNK).collect();
                    let out = rs.process(&[block], None).expect("rubato process");
                    for &s in &out[0] {
                        push_i16le(&mut self.out_i16le, s);
                    }
                }
            }
        }
        self.take_full_frames()
    }

    fn take_full_frames(&mut self) -> Vec<[u8; FRAME_BYTES]> {
        let mut frames = Vec::new();
        while self.out_i16le.len() >= FRAME_BYTES {
            let mut frame = [0u8; FRAME_BYTES];
            frame.copy_from_slice(&self.out_i16le[..FRAME_BYTES]);
            frames.push(frame);
            self.out_i16le.drain(..FRAME_BYTES);
        }
        frames
    }
}

fn push_i16le(buf: &mut Vec<u8>, sample_f32: f32) {
    let clamped = sample_f32.clamp(-1.0, 1.0);
    let v = (clamped * 32767.0).round() as i16;
    buf.extend_from_slice(&v.to_le_bytes());
}
// === ANCHOR: WIN_PCM_END ===
```

- [ ] **Step 4: Run tests to verify they PASS**

Run: `cd apps/native_helper_win && cargo test`
Expected: all 6 pcm tests pass and the existing ipc tests remain green.

Note: `SincFixedIn` requires exactly `RESAMPLER_CHUNK` input frames per `process()` call — the `while … drain(..RESAMPLER_CHUNK)` loop guarantees this. If your installed `rubato` version's `SincInterpolationParameters`/`new` signature differs, adapt to that version's API; the **tests are the behavioral contract** and must stay green.

- [ ] **Step 5: Commit**

```bash
git add apps/native_helper_win/src/pcm.rs
git commit -m "feat(win-helper): pcm downmix+resample+640B framing"
```

---

## Task 4: `capture.rs` — cpal WASAPI loopback [WIN]

Windows-only. Opens the default output device as a loopback input stream. The real-time callback does **nothing but enqueue** raw f32 frames into a bounded channel (spec §4 callback-safety). No resample/JSON/stdout in the callback.

**Files:**
- Modify: `apps/native_helper_win/src/capture.rs`

- [ ] **Step 1: Write the implementation**

```rust
// === ANCHOR: WIN_CAPTURE_START ===
//! cpal WASAPI loopback (Windows-only). Callback enqueues raw frames only.
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::Stream;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{channel, sync_channel, Receiver, SyncSender, TrySendError};
use std::sync::Arc;

/// Captured source format, reported in the `started` event.
pub struct CaptureFormat {
    pub device_name: String,
    pub sample_rate: u32,
    pub channels: u16,
}

pub enum CaptureError {
    NoDefaultRenderDevice,
    WasapiInitFailed(String),
    UnsupportedFormat(String),
}

/// One raw block of interleaved f32 frames from the audio callback.
pub type RawBlock = Vec<f32>;

pub struct Capture {
    _stream: Stream, // kept alive; dropping stops capture
    pub dropped: Arc<AtomicU64>,
}

/// Start loopback. Returns the live stream handle, source format, a receiver of
/// raw f32 blocks, and a receiver for asynchronous stream errors. Bounded audio
/// channel: on overflow the callback drops the newest block and bumps `dropped`
/// (explicit drop policy, never silent). Stream errors are unbounded because they
/// must always reach main and become a `fatal` event.
pub fn start() -> Result<(Capture, CaptureFormat, Receiver<RawBlock>, Receiver<String>), CaptureError> {
    let host = cpal::default_host();
    let device = host
        .default_output_device()
        .ok_or(CaptureError::NoDefaultRenderDevice)?;
    let device_name = device.name().unwrap_or_else(|_| "unknown".into());

    let supported = device
        .default_output_config()
        .map_err(|e| CaptureError::WasapiInitFailed(e.to_string()))?;
    let sample_rate = supported.sample_rate().0;
    let channels = supported.channels();

    if supported.sample_format() != cpal::SampleFormat::F32 {
        // Real WASAPI shared-mode mix format is F32; bail loudly otherwise so
        // pcm.rs assumptions never silently break.
        return Err(CaptureError::UnsupportedFormat(format!(
            "{:?}",
            supported.sample_format()
        )));
    }

    let (tx, rx): (SyncSender<RawBlock>, Receiver<RawBlock>) = sync_channel(128);
    let (err_tx, err_rx) = channel::<String>();
    let dropped = Arc::new(AtomicU64::new(0));
    let dropped_cb = dropped.clone();

    let stream = device
        .build_input_stream(
            &supported.into(),
            move |data: &[f32], _| {
                match tx.try_send(data.to_vec()) {
                    Ok(()) => {}
                    Err(TrySendError::Full(_)) => {
                        dropped_cb.fetch_add(1, Ordering::Relaxed);
                    }
                    Err(TrySendError::Disconnected(_)) => {}
                }
            },
            move |err| {
                let _ = err_tx.send(err.to_string());
            },
            None,
        )
        .map_err(|e| CaptureError::WasapiInitFailed(e.to_string()))?;
    stream
        .play()
        .map_err(|e| CaptureError::WasapiInitFailed(e.to_string()))?;

    Ok((
        Capture { _stream: stream, dropped },
        CaptureFormat { device_name, sample_rate, channels },
        rx,
        err_rx,
    ))
}
// === ANCHOR: WIN_CAPTURE_END ===
```

- [ ] **Step 2: Write a Windows smoke test**

Add to the bottom of `src/capture.rs`:

```rust
#[cfg(all(test, windows))]
mod smoke {
    use super::*;
    use std::time::Duration;

    // Requires audio playing on the default output. Manual/CI-gated.
    #[test]
    #[ignore]
    fn loopback_delivers_at_least_one_block() {
        let (_cap, fmt, rx, _err_rx) = start().expect("start loopback");
        eprintln!("device={} rate={} ch={}", fmt.device_name, fmt.sample_rate, fmt.channels);
        let block = rx
            .recv_timeout(Duration::from_secs(5))
            .expect("no audio block in 5s (is audio playing?)");
        assert!(!block.is_empty());
    }
}
```

- [ ] **Step 3: Build on Windows**

Run: `cargo build` (on Windows, in `apps/native_helper_win`)
Expected: compiles with cpal/ctrlc.

- [ ] **Step 4: Run the smoke test with audio playing**

Start a YouTube video, then:

Run: `cargo test loopback_delivers_at_least_one_block -- --ignored --nocapture`
Expected: prints `device=… rate=… ch=…`, the test PASSES (received a block within 5s).

- [ ] **Step 5: Commit**

```bash
git add apps/native_helper_win/src/capture.rs
git commit -m "feat(win-helper): cpal WASAPI loopback capture into bounded channel"
```

---

## Task 5: `main.rs` — integrate lifecycle, worker, fatal/panic/broken-pipe [WIN]

Wires capture → worker → ipc. Implements the spec's hard guarantees: fatal-on-all-failure, `panic = "abort"` propagation, broken-pipe → prompt exit, Ctrl-C → `stopping`.

**Files:**
- Modify: `apps/native_helper_win/src/main.rs`

- [ ] **Step 1: Write the Windows entry**

Replace the `#[cfg(windows)] fn main()` stub with:

```rust
#[cfg(windows)]
fn main() {
    use std::io::{self, Write};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;

    const VERSION: &str = env!("CARGO_PKG_VERSION");

    // stderr-only IPC available inside the panic hook (stdout may be mid-write).
    std::panic::set_hook(Box::new(|info| {
        let mut err = io::stderr();
        let line = format!(
            "{{\"event\":\"fatal\",\"payload\":{{\"reason\":\"panic\",\"detail\":\"{}\"}}}}\n",
            info.to_string().replace('"', "'").replace('\n', " ")
        );
        let _ = err.write_all(line.as_bytes());
        let _ = err.flush();
        // [profile.release] panic="abort" aborts the process after this hook.
        // Debug builds unwind instead; smoke/E2E/Task 5b all run --release so the
        // contract holds. Hook is stderr-only → never deadlocks on the stdout lock.
    }));

    // stdout lock is held for the whole program (ipc data sink). Any println!/print!
    // while it is held would DEADLOCK — all output must go through `ipc` (spec §4).
    let mut ipc = ipc::Ipc::new(io::stdout().lock(), io::stderr());
    ipc.emit_event("starting", serde_json::json!({ "version": VERSION }));

    let (capture, fmt, rx, err_rx) = match capture::start() {
        Ok(t) => t,
        Err(e) => {
            let (reason, detail) = match e {
                capture::CaptureError::NoDefaultRenderDevice => {
                    ("no_default_render_device".to_string(), String::new())
                }
                capture::CaptureError::WasapiInitFailed(d) => ("wasapi_init_failed".to_string(), d),
                capture::CaptureError::UnsupportedFormat(d) => ("unsupported_format".to_string(), d),
            };
            ipc.emit_event("fatal", serde_json::json!({ "reason": reason, "detail": detail }));
            std::process::exit(4);
        }
    };

    ipc.emit_event(
        "started",
        serde_json::json!({
            "device": fmt.device_name,
            "source_sample_rate": fmt.sample_rate,
            "source_channels": fmt.channels,
        }),
    );

    // Ctrl-C → graceful stopping (helps manual testing; TerminateProcess won't hit this).
    let stop = Arc::new(AtomicBool::new(false));
    let stop_h = stop.clone();
    let _ = ctrlc::set_handler(move || stop_h.store(true, Ordering::SeqCst));

    // Worker: drain raw blocks → pcm → 640B frames → stdout (write+flush).
    let mut conv = pcm::PcmConverter::new(fmt.sample_rate, fmt.channels);
    let mut last_dropped: u64 = 0;
    loop {
        // Surface bounded-channel overflow loudly (spec §4: never a silent drop).
        // Coalesced: emit only when the cumulative count changes.
        let dropped = capture.dropped.load(Ordering::Relaxed);
        if dropped != last_dropped {
            ipc.emit_event("dropped", serde_json::json!({ "frames_total": dropped }));
            last_dropped = dropped;
        }
        if stop.load(Ordering::SeqCst) {
            ipc.emit_event("stopping", serde_json::json!({ "dropped_frames_total": dropped }));
            std::process::exit(0);
        }
        if let Ok(detail) = err_rx.try_recv() {
            ipc.emit_event("fatal", serde_json::json!({ "reason": "stream_error", "detail": detail }));
            std::process::exit(4);
        }
        let block = match rx.recv_timeout(std::time::Duration::from_millis(250)) {
            Ok(b) => b,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                ipc.emit_event("fatal", serde_json::json!({ "reason": "stream_error", "detail": "capture channel closed" }));
                std::process::exit(4);
            }
        };
        for frame in conv.push_f32(&block) {
            if let Err(e) = ipc.emit_chunk(&frame) {
                if e.kind() == io::ErrorKind::BrokenPipe {
                    // Parent sidecar gone → exit promptly (orphan prevention, spec §3).
                    std::process::exit(0);
                }
                ipc.emit_event("fatal", serde_json::json!({ "reason": "stream_error", "detail": e.to_string() }));
                std::process::exit(4);
            }
        }
    }
    // `capture` (owns the live cpal stream) stays bound in this scope until exit.
}
```

- [ ] **Step 2: Build on Windows**

Run: `cargo build --release` (on Windows, in `apps/native_helper_win`)
Expected: compiles. Confirm `panic = "abort"` is active (no warning about it being ignored).

- [ ] **Step 3: Manual run — verify event sequence**

Start a YouTube video, then run the binary and pipe stdout to a file so stderr events are visible:

Run: `target\release\yeson-win-audio-helper.exe 1>chunks.bin`
Expected on stderr: a `starting` line, then a `started` line with `source_sample_rate`/`source_channels`/`device`. `chunks.bin` grows. Ctrl-C → a `stopping` line, clean exit.

- [ ] **Step 4: Verify chunk size**

Run (PowerShell): `(Get-Item chunks.bin).Length % 640`
Expected: `0` (whole 640-byte frames).

- [ ] **Step 5: Commit**

```bash
git add apps/native_helper_win/src/main.rs
git commit -m "feat(win-helper): main lifecycle, worker, fatal/panic/broken-pipe"
```

---

## Task 5b: Contract verification — fatal / panic / broken-pipe [WIN]

These are the hard guarantees the spec (§3, §8) promises. They are tedious but short; without them the helper can silently look "done" to the sidecar on a crash. Each is a one-off manual check.

- [ ] **Step 1: fatal-on-init (no default render device)**

Disable the default output device: Settings → System → Sound → (your output) → Properties → "Don't allow" / Disable. With no enabled output device, run:

Run: `target\release\yeson-win-audio-helper.exe 1>nul`
Expected on stderr: a `starting` line then `{"event":"fatal","payload":{"reason":"no_default_render_device", …}}`. Process exits non-zero — check with `echo $LASTEXITCODE` (expect `4`). Re-enable the device afterward.

- [ ] **Step 2: panic → fatal + abort**

Temporarily insert `panic!("forced");` immediately after the `started` event emit in `main.rs`. Rebuild release, run with audio playing:

Run: `cargo build --release; target\release\yeson-win-audio-helper.exe 1>nul`
Expected on stderr: `starting`, `started`, then `{"event":"fatal","payload":{"reason":"panic", …}}`. Process exits non-zero (`$LASTEXITCODE` ≠ 0). **Revert the `panic!` line and rebuild.**

- [ ] **Step 3: broken pipe → prompt exit (orphan prevention)**

Pipe stdout into a reader that takes a little data then exits, so the helper's next write hits `BrokenPipe`:

Run (PowerShell):
```powershell
target\release\yeson-win-audio-helper.exe | powershell -NoProfile -Command "$s=[Console]::OpenStandardInput(); $b=New-Object byte[] 1280; [void]$s.Read($b,0,1280); Start-Sleep 1"
```
Expected: the helper process exits on its own within ~1–2s of the reader exiting (no hang). Confirm no orphan:

Run: `Get-Process yeson-win-audio-helper -ErrorAction SilentlyContinue`
Expected: nothing listed.

- [ ] **Step 4: Record results**

Note pass/fail for each of the three in the spec §9 checklist lines (fatal / panic / broken-pipe). No commit (no code change once the `panic!` is reverted).

---

## Task 6: `config/audio.py` — Windows dev default path [MAC TDD]

Dev convenience so a Windows dev who doesn't set `YESON_NATIVE_HELPER_BIN` still gets a sensible default. Release path is Tauri-injected (Phase 2b), unaffected. Minimal edit inside the `AUDIO_PROVIDER` anchor. The default must point at the actual PoC Cargo output (`apps/native_helper_win/target/release/...`); no Phase 2b copy script is available in this plan.

**Files:**
- Modify: `apps/client_sidecar/config/audio.py` (anchor `AUDIO_PROVIDER`)
- Test: `apps/client_sidecar/tests/test_config_audio_paths.py` (create)

- [ ] **Step 1: Read the current anchor**

Run: `sed -n '27,45p' apps/client_sidecar/config/audio.py`
Confirm `NATIVE_HELPER_BIN_PATH` is the macOS-only `os.path.join(..., "target", "native-helper-mac", "yeson-mac-audio-helper")`.

- [ ] **Step 2: Write the failing test**

Create `apps/client_sidecar/tests/test_config_audio_paths.py`:

```python
"""NATIVE_HELPER_BIN_PATH default must be platform-correct."""
import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def _restore_audio_module():
    """Reloading config.audio mutates it in sys.modules; restore the real-platform
    state after each test so other test files don't inherit a win32/darwin reload."""
    yield
    import apps.client_sidecar.config.audio as audio
    importlib.reload(audio)  # sys.platform is back to real here (monkeypatch undone)


def _reload_with_platform(monkeypatch, platform_str):
    monkeypatch.setattr(sys, "platform", platform_str)
    monkeypatch.delenv("YESON_NATIVE_HELPER_BIN", raising=False)
    import apps.client_sidecar.config.audio as audio
    return importlib.reload(audio)


def test_windows_default_points_at_win_helper_exe(monkeypatch):
    audio = _reload_with_platform(monkeypatch, "win32")
    assert audio.NATIVE_HELPER_BIN_PATH.endswith("yeson-win-audio-helper.exe")
    assert "apps" in audio.NATIVE_HELPER_BIN_PATH
    assert "native_helper_win" in audio.NATIVE_HELPER_BIN_PATH
    assert "target" in audio.NATIVE_HELPER_BIN_PATH
    assert "release" in audio.NATIVE_HELPER_BIN_PATH


def test_macos_default_unchanged(monkeypatch):
    audio = _reload_with_platform(monkeypatch, "darwin")
    assert audio.NATIVE_HELPER_BIN_PATH.endswith("yeson-mac-audio-helper")
```

- [ ] **Step 3: Run the test to verify it FAILS**

Run: `uv run pytest apps/client_sidecar/tests/test_config_audio_paths.py -v`
Expected: `test_windows_default_points_at_win_helper_exe` FAILS (default is still the mac path on win32).

- [ ] **Step 4: Edit the anchor**

In `apps/client_sidecar/config/audio.py`, replace the `NATIVE_HELPER_BIN_PATH` assignment (inside `# === ANCHOR: AUDIO_PROVIDER_* ===`) with a platform branch:

```python
import sys  # add near the top imports if not already present

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if sys.platform == "win32":
    _NATIVE_HELPER_DEFAULT = os.path.join(
        _REPO_ROOT,
        "apps",
        "native_helper_win",
        "target",
        "release",
        "yeson-win-audio-helper.exe",
    )
else:
    _NATIVE_HELPER_DEFAULT = os.path.join(
        _REPO_ROOT, "target", "native-helper-mac", "yeson-mac-audio-helper"
    )
NATIVE_HELPER_BIN_PATH: str = os.environ.get(
    "YESON_NATIVE_HELPER_BIN", _NATIVE_HELPER_DEFAULT
)
```

Keep the existing `# === ANCHOR: AUDIO_PROVIDER_START/END ===` comment lines around this block.

- [ ] **Step 5: Run the test to verify it PASSES**

Run: `uv run pytest apps/client_sidecar/tests/test_config_audio_paths.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Run the existing sidecar suite to confirm no regression**

Run: `uv run pytest apps/client_sidecar/tests -q`
Expected: all pass (factory/source tests still green).

- [ ] **Step 7: Commit**

```bash
git add apps/client_sidecar/config/audio.py apps/client_sidecar/tests/test_config_audio_paths.py
git commit -m "feat(sidecar): windows dev default for native helper path"
```

---

## Task 7: Windows E2E — sidecar via uv → server → viewer [WIN]

Validates Phase 2 success criteria (spec §6): Voicemeeter-free capture, ~50 chunks/sec at the server, viewer subtitles, no orphaned helper.

**Prereqs:** server reachable from the Windows machine; a session/meeting started so you have a **device API key**, the **session UUID**, the session's **`external_id`** (for the audio_stats query), and an **operator/admin JWT** for authenticated API reads. Obtain these via the desktop setup assistant / existing meeting-start flow. The Rust helper is built (`cargo build --release` from Task 5).

Required sidecar env (verified against `apps/client_sidecar/main.py::audio_main` + `config/constants.py`): `SERVER_WS_BASE`, `YESON_DEVICE_API_KEY`, `YESON_SESSION_ID`. `YESON_SIDECAR_MODE` defaults to `audio` (no need to set).

- [ ] **Step 1: Launch the sidecar pinned to the native helper**

In PowerShell, from repo root (substitute the real audio_ws URL + env the project already uses):

```powershell
# Server + session: sidecar connects to {SERVER_WS_BASE}/ws/sidecar?key=…&session=…
$env:SERVER_WS_BASE = "ws://<server-host>:8000"   # use wss://… if the server is TLS
$env:YESON_DEVICE_API_KEY = "<device api key>"
$env:YESON_SESSION_ID = "<session uuid>"
# Native helper selection
$env:YESON_AUDIO_PROVIDER = "native"
$env:YESON_NATIVE_HELPER_BIN = "apps\native_helper_win\target\release\yeson-win-audio-helper.exe"
uv run python -m apps.client_sidecar.main
```

Expected: sidecar logs `audio provider: native (explicit, bin=…)`, `sidecar audio mode → source=NativePipeSource …`, then `native helper event: {'event': 'starting' …}` and `… 'started' …`.

- [ ] **Step 2: Play meeting audio**

Start a 1-minute English YouTube video (or Zoom/Teams call audio).

- [ ] **Step 3: Verify chunk cadence at the server**

Query the server's audio_stats endpoint for the active session. The endpoint is present at `apps/server/api/v1/audio_stats.py` and uses `require_operator`, so include an operator/admin bearer token:

Run: `curl -H "Authorization: Bearer <operator_jwt>" http://<server>/api/v1/sessions/<external_id>/audio_stats`
Expected: JSON with `chunks_per_sec_1s` ≈ 50 (40–55 acceptable) and `total_chunks` rising over time. Check for at least 30 seconds so short resampler warm-up or transient silence does not mask cadence drift.

- [ ] **Step 4: Verify viewer subtitles**

Open the viewer URL on any device. Expected: Korean subtitles appear within a few seconds — **without any Voicemeeter / manual audio routing**.

- [ ] **Step 5: Verify no orphan after shutdown**

Stop the sidecar (Ctrl-C in its terminal). Then:

Run (PowerShell): `Get-Process yeson-win-audio-helper -ErrorAction SilentlyContinue`
Expected: no process listed. (If it remains, record as a Phase 2b Job-Object blocker per spec §8 — broken-pipe exit should have handled it.)

- [ ] **Step 5b: Verify the silence blind spot (broken-pipe is Windows' only orphan defense)**

This deliberately exercises the gap in spec §3/§8: WASAPI loopback emits no packets during silence, so the helper does no stdout write and broken-pipe is never probed; and `main.py`'s asyncio signal handlers are a no-op on Windows, so there is no graceful-close fallback. **Pause the audio** (true silence), then hard-kill the sidecar so no graceful path runs:

```powershell
# In the sidecar terminal's process tree — force-kill, do NOT Ctrl-C:
Stop-Process -Name python -Force   # or target the exact sidecar PID
Get-Process yeson-win-audio-helper -ErrorAction SilentlyContinue
```
Expected: the helper **likely lingers** until audio would resume (no write → no broken-pipe). Record the observed behavior. This is the concrete Phase 2b Job-Object justification — **not** a PoC blocker. Do **not** add silence keepalive frames to paper over it (diverges from Mac RMS-gating, pins chunks/sec at 50, corrupts Step 3's metric).

- [ ] **Step 6: Record the result**

Note device, observed `chunks_per_sec_1s` range over 30+ seconds, subtitle latency feel, and orphan result in the spec §9 checklist line for E2E.

---

## Task 8: Docs sync [MAC]

Per the project's "docs after each slice" rule: update the native-track docs when the slice lands.

**Files:**
- Modify: `docs/NATIVE_DESKTOP_HELPER_PLAN.md` (Phase 2 success-criteria block)
- Modify: `docs/ROADMAP.md` (Native track lines)

- [ ] **Step 1: Mark Phase 2 1차 status in the native plan**

In `docs/NATIVE_DESKTOP_HELPER_PLAN.md` under "### Phase 2 — Windows native capture PoC", add a dated note mirroring the Phase 1 style (the `> 📌 (YYYY-MM-DD)` callout) summarizing: Rust cpal-loopback helper landed, uv-sidecar E2E result (chunks/sec, viewer subtitles), and that device-change tracking + Tauri packaged wiring are deferred to Phase 2b.

- [ ] **Step 2: Update the ROADMAP native-track line**

In `docs/ROADMAP.md`, update the native-track note (around the existing 2026-05-28 Phase 1 lines) with a 2026-05-28 line recording Phase 2 1차 (Windows WASAPI helper PoC) status and the Phase 2b remainder.

- [ ] **Step 3: Commit**

```bash
git add docs/NATIVE_DESKTOP_HELPER_PLAN.md docs/ROADMAP.md
git commit -m "docs(native-audio): record Windows WASAPI Phase 2 1차 status"
```

---

## Self-Review (author checklist — completed)

**Spec coverage:**
- §1 Rust standalone crate → Task 1. cpal+rubato → Tasks 1,3,4. spike gate → Task 0. ✓
- §2 contract (640B/16k mono s16le, stderr JSON) → Tasks 2,3,5. ✓
- §3 events (starting/started/dropped/fatal/stopping), no permission events → Task 5; fatal-on-all-failure, panic=abort, broken-pipe exit **implemented** in Task 5 and **verified** in Task 5b; bounded-channel overflow surfaced via `dropped` event (no silent drop, §4). ✓
- §4 modules + isolation rule (no cpal in ipc/pcm) + callback safety (bounded channel) → Tasks 2,3,4. ✓
- §5 Tauri wiring → **deferred (Phase 2b), explicitly out of scope.** ✓
- §6 verification (rust unit, win smoke, E2E ~50 chunks/sec via audio_stats) → Tasks 2,3,4,7. ✓
- §7 non-scope respected (no PyInstaller, no signing, no UI, device-watch deferred). ✓
- §8 risks (stdout buffering→flush per chunk; callback heavy work→channel; fatal contract; exclusive device→unsupported/wasapi_init; mix-format variance→pcm dynamic; orphan→broken-pipe) → Tasks 2,3,4,5. ✓
- §9 deliverables → Tasks map 1:1 except Phase-2b-tagged items. ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code; commands have expected output. ✓

**Type consistency:** `PcmConverter::new(u32,u16)`, `push_f32(&[f32])`, `push_i16(&[i16])`, `[u8;640]` frames; `capture::start() -> (Capture, CaptureFormat, Receiver<RawBlock>, Receiver<String>)`; `Ipc::new(data,control)`, `emit_chunk(&[u8])->io::Result`, `emit_event(&str, Value)` — consistent across Tasks 2–5. ✓

**Known adaptation point:** `rubato` API specifics (`SincInterpolationParameters`, `SincFixedIn::new`, `process`) may differ by minor version — the Task 3 tests are the behavioral contract; adapt the call shape to the installed version while keeping tests green.

---

## Phase 2b (separate future plan — NOT this plan)

Recorded so it isn't lost: `device_watch.rs` (default-device change tracking with restart/gap/dup-event policy), `scripts/build-release.ps1`, `package.json` `build:native-helper-win`, `sidecar.rs::locate_bundled_native_helper()` Windows x86_64 + `.exe` suffix, `tauri.windows.conf.json` externalBin + before*Command, Job-Object orphan cleanup, Windows PyInstaller sidecar bundle.

> 📌 (2026-05-29) **Tauri packaged wiring landed (3 additive edits).** The production-capture path is wired so the bundled installer ships and launches the WASAPI helper:
> - `sidecar.rs::locate_bundled_native_helper()` — added Windows x86_64 arm (`yeson-win-audio-helper`, `x86_64-pc-windows-msvc`) + `.exe` suffix, mirroring `locate_bundled_sidecar`. (`add_native_helper_env` unchanged — still injects `YESON_NATIVE_HELPER_BIN` + `provider=native`.)
> - `tauri.windows.conf.json` externalBin — added `binaries/yeson-win-audio-helper`.
> - `.github/workflows/windows-desktop.yml` — added a `cargo build --release --bin yeson-win-audio-helper` (native **MSVC**) step that copies to `binaries/yeson-win-audio-helper-x86_64-pc-windows-msvc.exe`, plus `apps/native_helper_win/**` to the push paths-filter.
>
> Chosen over the originally-listed local `scripts/build-release.ps1` + `package.json build:native-helper-win`: the helper is built in CI (windows-latest), matching the existing sidecar PyInstaller step — no local Windows build path needed. Base `beforeBuildCommand` stays `pnpm build:vite` (no helper build), so `before*Command` wiring was unnecessary.
>
> Verified on macOS: `cargo check` (src-tauri, Windows arm type-checks), JSON/YAML valid. **Unverified (needs CI/VM):** the MSVC helper build itself (the crate lib compiles `stream` = tungstenite+rustls/ring unconditionally; cross-compiled to windows-**gnu** already, MSVC likely OK but unconfirmed), externalBin bundling, and the runtime `locate_bundled_native_helper` hit.
>
> Still Phase 2b: `device_watch.rs`, Job-Object orphan cleanup. (Windows PyInstaller sidecar bundle already exists in the workflow.)

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-28-windows-wasapi-helper.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**

Note: Tasks 0, 4, 5, 7 are **[WIN]** — they run on your Windows machine/VM, not here. Tasks 1, 2, 3, 6, 8 are **[MAC]/[BOTH]** and run on this dev machine. A natural split: implement and `cargo test` the pure modules here, then move to Windows for capture + E2E.
