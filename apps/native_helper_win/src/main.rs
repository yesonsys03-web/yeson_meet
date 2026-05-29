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
                capture::CaptureError::UnsupportedFormat(d) => {
                    ("unsupported_format".to_string(), d)
                }
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
