// === ANCHOR: MAIN_START ===
// yeson-win-audio-helper: Windows WASAPI loopback → stdout 16k mono s16le PCM.
// Shared modules now live in the crate lib (src/lib.rs) so the stream_dump tool
// can reuse pcm/capture. This bin = the production stdout-PCM helper.

#[cfg(not(windows))]
fn main() {
    eprintln!("yeson-win-audio-helper is Windows-only");
    std::process::exit(2);
}

#[cfg(windows)]
fn main() {
    use std::io::{self, Write};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use yeson_win_audio_helper::{capture, device_watch, ipc, pcm};

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

    let (mut capture, fmt, mut rx, mut err_rx) = match capture::start() {
        Ok(t) => t,
        Err(e) => {
            let (reason, detail) = match e {
                capture::CaptureError::NoDefaultRenderDevice => {
                    ("no_default_render_device".to_string(), String::new())
                }
                capture::CaptureError::WasapiInitFailed(d) => ("wasapi_init_failed".to_string(), d),
                capture::CaptureError::UnsupportedFormat(d) => {
                    ("unsupported_format".to_string(), d)
                }
            };
            ipc.emit_event("fatal", serde_json::json!({ "reason": reason, "detail": detail }));
            std::process::exit(4);
        }
    };

    // Track the device we're capturing for the change poll (clone before the
    // `started` emit below moves fmt.device_name).
    let mut active_device = fmt.device_name.clone();
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

    // Device-change polling (spec §3): re-query the default output every ~1.5s and
    // rebuild the loopback in-process if it changed, so a mid-meeting device switch
    // (speakers → headphones/BT) doesn't silently stop subtitles. Monotonic clock
    // from a base Instant → u64 ms for the pure DeviceWatcher.
    use std::time::Instant;
    const POLL_INTERVAL_MS: u64 = 1_500;
    const REBUILD_THROTTLE_MS: u64 = 5_000;
    const REBUILD_SETTLE_MS: u64 = 250;
    const REBUILD_MAX_ATTEMPTS: u32 = 4;
    let clock_base = Instant::now();
    let mut watcher = device_watch::DeviceWatcher::new(REBUILD_THROTTLE_MS);
    let mut last_poll_ms: u64 = 0;

    loop {
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
            ipc.emit_event(
                "fatal",
                serde_json::json!({ "reason": "stream_error", "detail": detail }),
            );
            std::process::exit(4);
        }
        let block = match rx.recv_timeout(std::time::Duration::from_millis(250)) {
            Ok(b) => b,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                ipc.emit_event(
                    "fatal",
                    serde_json::json!({ "reason": "stream_error", "detail": "capture channel closed" }),
                );
                std::process::exit(4);
            }
        };
        for frame in conv.push_f32(&block) {
            if let Err(e) = ipc.emit_chunk(&frame) {
                if e.kind() == io::ErrorKind::BrokenPipe {
                    // Parent sidecar gone → exit promptly (orphan prevention, spec §3).
                    std::process::exit(0);
                }
                ipc.emit_event(
                    "fatal",
                    serde_json::json!({ "reason": "stream_error", "detail": e.to_string() }),
                );
                std::process::exit(4);
            }
        }
    }
    // `capture` (owns the live cpal stream) stays bound in this scope until exit.
}
// === ANCHOR: MAIN_END ===
