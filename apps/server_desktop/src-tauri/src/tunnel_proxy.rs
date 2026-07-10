// === ANCHOR: TUNNEL_PROXY_START ===
//! Viewer-only path-allowlist reverse proxy (P4.1a — the SECURITY BOUNDARY).
//!
//! cloudflared points NOT at the bundled server `:8000` directly, but at THIS
//! thin local proxy on `:vport`. The proxy forwards ONLY the viewer surface to
//! `127.0.0.1:<server_port>` and 404s EVERYTHING else, so the durable device
//! key (`/ws/sidecar?key=`), the operator WS, and the operator/auth/devices REST
//! never become reachable over the public tunnel. (USER DECISION = Option A.)
//!
//! The allowlist is **deny-by-default** and **bypass-resistant**: the request
//! path is fully normalized (percent-decoded to a fixed point, lower-cased,
//! duplicate slashes collapsed, `.`/`..` segments resolved with no escape) BEFORE
//! matching, so `/api/v1/../v1/devices`, `%2e%2e`, `/API/V1/...`, `//ws/sidecar`,
//! trailing-dot, and `/v/../ws/sidecar` smuggling all collapse to their true
//! target and are rejected.
use std::convert::Infallible;
use std::sync::Arc;

use http_body_util::{BodyExt, Full};
use hyper::body::{Bytes, Incoming};
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::upgrade::Upgraded;
use hyper::{Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use tokio::io::AsyncWriteExt;
use tokio::net::{TcpListener, TcpStream};

/// The decision the allowlist returns for a normalized request path.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PathDecision {
    /// Forward to the bundled server (viewer surface).
    Allow,
    /// 404 — not part of the viewer surface (deny-by-default).
    Deny,
}

// === ANCHOR: TUNNEL_PROXY_NORMALIZE_START ===
/// Normalize a raw request path into a canonical, bypass-resistant form for the
/// allowlist match. Steps (in order):
///   1. Drop the query string and fragment (decision is on the path only).
///   2. Percent-decode to a fixed point (defeats `%2e%2e`, double-encoding like
///      `%252e`, and `%2f` slash-smuggling).
///   3. Lower-case (defeats `/API/V1`, `/WS/Sidecar` case games — the viewer
///      route prefixes we match are all lower-case).
///   4. Split on `/`, collapse duplicate slashes (`//` -> `/`), and resolve `.`
///      / `..` segments. A `..` that would pop above root is simply dropped
///      (cannot escape), so `/v/../ws/sidecar` canonicalizes to `/ws/sidecar`
///      and is then denied.
///
/// Returns a path that always begins with `/`.
pub fn normalize_path(raw: &str) -> String {
    // 1. strip query (?) and fragment (#).
    let path = raw
        .split(['?', '#'])
        .next()
        .unwrap_or("");

    // 2. percent-decode to a fixed point.
    let mut decoded = percent_decode(path);
    for _ in 0..8 {
        let again = percent_decode(&decoded);
        if again == decoded {
            break;
        }
        decoded = again;
    }

    // 3. lower-case for case-insensitive prefix matching.
    let lowered = decoded.to_ascii_lowercase();

    // 4. collapse slashes + resolve . / .. (treat backslashes as separators too,
    //    so a Windows-style `\` cannot smuggle a segment past the split).
    let mut segments: Vec<&str> = Vec::new();
    for seg in lowered.split(['/', '\\']) {
        match seg {
            "" | "." => {}
            ".." => {
                segments.pop();
            }
            other => segments.push(other),
        }
    }
    format!("/{}", segments.join("/"))
}

