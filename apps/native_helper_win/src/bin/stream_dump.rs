// === ANCHOR: STREAM_DUMP_START ===
//! yeson-win-stream-dump — single self-contained test binary.
//!
//! Captures audio and streams it straight to the yeson-meet server's
//! /ws/sidecar, so subtitles can be validated with ONE exe (no Python/uv/repo).
//! Mirrors the cross-from-mac, double-clickable `voicemeeter_dump` pattern.
//!
//!   Windows: default source = WASAPI loopback (system audio).
//!   Any OS : set YESON_PCM_FILE to replay a raw f32le interleaved PCM file
//!            (used for macOS dry-runs against the same server).
//!
//! Config via env (same names as the sidecar, so they carry over from the
//! PowerShell session that created the meeting):
//!   SERVER_WS_BASE        wss://192.168.0.38         (required)
//!   YESON_DEVICE_API_KEY  <device api key>           (required)
//!   YESON_SESSION_ID      <session uuid>             (required)
//!   YESON_CA_FILE         path to Caddy root CA pem  (optional; verified TLS)
//!   YESON_TLS_INSECURE    1 → skip TLS verification  (optional escape hatch)
//!   YESON_PCM_FILE        raw f32le PCM to replay    (optional; forces file source)
//!   YESON_PCM_RATE        file sample rate           (default 48000)
//!   YESON_PCM_CHANNELS    file channel count         (default 2)

use yeson_win_audio_helper::source::{AudioSource, FileSource};
use yeson_win_audio_helper::stream::{self, Tls};

fn req(name: &str) -> Result<String, String> {
    std::env::var(name).map_err(|_| format!("missing required env {name}"))
}

fn main() {
    let code = match run() {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("\n[stream-dump] ERROR: {e}");
            2
        }
    };
    // Double-click friendly: keep the console open so output stays visible.
    if std::env::var("YESON_NO_PAUSE").is_err() {
        eprintln!("\nPress Enter to close…");
        let mut buf = String::new();
        let _ = std::io::stdin().read_line(&mut buf);
    }
    std::process::exit(code);
}

fn run() -> Result<(), String> {
    let base = req("SERVER_WS_BASE")?;
    let key = req("YESON_DEVICE_API_KEY")?;
    let session = req("YESON_SESSION_ID")?;

    let tls = if std::env::var("YESON_TLS_INSECURE").is_ok_and(|v| v == "1") {
        eprintln!("[stream-dump] TLS: INSECURE (verification disabled)");
        Tls::Insecure
    } else if let Ok(ca) = std::env::var("YESON_CA_FILE") {
        eprintln!("[stream-dump] TLS: verify + extra CA {ca}");
        Tls::VerifyWithCa(ca)
    } else {
        eprintln!("[stream-dump] TLS: verify (OS trust store)");
        Tls::Verify
    };

    let source: Box<dyn AudioSource> = match std::env::var("YESON_PCM_FILE") {
        Ok(path) => {
            let rate = std::env::var("YESON_PCM_RATE")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(48_000u32);
            let channels = std::env::var("YESON_PCM_CHANNELS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(2u16);
            eprintln!("[stream-dump] source: PCM file {path} ({rate} Hz / {channels} ch, f32le)");
            Box::new(FileSource::open(&path, rate, channels).map_err(|e| format!("open PCM file: {e}"))?)
        }
        Err(_) => make_capture_source()?,
    };

    stream::run(source, &base, &key, &session, tls)
}

#[cfg(windows)]
fn make_capture_source() -> Result<Box<dyn AudioSource>, String> {
    use yeson_win_audio_helper::source::WasapiSource;
    let src = WasapiSource::start()?;
    eprintln!("[stream-dump] source: WASAPI loopback (device: {})", src.device_name);
    Ok(Box::new(src))
}

#[cfg(not(windows))]
fn make_capture_source() -> Result<Box<dyn AudioSource>, String> {
    Err("no capture source on this OS — set YESON_PCM_FILE to replay a PCM file".to_string())
}
// === ANCHOR: STREAM_DUMP_END ===
