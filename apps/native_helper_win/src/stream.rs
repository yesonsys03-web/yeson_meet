//! All-in-one streamer: capture source → 16k mono 640B frames → `/ws/sidecar`.
//! Replicates the Python sidecar's `audio_ws.stream_audio` protocol exactly:
//!   1. connect wss://host/ws/sidecar?key=&session=
//!   2. text `audio.started`
//!   3. binary 640B frames, text `chunk_meta` every 50
//!   4. text `audio.stopped` on exit
//! (No reconnect/backoff — this is a one-shot test tool, not the sidecar.)

use crate::pcm::PcmConverter;
use crate::source::AudioSource;
use tungstenite::{client_tls_with_config, Connector, Message};

/// TLS verification policy for the wss connection.
pub enum Tls {
    /// Verify against the OS trust store (default).
    Verify,
    /// Verify, additionally trusting the CA PEM at this path (Caddy internal CA).
    VerifyWithCa(String),
    /// Skip verification entirely (PoC LAN escape hatch, YESON_TLS_INSECURE=1).
    Insecure,
}

fn build_connector(tls: &Tls) -> Result<Connector, String> {
    let mut builder = native_tls::TlsConnector::builder();
    match tls {
        Tls::Verify => {}
        Tls::VerifyWithCa(path) => {
            let pem = std::fs::read(path).map_err(|e| format!("read CA file {path}: {e}"))?;
            let cert =
                native_tls::Certificate::from_pem(&pem).map_err(|e| format!("parse CA PEM: {e}"))?;
            builder.add_root_certificate(cert);
        }
        Tls::Insecure => {
            builder.danger_accept_invalid_certs(true);
            builder.danger_accept_invalid_hostnames(true);
        }
    }
    let connector = builder.build().map_err(|e| format!("build TLS connector: {e}"))?;
    Ok(Connector::NativeTls(connector))
}

/// Parse "wss://host[:port]" / "ws://host[:port]" → (host, port).
fn parse_host_port(base: &str) -> Result<(String, u16), String> {
    let (scheme, rest) = base
        .split_once("://")
        .ok_or_else(|| format!("SERVER_WS_BASE missing scheme: {base}"))?;
    let default_port = match scheme {
        "wss" => 443,
        "ws" => 80,
        other => return Err(format!("unsupported scheme: {other}")),
    };
    let hostport = rest.trim_end_matches('/');
    match hostport.rsplit_once(':') {
        Some((h, p)) => {
            let port = p.parse::<u16>().map_err(|_| format!("bad port: {p}"))?;
            Ok((h.to_string(), port))
        }
        None => Ok((hostport.to_string(), default_port)),
    }
}

fn now_rfc3339() -> String {
    chrono::Utc::now().to_rfc3339()
}

/// Connect, stream the source to the server, send audio.stopped on exit.
pub fn run(
    mut source: Box<dyn AudioSource>,
    base: &str,
    key: &str,
    session: &str,
    tls: Tls,
) -> Result<(), String> {
    let base = base.trim_end_matches('/');
    let url = format!("{base}/ws/sidecar?key={key}&session={session}");
    let (host, port) = parse_host_port(base)?;
    let addr = format!("{host}:{port}");

    eprintln!("[stream] TCP connect {addr} …");
    let tcp =
        std::net::TcpStream::connect(&addr).map_err(|e| format!("tcp connect {addr}: {e}"))?;
    let connector = build_connector(&tls)?;

    eprintln!("[stream] WebSocket/TLS handshake …");
    let (mut ws, resp) = client_tls_with_config(url.as_str(), tcp, None, Some(connector))
        .map_err(|e| format!("ws/tls handshake failed: {e}"))?;
    eprintln!("[stream] connected (HTTP {})", resp.status());

    // 1) audio.started — required for the server to start the AI live session.
    let started = serde_json::json!({
        "type": "audio.started",
        "sample_rate": 16000,
        "channels": 1,
        "format": "pcm_s16le",
        "started_at": now_rfc3339(),
    });
    ws.send(Message::text(started.to_string()))
        .map_err(|e| format!("send audio.started: {e}"))?;

    let (rate, channels) = source.format();
    eprintln!("[stream] source {rate} Hz / {channels} ch → 16k mono s16le; streaming (Ctrl-C to stop) …");
    let mut conv = PcmConverter::new(rate, channels);
    let mut seq: u64 = 0;
    let mut reason = "stream exhausted";

    'outer: while let Some(block) = source.next_block() {
        for frame in conv.push_f32(&block) {
            seq += 1;
            if let Err(e) = ws.send(Message::binary(frame.to_vec())) {
                eprintln!("[stream] send chunk #{seq} failed: {e}");
                reason = "send error";
                break 'outer;
            }
            if seq % 50 == 0 {
                let meta = serde_json::json!({
                    "type": "chunk_meta",
                    "seq": seq,
                    "started_at": now_rfc3339(),
                });
                let _ = ws.send(Message::text(meta.to_string()));
                eprintln!("[stream] {seq} chunks sent (~{}s of audio)", seq / 50);
            }
        }
    }

    let stopped = serde_json::json!({ "type": "audio.stopped", "reason": reason });
    let _ = ws.send(Message::text(stopped.to_string()));
    let _ = ws.close(None);
    eprintln!("[stream] done — {seq} chunks, reason: {reason}");
    Ok(())
}
