//! All-in-one streamer: capture source → 16k mono 640B frames → `/ws/sidecar`.
//! Replicates the Python sidecar's `audio_ws.stream_audio` protocol exactly:
//!   1. connect wss://host/ws/sidecar?key=&session=
//!   2. text `audio.started`
//!   3. binary 640B frames, text `chunk_meta` every 50
//!   4. text `audio.stopped` on exit
//!
//! TLS = rustls (ring). Identical stack macOS↔Windows — native-tls's Windows
//! SChannel backend silently dropped binary frames. A periodic non-fatal read
//! "pump" services server WebSocket pings (→ pong) so the connection survives
//! past the server's ~40s ping timeout, and detects a server-side close.

use crate::pcm::PcmConverter;
use crate::source::AudioSource;
use std::io::ErrorKind;
use std::net::TcpStream;
use std::sync::Arc;
use std::time::Duration;
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{client_tls_with_config, Connector, Message, WebSocket};

/// TLS verification policy for the wss connection.
pub enum Tls {
    /// Verify against the bundled webpki roots (won't trust a private/self-signed CA).
    Verify,
    /// Verify, trusting the CA PEM at this path (e.g. Caddy internal CA).
    VerifyWithCa(String),
    /// Skip verification entirely (PoC LAN escape hatch, YESON_TLS_INSECURE=1).
    Insecure,
}

type Ws = WebSocket<MaybeTlsStream<TcpStream>>;

fn ring() -> Arc<rustls::crypto::CryptoProvider> {
    Arc::new(rustls::crypto::ring::default_provider())
}

fn ca_config(path: &str) -> Result<rustls::ClientConfig, String> {
    let pem = std::fs::read(path).map_err(|e| format!("read CA file {path}: {e}"))?;
    let mut roots = rustls::RootCertStore::empty();
    for cert in rustls_pemfile::certs(&mut &pem[..]) {
        let cert = cert.map_err(|e| format!("parse CA PEM: {e}"))?;
        roots.add(cert).map_err(|e| format!("add CA to root store: {e}"))?;
    }
    Ok(rustls::ClientConfig::builder_with_provider(ring())
        .with_safe_default_protocol_versions()
        .map_err(|e| format!("rustls versions: {e}"))?
        .with_root_certificates(roots)
        .with_no_client_auth())
}

fn insecure_config() -> Result<rustls::ClientConfig, String> {
    let provider = ring();
    Ok(rustls::ClientConfig::builder_with_provider(provider.clone())
        .with_safe_default_protocol_versions()
        .map_err(|e| format!("rustls versions: {e}"))?
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(NoVerify(provider)))
        .with_no_client_auth())
}

/// Accept-all server cert verifier (YESON_TLS_INSECURE only).
#[derive(Debug)]
struct NoVerify(Arc<rustls::crypto::CryptoProvider>);

impl rustls::client::danger::ServerCertVerifier for NoVerify {
    fn verify_server_cert(
        &self,
        _end_entity: &rustls::pki_types::CertificateDer<'_>,
        _intermediates: &[rustls::pki_types::CertificateDer<'_>],
        _server_name: &rustls::pki_types::ServerName<'_>,
        _ocsp: &[u8],
        _now: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        Ok(rustls::client::danger::ServerCertVerified::assertion())
    }
    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &rustls::pki_types::CertificateDer<'_>,
        dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls12_signature(message, cert, dss, &self.0.signature_verification_algorithms)
    }
    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &rustls::pki_types::CertificateDer<'_>,
        dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls13_signature(message, cert, dss, &self.0.signature_verification_algorithms)
    }
    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        self.0.signature_verification_algorithms.supported_schemes()
    }
}

fn connector(tls: &Tls) -> Result<Option<Connector>, String> {
    Ok(match tls {
        Tls::Verify => None, // tungstenite default = rustls + webpki roots
        Tls::VerifyWithCa(path) => Some(Connector::Rustls(Arc::new(ca_config(path)?))),
        Tls::Insecure => Some(Connector::Rustls(Arc::new(insecure_config()?))),
    })
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
        Some((h, p)) => Ok((h.to_string(), p.parse::<u16>().map_err(|_| format!("bad port: {p}"))?)),
        None => Ok((hostport.to_string(), default_port)),
    }
}

fn set_read_timeout(ws: &mut Ws, dur: Option<Duration>) {
    match ws.get_mut() {
        MaybeTlsStream::Plain(s) => {
            let _ = s.set_read_timeout(dur);
        }
        MaybeTlsStream::Rustls(s) => {
            let _ = s.sock.set_read_timeout(dur);
        }
        _ => {}
    }
}

/// Drain any immediately-available incoming frames so tungstenite can answer
/// server pings (queues a Pong, flushed on the next write) and we notice a
/// server-side close. Returns Err only on a real close/error — a read timeout
/// (no data) is the normal, expected outcome.
fn pump(ws: &mut Ws) -> Result<(), String> {
    loop {
        match ws.read() {
            Ok(Message::Close(_)) => return Err("server closed the connection".into()),
            Ok(_) => continue, // ping handled internally; ignore other frames
            Err(tungstenite::Error::Io(e))
                if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) =>
            {
                return Ok(())
            }
            Err(tungstenite::Error::ConnectionClosed) | Err(tungstenite::Error::AlreadyClosed) => {
                return Err("connection closed".into())
            }
            Err(e) => return Err(format!("read: {e}")),
        }
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
    let tcp = TcpStream::connect(&addr).map_err(|e| format!("tcp connect {addr}: {e}"))?;

    eprintln!("[stream] WebSocket/TLS handshake …");
    let (mut ws, resp) = client_tls_with_config(url.as_str(), tcp, None, connector(&tls)?)
        .map_err(|e| format!("ws/tls handshake failed: {e}"))?;
    eprintln!("[stream] connected (HTTP {})", resp.status());

    // Short read timeout so pump() returns quickly when nothing is pending.
    set_read_timeout(&mut ws, Some(Duration::from_millis(15)));

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
    pump(&mut ws)?; // drain post-handshake/server frames before streaming

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
                if let Err(e) = pump(&mut ws) {
                    eprintln!("[stream] {e}");
                    reason = "server closed";
                    break 'outer;
                }
            }
        }
    }

    let stopped = serde_json::json!({ "type": "audio.stopped", "reason": reason });
    let _ = ws.send(Message::text(stopped.to_string()));
    let _ = ws.close(None);
    let _ = ws.flush();
    eprintln!("[stream] done — {seq} chunks, reason: {reason}");
    Ok(())
}