/// Minimal percent-decoder: turns `%XX` into its byte, lossily as UTF-8. Invalid
/// escapes are passed through literally. (We only need this for path
/// canonicalization, not general URL parsing — kept dependency-free.)
fn percent_decode(input: &str) -> String {
    let bytes = input.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        // A `%XX` escape needs exactly 3 bytes (`%`, hi, lo). `i + 3 <= len`
        // admits a `%XX` that OCCUPIES the final three bytes (the old
        // `i + 2 < len` dropped a trailing escape, so a path ending in `%2e`
        // smuggled a literal `%2e` past normalization — fail-open). Fail-closed:
        // decode the trailing escape too.
        if bytes[i] == b'%' && i + 3 <= bytes.len() {
            let hi = hex_val(bytes[i + 1]);
            let lo = hex_val(bytes[i + 2]);
            if let (Some(hi), Some(lo)) = (hi, lo) {
                out.push((hi << 4) | lo);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn hex_val(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}
// === ANCHOR: TUNNEL_PROXY_NORMALIZE_END ===

// === ANCHOR: TUNNEL_PROXY_DECIDE_START ===
/// The pure allowlist decision over a *normalized* path. Deny-by-default: only
/// the viewer surface is allowed through. This is the single security gate and
/// is exhaustively unit-tested (including the bypass attempts).
///
/// ALLOW (viewer surface, method-agnostic — preserves prior behavior):
///   - `/`                      -> SPA shell
///   - `/v` and `/v/<...>`      -> viewer client route
///   - `/index.html`            -> SPA shell file
///   - `/favicon...`            -> top-level favicon(s)
///   - `/assets/<...>`          -> built SPA JS/CSS (apps/web/dist/assets/*)
///   - `/api/v1/viewer/<...>`   -> viewer REST (list_viewer_utterances)
///   - `/ws/viewer`             -> viewer WebSocket
///
/// ALLOW (capture surface, method pinned — new for remote capture support):
///   - `GET  /capture`                                  -> capture SPA route
///   - `POST /api/v1/auth/login`                        -> operator login
///   - `POST /api/v1/sessions`                          -> create session
///   - `GET  /ws/capture`                                -> capture WebSocket
///   - `POST /api/v1/sessions/<id>/end`                  -> end session
///   - `POST /api/v1/sessions/<id>/capture-token`        -> capture token
///   - `GET  /api/v1/sessions/<id>/utterances`            -> preview polling
///     (`<id>` is a single, non-empty path segment — no nesting, no empty slot)
///
/// Everything else (incl. `/ws/sidecar`, `/ws/operator`, `/api/v1/auth/*`
/// with the wrong method, `/api/v1/devices*`, `/api/v1/sessions*` outside the
/// pinned shapes above, `/api/v1/operator/*`, `/api/v1/audio_stats*`) -> DENY.
pub fn decide(method: &str, normalized_path: &str) -> PathDecision {
    let p = normalized_path;
    let m = method.to_ascii_uppercase();

    // --- 기존 뷰어 표면 (메서드 무관 — 기존 동작 보존) ---
    if p == "/" || p == "/index.html" || p == "/v" || p == "/ws/viewer" {
        return PathDecision::Allow;
    }
    // Viewer client route and its sub-paths.
    if p.starts_with("/v/") {
        return PathDecision::Allow;
    }
    // Built SPA static assets.
    if p.starts_with("/assets/") {
        return PathDecision::Allow;
    }
    // Top-level favicon(s): /favicon.ico, /favicon.svg, /favicon-32x32.png, ...
    if p.starts_with("/favicon") {
        return PathDecision::Allow;
    }
    // Viewer REST only — NOT the rest of /api/v1/*.
    if p.starts_with("/api/v1/viewer/") {
        return PathDecision::Allow;
    }

    // --- 캡처 표면 (메서드 못박음) ---
    if p == "/capture" && m == "GET" {
        return PathDecision::Allow;
    }
    if p == "/api/v1/auth/login" && m == "POST" {
        return PathDecision::Allow;
    }
    if p == "/api/v1/sessions" && m == "POST" {
        return PathDecision::Allow;
    }
    if p == "/ws/capture" && m == "GET" {
        return PathDecision::Allow;
    }
    // /api/v1/sessions/<id>/{end|capture-token|utterances} — <id>는 단일 비어있지 않은 세그먼트
    if let Some(rest) = p.strip_prefix("/api/v1/sessions/") {
        let mut parts = rest.split('/');
        let (id, tail, extra) = (parts.next(), parts.next(), parts.next());
        if extra.is_none() {
            if let (Some(id), Some(tail)) = (id, tail) {
                if !id.is_empty() {
                    match (tail, m.as_str()) {
                        ("end", "POST") | ("capture-token", "POST") | ("utterances", "GET") => {
                            return PathDecision::Allow;
                        }
                        _ => {}
                    }
                }
            }
        }
    }

    PathDecision::Deny
}

/// Convenience: normalize THEN decide. The proxy uses this; tests exercise both
/// `normalize_path`+`decide` and this combined entrypoint with raw paths.
pub fn viewer_allows(method: &str, raw_path: &str) -> PathDecision {
    decide(method, &normalize_path(raw_path))
}
// === ANCHOR: TUNNEL_PROXY_DECIDE_END ===

// === ANCHOR: TUNNEL_PROXY_SERVER_START ===
/// A running viewer-only proxy: the local port cloudflared should target, plus a
/// shutdown signal so the tunnel manager can tear it down with the tunnel.
pub struct ProxyHandle {
    pub vport: u16,
    shutdown: tokio::sync::watch::Sender<bool>,
}

impl ProxyHandle {
    /// Signal the accept loop to stop. In-flight connections drain on their own.
    pub fn stop(&self) {
        let _ = self.shutdown.send(true);
    }
}

/// Bind the viewer-only proxy on an ephemeral loopback port and forward the
/// allowlisted viewer surface to `server_port`. Returns the chosen `vport`.
/// Spawns the accept loop on the provided tokio handle.
pub async fn start_proxy(server_port: u16) -> std::io::Result<ProxyHandle> {
    // Bind loopback:0 so the OS hands us a free ephemeral port distinct from the
    // server's. cloudflared connects over loopback only; the public edge is the
    // tunnel, never this port directly.
    let listener = TcpListener::bind(("127.0.0.1", 0)).await?;
    let vport = listener.local_addr()?.port();
    let (shutdown_tx, mut shutdown_rx) = tokio::sync::watch::channel(false);
    let upstream = Arc::new(format!("127.0.0.1:{server_port}"));

    tokio::spawn(async move {
        loop {
            tokio::select! {
                _ = shutdown_rx.changed() => {
                    if *shutdown_rx.borrow() {
                        break;
                    }
                }
                accepted = listener.accept() => {
                    let Ok((stream, _peer)) = accepted else { continue };
                    let upstream = upstream.clone();
                    tokio::spawn(async move {
                        let io = TokioIo::new(stream);
                        let service = service_fn(move |req| {
                            let upstream = upstream.clone();
                            async move { proxy_request(req, upstream).await }
                        });
                        // `with_upgrades` so the `/ws/viewer` 101 handshake can be
                        // hijacked and relayed as a raw bidirectional tunnel.
                        let _ = http1::Builder::new()
                            .serve_connection(io, service)
                            .with_upgrades()
                            .await;
                    });
                }
            }
        }
    });

    Ok(ProxyHandle { vport, shutdown: shutdown_tx })
}

/// Build a 404 response (deny-by-default + any upstream failure surface).
fn not_found() -> Response<Full<Bytes>> {
    let mut resp = Response::new(Full::new(Bytes::from_static(b"404 Not Found")));
    *resp.status_mut() = StatusCode::NOT_FOUND;
    resp
}

fn bad_gateway() -> Response<Full<Bytes>> {
    let mut resp = Response::new(Full::new(Bytes::from_static(b"502 Bad Gateway")));
    *resp.status_mut() = StatusCode::BAD_GATEWAY;
    resp
}

/// Per-request handler: run the allowlist on the *normalized* path, then either
/// 404 or forward to the bundled server (WS upgrade or plain HTTP).
async fn proxy_request(
    req: Request<Incoming>,
    upstream: Arc<String>,
) -> Result<Response<Full<Bytes>>, Infallible> {
    let raw_path = req.uri().path().to_string();
    let method = req.method().as_str().to_string();
    if viewer_allows(&method, &raw_path) == PathDecision::Deny {
        return Ok(not_found());
    }

    // WebSocket upgrade (only `/ws/viewer` is allowed to reach here) needs a raw
    // byte tunnel after the 101, so it is handled distinctly from plain HTTP.
    if is_upgrade(&req) {
        return Ok(proxy_upgrade(req, upstream).await);
    }

    match forward_http(req, upstream).await {
        Ok(resp) => Ok(resp),
        Err(_) => Ok(bad_gateway()),
    }
}

/// True when the request asks for a protocol upgrade (WebSocket).
fn is_upgrade(req: &Request<Incoming>) -> bool {
    req.headers()
        .get(hyper::header::CONNECTION)
        .and_then(|v| v.to_str().ok())
        .map(|v| v.to_ascii_lowercase().contains("upgrade"))
        .unwrap_or(false)
        && req.headers().contains_key(hyper::header::UPGRADE)
}

/// Forward a plain (non-upgrade) HTTP request to the bundled server and return
/// its response with the body fully buffered.
async fn forward_http(
    req: Request<Incoming>,
    upstream: Arc<String>,
) -> Result<Response<Full<Bytes>>, Box<dyn std::error::Error + Send + Sync>> {
    let (parts, body) = req.into_parts();
    let body_bytes = body.collect().await?.to_bytes();

    let stream = TcpStream::connect(upstream.as_str()).await?;
    let io = TokioIo::new(stream);
    let (mut sender, conn) = hyper::client::conn::http1::handshake(io).await?;
    tokio::spawn(async move {
        let _ = conn.await;
    });

    let mut out_req = Request::from_parts(parts, Full::new(body_bytes));
    // Forward the original path+query (allowlist already cleared the path; the
    // server re-validates tokens etc.). The URI carries it through unchanged.
    // Defense-in-depth (Nit 2): strip hop-by-hop / Connection / Upgrade control
    // headers and clear any inbound `X-Forwarded-*` an edge might have set, so a
    // crafted public request can neither smuggle an upgrade on the plain-HTTP
    // path nor spoof the forwarded chain. The WS path keeps its upgrade headers
    // (handled separately in `proxy_upgrade`); this is HTTP-only.
    strip_hop_by_hop_headers(out_req.headers_mut());

    let resp = sender.send_request(out_req).await?;
    let (parts, body) = resp.into_parts();
    let bytes = body.collect().await?.to_bytes();
    Ok(Response::from_parts(parts, Full::new(bytes)))
}

/// Remove hop-by-hop, connection-control, and upgrade-control headers from a
/// plain-HTTP request before forwarding, plus any inbound `X-Forwarded-*`
/// (defense-in-depth). Hop-by-hop headers are scoped to a single transport hop
/// (RFC 9110/7230) and must not be relayed across the proxy; clearing
/// `connection`/`upgrade`/`x-forwarded-*` also denies a crafted public request
/// the ability to coax the viewer-only HTTP path into an upgrade or to spoof the
/// forwarded chain. The viewer WS upgrade is forwarded by `proxy_upgrade`, which
/// deliberately preserves the headers the handshake needs — this is HTTP-only.
fn strip_hop_by_hop_headers(headers: &mut hyper::HeaderMap) {
    use hyper::header::{HeaderName, CONNECTION, UPGRADE};
    const HOP_BY_HOP: [&str; 7] = [
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "x-forwarded-for",
    ];
    headers.remove(CONNECTION);
    headers.remove(UPGRADE);
    headers.remove("x-forwarded-host");
    headers.remove("x-forwarded-proto");
    for name in HOP_BY_HOP {
        if let Ok(header) = HeaderName::from_bytes(name.as_bytes()) {
            headers.remove(header);
        }
    }
}

/// Proxy a WebSocket upgrade: open a parallel upgrade to the upstream, relay the
/// 101 + headers back to the client, then splice the two upgraded byte streams
/// bidirectionally until either side closes.
async fn proxy_upgrade(
    req: Request<Incoming>,
    upstream: Arc<String>,
) -> Response<Full<Bytes>> {
    // Clone the head pieces so we can rebuild the handshake request for the
    // upstream while keeping the original `req` to register the client-side
    // on-upgrade future. A WS handshake carries no body, so we send an empty one
    // upstream.
    let method = req.method().clone();
    let uri = req.uri().clone();
    let headers = req.headers().clone();
    let version = req.version();

    let stream = match TcpStream::connect(upstream.as_str()).await {
        Ok(s) => s,
        Err(_) => return bad_gateway(),
    };
    let io = TokioIo::new(stream);
    let (mut sender, conn) = match hyper::client::conn::http1::handshake(io).await {
        Ok(pair) => pair,
        Err(_) => return bad_gateway(),
    };
    // The upstream connection must keep running so its upgrade can complete.
    let upstream_conn = tokio::spawn(async move {
        let _ = conn.with_upgrades().await;
    });

    let mut upstream_req = Request::new(Full::new(Bytes::new()));
    *upstream_req.method_mut() = method;
    *upstream_req.uri_mut() = uri;
    *upstream_req.headers_mut() = headers;
    *upstream_req.version_mut() = version;
    let upstream_resp = match sender.send_request(upstream_req).await {
        Ok(r) => r,
        Err(_) => {
            upstream_conn.abort();
            return bad_gateway();
        }
    };

    if upstream_resp.status() != StatusCode::SWITCHING_PROTOCOLS {
        // Upstream refused the upgrade (e.g. bad token -> non-101). Relay its
        // status/body straight back.
        let (rparts, rbody) = upstream_resp.into_parts();
        let bytes = rbody.collect().await.map(|b| b.to_bytes()).unwrap_or_default();
        upstream_conn.abort();
        return Response::from_parts(rparts, Full::new(bytes));
    }

    // Build the 101 to hand back to the client, preserving the upgrade headers
    // the browser needs (Sec-WebSocket-Accept, Upgrade, Connection, ...).
    let mut client_resp: Response<Full<Bytes>> = Response::new(Full::new(Bytes::new()));
    *client_resp.status_mut() = StatusCode::SWITCHING_PROTOCOLS;
    *client_resp.headers_mut() = upstream_resp.headers().clone();

    // Register both on-upgrade futures, then splice the two upgraded streams.
    let upstream_upgrade = hyper::upgrade::on(upstream_resp);
    let client_upgrade = hyper::upgrade::on(req);
    tokio::spawn(async move {
        let client_upgraded = match client_upgrade.await {
            Ok(u) => u,
            Err(_) => {
                upstream_conn.abort();
                return;
            }
        };
        let server_upgraded = match upstream_upgrade.await {
            Ok(u) => u,
            Err(_) => {
                upstream_conn.abort();
                return;
            }
        };
        relay_bidirectional(client_upgraded, server_upgraded).await;
        upstream_conn.abort();
    });

    client_resp
}

/// Copy bytes both ways between the client and upstream upgraded sockets until
/// either half closes.
async fn relay_bidirectional(client: Upgraded, server: Upgraded) {
    let mut client = TokioIo::new(client);
    let mut server = TokioIo::new(server);
    let _ = tokio::io::copy_bidirectional(&mut client, &mut server).await;
    let _ = client.shutdown().await;
    let _ = server.shutdown().await;
}
// === ANCHOR: TUNNEL_PROXY_SERVER_END ===

// === ANCHOR: TUNNEL_PROXY_TESTS_START ===
#[cfg(test)]
mod tests {
    use super::*;

    fn allows(method: &str, raw: &str) -> bool {
        viewer_allows(method, raw) == PathDecision::Allow
    }

    // --- ALLOW: the viewer surface ---
    #[test]
    fn allows_viewer_surface() {
        assert!(allows("GET", "/"), "SPA shell");
        assert!(allows("GET", "/index.html"), "SPA shell file");
        assert!(allows("GET", "/v/abc"), "viewer client route");
        assert!(allows("GET", "/v/some-long-token-XYZ"), "viewer token route");
        assert!(allows("GET", "/assets/index-CXOkeCk3.js"), "built JS asset");
        assert!(allows("GET", "/assets/index-Bn5esT6U.css"), "built CSS asset");
        assert!(allows("GET", "/favicon.ico"), "favicon");
        assert!(allows("GET", "/api/v1/viewer/utterances"), "viewer REST");
        assert!(allows("GET", "/api/v1/viewer/utterances?token=x&since=1"), "viewer REST w/ query");
        assert!(allows("GET", "/ws/viewer"), "viewer websocket");
        assert!(allows("GET", "/ws/viewer?token=abc"), "viewer websocket w/ token");
    }

    #[test]
    fn allows_capture_surface() {
        assert!(allows("GET", "/capture"), "capture SPA route");
        assert!(allows("POST", "/api/v1/auth/login"), "operator login");
        assert!(allows("POST", "/api/v1/sessions"), "create session");
        assert!(allows("POST", "/api/v1/sessions/abc-123/end"), "end session");
        assert!(allows("GET", "/api/v1/sessions/abc-123/utterances"), "preview polling");
        assert!(allows("POST", "/api/v1/sessions/abc-123/capture-token"), "capture token");
        assert!(allows("GET", "/ws/capture"), "capture websocket (upgrade is GET)");
    }

    #[test]
    fn denies_capture_adjacent_surface() {
        // 메서드 불일치
        assert!(!allows("GET", "/api/v1/auth/login"));
        assert!(!allows("GET", "/api/v1/sessions"), "회의기록 목록은 계속 차단");
        assert!(!allows("POST", "/api/v1/sessions/abc/utterances"));
        // 세션 상세·타 REST
        assert!(!allows("GET", "/api/v1/sessions/abc-123"), "세션 상세 차단");
        assert!(!allows("POST", "/api/v1/devices/self-enroll"), "영구키 발급 창구 차단");
        assert!(!allows("GET", "/ws/sidecar"), "영구키 WS 계속 차단");
        assert!(!allows("GET", "/ws/operator"), "operator WS 계속 차단");
        // <id> 와일드카드 경계
        assert!(!allows("POST", "/api/v1/sessions//end"), "빈 세그먼트 불가");
        assert!(!allows("POST", "/api/v1/sessions/a/b/end"), "중첩 세그먼트 불가");
        assert!(!allows("GET", "/api/v1/sessions/abc/utterances/extra"), "뒤 추가 세그먼트 불가");
        assert!(!allows("GET", "/capture/anything"), "capture는 정확 일치만");
    }

    #[test]
    fn capture_surface_defeats_smuggling() {
        // ".."가 <id> 자리로 들어오면 정규화가 세그먼트를 pop해 다른 경로가 된다 → deny
        assert_eq!(normalize_path("/api/v1/sessions/%2e%2e/end"), "/api/v1/end");
        assert!(!allows("POST", "/api/v1/sessions/%2e%2e/end"));
        // 대소문자는 정규화(소문자화)로 흡수된다
        assert!(allows("POST", "/API/V1/AUTH/LOGIN"));
        assert!(allows("GET", "/CAPTURE"));
        // 인코딩 슬래시 스머글링
        assert!(!allows("GET", "/ws%2fcapture/../sidecar"));
        assert!(!allows("GET", "/v/../ws/sidecar"));
    }

    // --- DENY: the operator / sidecar / auth surface (deny-by-default) ---
    #[test]
    fn denies_non_viewer_surface() {
        assert!(!allows("GET", "/ws/sidecar"), "durable device-key socket MUST be denied");
        assert!(!allows("GET", "/ws/sidecar?key=SECRET"), "device key never over tunnel");
        assert!(!allows("GET", "/ws/operator"), "operator socket denied");
        assert!(!allows("GET", "/api/v1/devices"), "devices REST denied");
        assert!(!allows("GET", "/api/v1/auth/login"), "auth login denied");
        assert!(!allows("GET", "/api/v1/sessions"), "operator sessions denied");
        assert!(!allows("GET", "/api/v1/operator/anything"), "operator REST denied");
        assert!(!allows("GET", "/api/v1/audio_stats"), "audio_stats denied");
        assert!(!allows("GET", "/api/v1/health"), "health denied (not viewer)");
        assert!(!allows("GET", "/api/v1/viewer"), "/api/v1/viewer (no trailing) denied");
        assert!(!allows("GET", "/api/v1/viewerX/utterances"), "prefix-confusion denied");
    }

    // --- DENY: bypass / smuggling attempts ---
    #[test]
    fn defeats_dotdot_traversal() {
        // `/api/v1/../devices` canonicalizes to `/api/devices` -> deny.
        assert!(!allows("GET", "/api/v1/../devices"));
        // `/api/v1/viewer/../../devices` -> `/api/devices` -> deny.
        assert!(!allows("GET", "/api/v1/viewer/../../devices"));
        // `/v/../ws/sidecar` -> `/ws/sidecar` -> deny.
        assert!(!allows("GET", "/v/../ws/sidecar"));
        assert!(!allows("GET", "/assets/../ws/sidecar"));
    }

    #[test]
    fn defeats_percent_encoding() {
        // `%2e%2e` == `..` ; `/api/v1/%2e%2e/devices` -> `/api/devices` -> deny.
        assert!(!allows("GET", "/api/v1/%2e%2e/devices"));
        // double-encoded `%252e%252e` -> `%2e%2e` -> `..`
        assert!(!allows("GET", "/api/v1/%252e%252e/devices"));
        // `/%2e%2e/ws/sidecar` -> `/ws/sidecar` -> deny.
        assert!(!allows("GET", "/%2e%2e/ws/sidecar"));
        // encoded slash `%2f` joining a denied path.
        assert!(!allows("GET", "/ws%2fsidecar"));
    }

    #[test]
    fn defeats_case_games() {
        assert!(!allows("GET", "/WS/SIDECAR"));
        assert!(!allows("GET", "/API/V1/DEVICES"));
        assert!(!allows("GET", "/Ws/Sidecar"));
        // `/V/../ws/sidecar` (upper V) -> `/ws/sidecar` -> deny.
        assert!(!allows("GET", "/V/../ws/sidecar"));
        // case must NOT break a legit allow either.
        assert!(allows("GET", "/V/abc"), "viewer route is case-insensitive on prefix");
    }

    #[test]
    fn defeats_slash_games() {
        assert!(!allows("GET", "//ws/sidecar"), "leading double slash");
        assert!(!allows("GET", "/ws//sidecar"), "embedded double slash");
        assert!(!allows("GET", "/./ws/sidecar"), "dot segment");
        assert!(!allows("GET", "/ws/sidecar/"), "trailing slash variant");
        assert!(!allows("GET", "/ws/sidecar."), "trailing dot (distinct path) denied");
        // backslash separator smuggling
        assert!(!allows("GET", "/ws\\sidecar"));
    }

    // Nit 3 (off-by-one): a `%XX` occupying the FINAL three bytes must decode.
    // The old `i + 2 < len` bound dropped a trailing escape, so `/v/.%2e` kept a
    // literal `%2e` instead of collapsing to `..` and could fail-open.
    #[test]
    fn decodes_trailing_percent_escape() {
        // bare trailing escape decodes to its byte.
        assert_eq!(percent_decode("%2e"), ".");
        assert_eq!(percent_decode("/a/%2e"), "/a/.");
        // a path ending in `%2e` (a `.` segment) canonicalizes away the trailing
        // dot rather than smuggling a literal `%2e` segment through.
        assert_eq!(normalize_path("/v/%2e"), "/v");
        // a trailing `%2e%2e` decodes to `..`, popping the prior segment — so
        // `/v/x/%2e%2e` -> `/v` (NOT a literal `%2e%2e` left dangling).
        assert_eq!(normalize_path("/v/x/%2e%2e"), "/v");
        // and a denied target reached via a trailing-escape `..` stays denied.
        assert!(!allows("GET", "/v/%2e%2e/ws/sidecar"));
    }

    // Nit 2 (hop-by-hop): the HTTP forwarder must drop hop-by-hop / connection /
    // upgrade-control headers and inbound X-Forwarded-* before relaying.
    #[test]
    fn strips_hop_by_hop_and_forwarded_headers() {
        use hyper::header::{HeaderMap, HeaderValue, CONNECTION, HOST, UPGRADE};
        let mut headers = HeaderMap::new();
        headers.insert(CONNECTION, HeaderValue::from_static("upgrade"));
        headers.insert(UPGRADE, HeaderValue::from_static("websocket"));
        headers.insert("keep-alive", HeaderValue::from_static("timeout=5"));
        headers.insert("transfer-encoding", HeaderValue::from_static("chunked"));
        headers.insert("x-forwarded-for", HeaderValue::from_static("1.2.3.4"));
        headers.insert("x-forwarded-host", HeaderValue::from_static("evil.example"));
        headers.insert("x-forwarded-proto", HeaderValue::from_static("https"));
        // A legit end-to-end header must survive.
        headers.insert(HOST, HeaderValue::from_static("localhost"));

        strip_hop_by_hop_headers(&mut headers);

        assert!(!headers.contains_key(CONNECTION), "Connection stripped");
        assert!(!headers.contains_key(UPGRADE), "Upgrade stripped (no HTTP-path upgrade smuggling)");
        assert!(!headers.contains_key("keep-alive"));
        assert!(!headers.contains_key("transfer-encoding"));
        assert!(!headers.contains_key("x-forwarded-for"));
        assert!(!headers.contains_key("x-forwarded-host"));
        assert!(!headers.contains_key("x-forwarded-proto"));
        assert_eq!(headers.get(HOST).unwrap(), "localhost", "end-to-end header preserved");
    }

    #[test]
    fn normalize_examples() {
        assert_eq!(normalize_path("/api/v1/../devices"), "/api/devices");
        assert_eq!(normalize_path("//ws/sidecar"), "/ws/sidecar");
        assert_eq!(normalize_path("/V/../ws/sidecar"), "/ws/sidecar");
        assert_eq!(normalize_path("/api/v1/%2e%2e/devices"), "/api/devices");
        assert_eq!(normalize_path("/api/v1/viewer/utterances?token=x"), "/api/v1/viewer/utterances");
        assert_eq!(normalize_path("/"), "/");
    }
}
// === ANCHOR: TUNNEL_PROXY_TESTS_END ===
// === ANCHOR: TUNNEL_PROXY_END ===
