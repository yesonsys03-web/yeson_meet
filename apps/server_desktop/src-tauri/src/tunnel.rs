// === ANCHOR: TUNNEL_START ===
//! cloudflared quick-tunnel process manager (P4.1a, Part 2).
//!
//! Spawns the bundled `cloudflared` as a process-group leader pointing at the
//! viewer-only proxy (`tunnel_proxy.rs`), captures the random
//! `https://<rand>.trycloudflare.com` URL from its log output, and tears the
//! whole group down on stop/app-exit — REUSING (not copying verbatim) the proven
//! process-group spawn + SIGTERM->grace->SIGKILL teardown pattern from
//! `server_process.rs`. Detection of degradation (child-exit watch) is P4.2; this
//! sub-slice is spawn + capture + teardown + status only.
//!
//! The binary is resolved from a vendored `binaries/cloudflared-<triple>/`
//! resource (full cross-OS vendoring is P4.3; the dev-host triple is vendored
//! now). `YESON_CLOUDFLARED_BIN` overrides the path so tests can point at the
//! `binaries/cloudflared-stub.sh` fake (no real binary / network needed).
use std::{
    io::{BufRead, BufReader},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        mpsc,
        Mutex,
    },
    thread,
    time::{Duration, Instant},
};

use serde::Serialize;

/// Env var that overrides the cloudflared binary path (tests point this at the
/// stub script).
const CLOUDFLARED_BIN_ENV: &str = "YESON_CLOUDFLARED_BIN";

/// How long to wait for the `trycloudflare.com` URL to appear in the logs before
/// giving up (mirrors `deploy/tunnel-quick.sh`'s ~60s budget).
const URL_CAPTURE_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Default)]
pub struct TunnelState {
    inner: Mutex<Option<RunningTunnel>>,
    /// Sticky degraded record (P4.2): the now-dead public URL, set when a
    /// *running* tunnel's cloudflared child exits WITHOUT an operator stop. The
    /// 1s `tunnel_status` poll is the detector — a clean `stop_tunnel` empties
    /// `inner` so its exit is never seen here, whereas an unexpected exit leaves
    /// `inner` populated with a dead child. Stays set (so the console banner
    /// persists) until the operator re-publishes (`start_tunnel`) or falls back
    /// to LAN (`stop_tunnel`), both of which clear it.
    degraded: Mutex<Option<String>>,
}

/// A live cloudflared child plus the captured public URL and the proxy it
/// fronts. Holding the `ProxyHandle` keeps the viewer-only proxy alive for the
/// tunnel's lifetime and lets us stop it on teardown.
struct RunningTunnel {
    child: Child,
    url: String,
    started_at: Instant,
    proxy: crate::tunnel_proxy::ProxyHandle,
}

impl Drop for TunnelState {
    fn drop(&mut self) {
        if let Ok(mut slot) = self.inner.lock() {
            if let Some(mut running) = slot.take() {
                running.proxy.stop();
                terminate_group(&mut running.child);
            }
        }
    }
}

/// Stop the tunnel (and its proxy) if running. Safe to call on app exit.
pub fn shutdown(state: &TunnelState) {
    if let Ok(mut slot) = state.inner.lock() {
        if let Some(mut running) = slot.take() {
            running.proxy.stop();
            terminate_group(&mut running.child);
        }
    }
}

// === ANCHOR: TUNNEL_TEARDOWN_START ===
/// Terminate cloudflared and its whole process group, mirroring
/// `server_process::terminate_group`: SIGTERM the negative pgid (cloudflared is
/// spawned as a group leader) so any helper it forks is reaped too, wait a short
/// grace, then SIGKILL the group as a backstop. cloudflared has no durable DB to
/// flush, so the grace is short (~1s) — just enough for a clean connection close.
fn terminate_group(child: &mut Child) {
    #[cfg(unix)]
    {
        let pgid = child.id() as i32; // == pid: spawned via process_group(0)
        let _ = Command::new("/bin/kill")
            .arg("-TERM")
            .arg(format!("-{pgid}"))
            .status();
        let mut exited = false;
        for _ in 0..10 {
            if matches!(child.try_wait(), Ok(Some(_))) {
                exited = true;
                break;
            }
            thread::sleep(Duration::from_millis(100));
        }
        if !exited {
            let _ = Command::new("/bin/kill")
                .arg("-KILL")
                .arg(format!("-{pgid}"))
                .status();
        }
    }
    #[cfg(not(unix))]
    {
        // Windows has no process groups: `child.kill()` is TerminateProcess on
        // the TOP handle only, so cloudflared's edge helper(s) would be orphaned
        // and keep the (now-stale) tunnel alive after the console closes.
        // `taskkill /T` reaps the whole subtree by PID — the Windows analog of
        // the Unix `kill -TERM -pgid` above. CREATE_NO_WINDOW keeps it from
        // flashing a console window. Falls back to `child.kill()` if taskkill is
        // unavailable or the tree was already partway down.
        let pid = child.id();
        let mut kill = Command::new("taskkill");
        kill.args(["/PID", &pid.to_string(), "/T", "/F"]);
        set_no_window(&mut kill);
        let reaped = kill.status().map(|status| status.success()).unwrap_or(false);
        if !reaped {
            let _ = child.kill();
        }
    }
    let _ = child.wait();
}
// === ANCHOR: TUNNEL_TEARDOWN_END ===

/// Spawn cloudflared as a new process-group leader so the whole subtree can be
/// signalled together at teardown (same rationale as the server sidecar).
fn set_process_group(command: &mut Command) {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(not(unix))]
    {
        let _ = command;
    }
}

/// Suppress the console window Windows would otherwise pop. No-op elsewhere.
fn set_no_window(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    {
        let _ = command;
    }
}

// === ANCHOR: TUNNEL_LOCATE_START ===
/// Locate the bundled `cloudflared` binary. `YESON_CLOUDFLARED_BIN` overrides
/// everything (tests / advanced operators). Otherwise resolve a
/// `binaries/cloudflared-<triple>/cloudflared[.exe]` resource via the same
/// `current_exe()` / packaged-Resources / dev-`binaries/` candidate logic as
/// `server_process::locate_bundled_server`. Full cross-OS vendoring is P4.3; the
/// dev-host triple is vendored now. Returns None when absent.
pub fn locate_cloudflared() -> Option<PathBuf> {
    if let Ok(override_path) = std::env::var(CLOUDFLARED_BIN_ENV) {
        let path = PathBuf::from(override_path);
        if path.is_file() {
            return Some(path);
        }
        return None;
    }

    let triple: &str = if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        "x86_64-pc-windows-msvc"
    } else if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        "aarch64-apple-darwin"
    } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
        "x86_64-apple-darwin"
    } else if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
        "x86_64-unknown-linux-gnu"
    } else {
        return None;
    };
    let suffix = if cfg!(target_os = "windows") { ".exe" } else { "" };
    let dir_name = format!("cloudflared-{triple}");
    let bin_name = format!("cloudflared{suffix}");

    let mut roots: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            roots.push(dir.to_path_buf());
            roots.push(dir.join("binaries"));
            if let Some(contents) = dir.parent() {
                roots.push(contents.join("Resources"));
                roots.push(contents.join("Resources").join("binaries"));
            }
        }
    }
    roots.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries"));

    roots
        .into_iter()
        .map(|root| root.join(&dir_name).join(&bin_name))
        .find(|path| path.is_file())
}
// === ANCHOR: TUNNEL_LOCATE_END ===

// === ANCHOR: TUNNEL_CAPTURE_URL_START ===
/// Capture the first `https://<rand>.trycloudflare.com` URL from a cloudflared
/// output line. Pure + dependency-free (a tiny scanner instead of a regex crate)
/// so it is trivially unit-testable, matching the `tunnel-quick.sh:20` pattern
/// `https://[a-z0-9-]+\.trycloudflare\.com`.
pub fn extract_tunnel_url(line: &str) -> Option<String> {
    const MARKER: &str = "https://";
    const SUFFIX: &str = ".trycloudflare.com";
    let mut search_from = 0;
    while let Some(rel) = line[search_from..].find(MARKER) {
        let start = search_from + rel;
        let after_scheme = start + MARKER.len();
        // host label chars: a-z 0-9 '-' (quick-tunnel hosts are lower-case).
        let host_end = line[after_scheme..]
            .find(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-'))
            .map(|i| after_scheme + i)
            .unwrap_or(line.len());
        let host = &line[after_scheme..host_end];
        if !host.is_empty() && line[host_end..].starts_with(SUFFIX) {
            let end = host_end + SUFFIX.len();
            return Some(line[start..end].to_string());
        }
        search_from = after_scheme;
    }
    None
}
// === ANCHOR: TUNNEL_CAPTURE_URL_END ===

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TunnelStatus {
    running: bool,
    url: Option<String>,
    /// The local proxy port cloudflared targets (for diagnostics).
    vport: Option<u16>,
    uptime_secs: Option<u64>,
    detail: String,
    /// P4.2: true when the public tunnel dropped on its own (cloudflared exited
    /// without an operator stop). `running` is false and `url` carries the dead
    /// URL so the console can explain which link stopped working. LAN viewing is
    /// unaffected.
    degraded: bool,
}

fn running_status(running: &RunningTunnel, detail: impl Into<String>) -> TunnelStatus {
    TunnelStatus {
        running: true,
        url: Some(running.url.clone()),
        vport: Some(running.proxy.vport),
        uptime_secs: Some(running.started_at.elapsed().as_secs()),
        detail: detail.into(),
        degraded: false,
    }
}

fn stopped_status(detail: impl Into<String>) -> TunnelStatus {
    TunnelStatus {
        running: false,
        url: None,
        vport: None,
        uptime_secs: None,
        detail: detail.into(),
        degraded: false,
    }
}

/// Status for a tunnel that dropped on its own (P4.2). Not running, but carries
/// the dead URL + the degraded flag so the console raises the fallback banner.
fn degraded_status(url: String) -> TunnelStatus {
    TunnelStatus {
        running: false,
        url: Some(url),
        vport: None,
        uptime_secs: None,
        detail: "public tunnel dropped — LAN viewing unaffected".to_string(),
        degraded: true,
    }
}

// === ANCHOR: TUNNEL_START_TUNNEL_START ===
/// Start the viewer-only proxy + cloudflared, capture the public URL, and return
/// it. P4.1a is spawn/capture/teardown ONLY — it does NOT write `viewer_base` or
/// restart the server (that lifecycle is P4.1b). `server_port` is the running
/// bundled server's port; the proxy fronts it and cloudflared targets the proxy.
pub fn start_tunnel(state: &TunnelState, server_port: u16) -> Result<TunnelStatus, String> {
    let mut slot = state
        .inner
        .lock()
        .map_err(|_| "tunnel state lock failed".to_string())?;

    if let Some(running) = slot.as_mut() {
        if running
            .child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_none()
        {
            return Ok(running_status(running, "tunnel is already running"));
        }
    }
    *slot = None;
    // Re-publishing clears any prior degraded record (P4.2 recovery, AC P4.2.3).
    clear_degraded(state);

    let cloudflared = locate_cloudflared().ok_or_else(|| {
        "bundled cloudflared binary not found (set YESON_CLOUDFLARED_BIN or vendor it)".to_string()
    })?;

    // Bring up the viewer-only proxy on an ephemeral loopback port, then point
    // cloudflared at it (NEVER directly at the server's port).
    let proxy = start_proxy_blocking(server_port)
        .map_err(|error| format!("failed to start viewer proxy: {error}"))?;
    let vport = proxy.vport;

    let mut command = Command::new(&cloudflared);
    command
        .arg("tunnel")
        .arg("--no-autoupdate")
        // Force the HTTP2 edge protocol (TCP/443) instead of the default QUIC
        // (UDP/7844). On networks that throttle or block UDP, the QUIC dial
        // stalls until it times out (~5s) before falling back, delaying or
        // failing tunnel registration; http2 connects over TCP promptly and
        // reliably, so Go Live comes up faster and more dependably.
        .arg("--protocol")
        .arg("http2")
        .arg("--url")
        .arg(format!("http://localhost:{vport}"))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    set_process_group(&mut command);
    set_no_window(&mut command);

    let mut child = command.spawn().map_err(|error| {
        proxy.stop();
        format!("failed to start cloudflared: {error}")
    })?;

    // cloudflared prints the URL banner to stderr; some builds use stdout. Watch
    // BOTH and take the first match. The forwarder threads send any captured URL
    // over a channel so the (sync) command path can block up to the timeout.
    let (tx, rx) = mpsc::channel::<String>();
    spawn_url_watch(child.stdout.take(), tx.clone());
    spawn_url_watch(child.stderr.take(), tx);

    let url = match rx.recv_timeout(URL_CAPTURE_TIMEOUT) {
        Ok(url) => url,
        Err(_) => {
            // No URL within the budget — tear everything down and report.
            terminate_group(&mut child);
            proxy.stop();
            return Err(
                "cloudflared did not report a trycloudflare.com URL within the timeout".to_string(),
            );
        }
    };

    let running = RunningTunnel {
        child,
        url: url.clone(),
        started_at: Instant::now(),
        proxy,
    };
    let status = running_status(&running, "tunnel started");
    *slot = Some(running);
    Ok(status)
}
// === ANCHOR: TUNNEL_START_TUNNEL_END ===

/// Stop the tunnel + proxy if running.
pub fn stop_tunnel(state: &TunnelState) -> Result<TunnelStatus, String> {
    let mut slot = state
        .inner
        .lock()
        .map_err(|_| "tunnel state lock failed".to_string())?;
    let Some(mut running) = slot.take() else {
        // Falling back to LAN clears any degraded record so the banner drops.
        clear_degraded(state);
        return Ok(stopped_status("tunnel is not running"));
    };
    running.proxy.stop();
    terminate_group(&mut running.child);
    clear_degraded(state);
    Ok(stopped_status("tunnel stopped"))
}

/// Clear the sticky P4.2 degraded record. Called by both lifecycle ends
/// (re-publish and LAN fallback) — either way the banner should drop.
fn clear_degraded(state: &TunnelState) {
    if let Ok(mut deg) = state.degraded.lock() {
        *deg = None;
    }
}

/// Report whether the tunnel is running and its current URL. This 1s-polled
/// path is also the P4.2 degraded DETECTOR: if a tunnel we believe is running
/// has actually exited (cloudflared died on its own — a clean stop would have
/// emptied `inner`), record the dead URL as degraded so the console raises the
/// fallback banner, and keep reporting it until the operator re-publishes or
/// falls back to LAN.
pub fn tunnel_status(state: &TunnelState) -> Result<TunnelStatus, String> {
    let mut slot = state
        .inner
        .lock()
        .map_err(|_| "tunnel state lock failed".to_string())?;
    let Some(running) = slot.as_mut() else {
        // No live tunnel: surface a sticky degraded record if one was set.
        let deg = state
            .degraded
            .lock()
            .map_err(|_| "tunnel state lock failed".to_string())?;
        return Ok(match deg.as_ref() {
            Some(url) => degraded_status(url.clone()),
            None => stopped_status("tunnel is not running"),
        });
    };
    if running
        .child
        .try_wait()
        .map_err(|error| error.to_string())?
        .is_some()
    {
        // Unexpected exit: tear the proxy down, record the dead URL as degraded.
        let dead = slot.take().unwrap();
        dead.proxy.stop();
        let url = dead.url.clone();
        if let Ok(mut deg) = state.degraded.lock() {
            *deg = Some(url.clone());
        }
        return Ok(degraded_status(url));
    }
    Ok(running_status(running, "tunnel is running"))
}

/// Read cloudflared output line-by-line and send the first captured tunnel URL
/// over `tx`, then keep draining so the pipe never blocks the child.
fn spawn_url_watch<R>(pipe: Option<R>, tx: mpsc::Sender<String>)
where
    R: std::io::Read + Send + 'static,
{
    let Some(pipe) = pipe else { return };
    thread::spawn(move || {
        let reader = BufReader::new(pipe);
        let mut sent = false;
        for line in reader.lines() {
            let Ok(line) = line else { break };
            if !sent {
                if let Some(url) = extract_tunnel_url(&line) {
                    sent = tx.send(url).is_ok();
                }
            }
        }
    });
}

/// Start the async viewer proxy from this synchronous command path. The proxy is
/// tokio-based; we run a tiny current-thread runtime just to perform the bind
/// (which yields the chosen port) and leak it so the spawned accept loop keeps
/// running for the tunnel's lifetime. The handle's `stop()` ends the loop.
fn start_proxy_blocking(
    server_port: u16,
) -> std::io::Result<crate::tunnel_proxy::ProxyHandle> {
    use std::sync::OnceLock;
    use tokio::runtime::Runtime;
    // A shared multi-thread runtime hosts the proxy's accept loop + per-connection
    // tasks for the whole app lifetime. Created once, lazily.
    static RUNTIME: OnceLock<Runtime> = OnceLock::new();
    let rt = RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .expect("failed to build tunnel proxy runtime")
    });
    rt.block_on(crate::tunnel_proxy::start_proxy(server_port))
}

// === ANCHOR: TUNNEL_LIVE_SESSIONS_START ===
/// Query the bundled server's read-only live-meeting count over loopback. This
/// is the restart gate (BINDING must-fix #2): the lifecycle restart SIGTERMs the
/// server's process group and hard-kills any ACTIVE meeting (in-memory AI session
/// state), so going public MUST refuse while a meeting is live. We hit
/// `GET /api/v1/health/live-sessions` (added in `apps/server/api/v1/health.py`),
/// whose `status == "live"` count is the SAME authoritative DB flag the safety
/// watchdog queries. Reuses the `hyper` client already vendored for the proxy
/// (no new dep); a connect/parse failure is surfaced to the caller (fail-closed:
/// the lifecycle treats an unknown count as "do not restart").
fn live_session_count(server_port: u16) -> Result<u32, String> {
    use std::sync::OnceLock;
    use tokio::runtime::Runtime;
    static RUNTIME: OnceLock<Runtime> = OnceLock::new();
    let rt = RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("failed to build live-session probe runtime")
    });
    rt.block_on(fetch_live_session_count(server_port))
}

/// 프로브 전체(연결→요청→본문)의 상한. 포트를 점유한 외부 프로세스가 TCP만
/// 받고 HTTP 응답을 안 하면(실기 Windows 10001 — 보안SW 포트 감시류) 이
/// 왕복이 무한정 걸린다. 이 함수는 1초 상태 폴링 경로에서 불리므로 상한이
/// 없으면 앱이 통째로 얼었다. 타임아웃=Err(fail-closed) — 라이프사이클은
/// unknown count를 "재시작 금지"로 보는 기존 규약 그대로다.
const LIVE_PROBE_TIMEOUT: Duration = Duration::from_secs(2);

async fn fetch_live_session_count(server_port: u16) -> Result<u32, String> {
    tokio::time::timeout(
        LIVE_PROBE_TIMEOUT,
        fetch_live_session_count_inner(server_port),
    )
    .await
    .map_err(|_| {
        format!(
            "live-session probe timed out after {}s on 127.0.0.1:{server_port} \
             (port answered TCP but not HTTP — foreign process on the port?)",
            LIVE_PROBE_TIMEOUT.as_secs()
        )
    })?
}

async fn fetch_live_session_count_inner(server_port: u16) -> Result<u32, String> {
    use http_body_util::{BodyExt, Full};
    use hyper::body::Bytes;
    use hyper::{Request, StatusCode};
    use hyper_util::rt::TokioIo;
    use tokio::net::TcpStream;

    let addr = format!("127.0.0.1:{server_port}");
    let stream = TcpStream::connect(&addr)
        .await
        .map_err(|error| format!("server unreachable on {addr}: {error}"))?;
    let io = TokioIo::new(stream);
    let (mut sender, conn) = hyper::client::conn::http1::handshake::<_, Full<Bytes>>(io)
        .await
        .map_err(|error| format!("server handshake failed: {error}"))?;
    tokio::spawn(async move {
        let _ = conn.await;
    });

    let req = Request::builder()
        .method("GET")
        .uri("/api/v1/health/live-sessions")
        .header("host", addr.as_str())
        .body(Full::new(Bytes::new()))
        .map_err(|error| format!("failed to build live-session request: {error}"))?;

    let resp = sender
        .send_request(req)
        .await
        .map_err(|error| format!("live-session request failed: {error}"))?;
    if resp.status() != StatusCode::OK {
        return Err(format!(
            "live-session probe returned HTTP {}",
            resp.status().as_u16()
        ));
    }
    let body = resp
        .into_body()
        .collect()
        .await
        .map_err(|error| format!("failed to read live-session body: {error}"))?
        .to_bytes();
    parse_live_count(&body)
}

/// Parse the `{"live": N}` body from the live-session endpoint. Tiny + pure so
/// it is unit-testable without a server (dependency-free scan, matching the
/// `extract_tunnel_url` approach). Fail-closed on any unexpected shape.
fn parse_live_count(body: &[u8]) -> Result<u32, String> {
    let text = std::str::from_utf8(body).map_err(|_| "live-session body not UTF-8".to_string())?;
    let key = "\"live\"";
    let after_key = text
        .find(key)
        .map(|i| i + key.len())
        .ok_or_else(|| "live-session body missing \"live\" field".to_string())?;
    let rest = &text[after_key..];
    let colon = rest
        .find(':')
        .ok_or_else(|| "malformed live-session body".to_string())?;
    let digits: String = rest[colon + 1..]
        .chars()
        .skip_while(|c| c.is_whitespace())
        .take_while(|c| c.is_ascii_digit())
        .collect();
    digits
        .parse::<u32>()
        .map_err(|_| "live-session count is not a number".to_string())
}
// === ANCHOR: TUNNEL_LIVE_SESSIONS_END ===

// === ANCHOR: TUNNEL_COMMANDS_START ===
// P4.1b lifecycle commands. `start_tunnel_cmd` drives the FULL public-mode flow:
// bring up the viewer-only proxy + cloudflared
// (P4.1a) and capture the URL → persist it as the keychain `viewer_base` (for
// cold-start env injection) → publish it to `{STORAGE_ROOT}/viewer_base.txt` so
// the RUNNING server mints tunnel viewer URLs immediately (the server reads that
// file fresh per session, precedence file > env > default — no restart). The old
// stop→start server restart is gone: the file write is light (no ~2-3s server
// downtime / status blip). cloudflared + the proxy are SEPARATE processes; the
// proxy reconnects per-request to `127.0.0.1:<port>` and cloudflared holds its
// own edge connection, so the server keeps running untouched.
#[tauri::command]
pub fn start_tunnel_cmd(
    app: tauri::AppHandle,
    server_port: u16,
    tunnel_state: tauri::State<'_, TunnelState>,
    server_state: tauri::State<'_, crate::server_process::ServerProcessState>,
) -> Result<TunnelStatus, String> {
    let _ = &server_state; // retained for signature/UI parity; no restart needed now.
    // NOTE: the old "refuse while a meeting is live" gate is gone. Its rationale
    // (going public used to restart the server, killing the live meeting) died
    // with the restart-free flow above, and blocking re-publish also blocked
    // RECOVERY when cloudflared dropped mid-meeting. Going public now never
    // touches the running server; a mid-meeting re-publish just mints a NEW
    // trycloudflare host, so the operator must re-share the viewer link.

    // 1. Bring up proxy + cloudflared and capture the public URL (P4.1a).
    let status = start_tunnel(&tunnel_state, server_port)?;
    let Some(url) = status.url.clone() else {
        return Ok(status);
    };

    // 2. Persist the captured URL as the keychain viewer_base so a future COLD
    //    start injects VIEWER_BASE via inject_secrets. If this fails, tear the
    //    tunnel back down so we do not leave a public edge whose URL is unminted.
    if let Err(error) = crate::server_config::set_viewer_base(&url) {
        let _ = stop_tunnel(&tunnel_state);
        return Err(format!("failed to persist viewer_base: {error}"));
    }

    // 3. Publish the URL to {STORAGE_ROOT}/viewer_base.txt so the ALREADY-RUNNING
    //    server mints tunnel viewer URLs at the next session creation — no
    //    restart. If the write fails, tear the tunnel back down so we do not
    //    leave a live public edge the server will never advertise.
    if let Err(error) = crate::server_process::write_viewer_base_file(&app, &url) {
        let _ = crate::server_config::set_viewer_base("");
        let _ = stop_tunnel(&tunnel_state);
        return Err(format!("failed to publish viewer_base file: {error}"));
    }

    Ok(status)
}

#[tauri::command]
pub fn stop_tunnel_cmd(
    app: tauri::AppHandle,
    tunnel_state: tauri::State<'_, TunnelState>,
    server_state: tauri::State<'_, crate::server_process::ServerProcessState>,
) -> Result<TunnelStatus, String> {
    let _ = &server_state; // retained for signature/UI parity; no restart needed now.
    // Tear down cloudflared + proxy first so the public edge is gone immediately.
    let status = stop_tunnel(&tunnel_state)?;

    // Revert the viewer base to env/LAN WITHOUT a restart: delete the runtime
    // {STORAGE_ROOT}/viewer_base.txt so the running server falls back to its env
    // VIEWER_BASE (LAN default) at the next session, and clear the keychain copy
    // so a future cold start is LAN-only too. Both are best-effort/idempotent —
    // the public edge is already down, so even if revert fails no traffic reaches
    // the stale host. Deleting the file mid-meeting only changes the base for
    // sessions created AFTER this point; existing viewer URLs are unaffected.
    crate::server_config::set_viewer_base("")?;
    crate::server_process::remove_viewer_base_file(&app)?;

    Ok(status)
}

#[tauri::command]
pub fn tunnel_status_cmd(state: tauri::State<'_, TunnelState>) -> Result<TunnelStatus, String> {
    tunnel_status(&state)
}

/// Read-only live-meeting count for the UI's PRIMARY guard: the server console
/// hides/disables "Go live (public)" while a meeting is active (the command-level
/// refuse in `start_tunnel_cmd` is the backstop). Returns the count when the
/// server is running, or `None` when it is not (no meeting can be live then).
/// async 커맨드 — 동기 커맨드는 Tauri v2에서 메인 스레드에서 실행되므로,
/// 이 1초 주기 폴링이 네트워크에서 지연되면(위 LIVE_PROBE_TIMEOUT 참조)
/// 그 시간만큼 UI 전체가 굳는다. async로 런타임 풀에서 돌리면 최악의 경우에도
/// 폴링 한 번이 늦을 뿐 앱은 살아 있다.
#[tauri::command]
pub async fn live_session_count_cmd(
    server_state: tauri::State<'_, crate::server_process::ServerProcessState>,
) -> Result<Option<u32>, String> {
    let port = crate::server_process::current_port(&server_state);
    match port {
        Some(port) => fetch_live_session_count(port).await.map(Some),
        None => Ok(None),
    }
}

/// Best-effort primary LAN IPv4 of THIS machine. Opens a UDP socket and
/// `connect`s it to a public address — no packet is ever sent; the connect just
/// makes the OS pick the outbound interface, so `local_addr()` reveals the LAN
/// IP. Returns None on loopback-only / unusual networking (caller falls back to
/// a generic hint).
fn local_lan_ipv4() -> Option<std::net::IpAddr> {
    use std::net::UdpSocket;
    let sock = UdpSocket::bind("0.0.0.0:0").ok()?;
    sock.connect("8.8.8.8:80").ok()?;
    let ip = sock.local_addr().ok()?.ip();
    if ip.is_loopback() || ip.is_unspecified() {
        None
    } else {
        Some(ip)
    }
}

/// LAN viewer base (`http://<lan-ip>:<port>`) for the P4.2 degraded banner's
/// fallback URL. LAN viewers reach the bundled server (and the viewer SPA it now
/// serves) directly on this address regardless of the public tunnel's state.
/// Returns None when the LAN IP can't be determined.
#[tauri::command]
pub fn lan_viewer_base_cmd(server_port: u16) -> Option<String> {
    local_lan_ipv4().map(|ip| format!("http://{ip}:{server_port}"))
}
// === ANCHOR: TUNNEL_COMMANDS_END ===

// === ANCHOR: TUNNEL_TESTS_START ===
#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex as StdMutex;

    // Serialize the two tests that mutate the process-global
    // `YESON_CLOUDFLARED_BIN` env var, since `cargo test` runs them on parallel
    // threads (one test's `remove_var` would otherwise race the other's spawn).
    static ENV_LOCK: StdMutex<()> = StdMutex::new(());

    #[test]
    fn extracts_url_from_banner_line() {
        let line = "|  https://fake-xyz.trycloudflare.com                |";
        assert_eq!(
            extract_tunnel_url(line).as_deref(),
            Some("https://fake-xyz.trycloudflare.com")
        );
    }

    #[test]
    fn extracts_url_from_real_log_shape() {
        let line = "2024-01-01T00:00:00Z INF +-----+ | https://blue-cat-42.trycloudflare.com | +-----+";
        assert_eq!(
            extract_tunnel_url(line).as_deref(),
            Some("https://blue-cat-42.trycloudflare.com")
        );
    }

    #[test]
    fn ignores_non_tunnel_lines() {
        assert_eq!(extract_tunnel_url("INF Starting tunnel"), None);
        assert_eq!(extract_tunnel_url("https://example.com/foo"), None);
        assert_eq!(extract_tunnel_url("https://.trycloudflare.com"), None);
    }

    #[test]
    fn parses_live_session_count() {
        assert_eq!(parse_live_count(b"{\"live\":0}").unwrap(), 0);
        assert_eq!(parse_live_count(b"{\"live\": 3}").unwrap(), 3);
        assert_eq!(parse_live_count(b"{\"live\" : 42 }").unwrap(), 42);
        // restart-gate semantics: any positive count blocks going public.
        assert!(parse_live_count(b"{\"live\":1}").unwrap() > 0);
    }

    #[test]
    fn rejects_malformed_live_session_bodies() {
        assert!(parse_live_count(b"{}").is_err(), "missing field");
        assert!(parse_live_count(b"{\"live\":\"x\"}").is_err(), "non-numeric");
        assert!(parse_live_count(b"not json").is_err());
    }

    #[test]
    fn degraded_status_is_sticky_until_cleared() {
        let state = TunnelState::default();
        // Clean slate: not running, not degraded.
        let s = tunnel_status(&state).unwrap();
        assert!(!s.running && !s.degraded);

        // Simulate a detected drop (cloudflared died under us → recorded).
        *state.degraded.lock().unwrap() = Some("https://dead.trycloudflare.com".to_string());
        let s = tunnel_status(&state).unwrap();
        assert!(!s.running, "a degraded tunnel is not running");
        assert!(s.degraded, "status must report degraded");
        assert_eq!(s.url.as_deref(), Some("https://dead.trycloudflare.com"));

        // Polling again keeps reporting it (sticky, so the banner persists).
        assert!(tunnel_status(&state).unwrap().degraded);

        // Falling back to LAN (operator stop) clears it.
        stop_tunnel(&state).unwrap();
        let s = tunnel_status(&state).unwrap();
        assert!(!s.degraded, "stop must clear the degraded flag");
    }

    #[test]
    fn locate_honors_env_override_to_stub() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let stub = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("binaries")
            .join("cloudflared-stub.sh");
        assert!(stub.is_file(), "stub script must exist for the spawn test");
        std::env::set_var(CLOUDFLARED_BIN_ENV, &stub);
        assert_eq!(locate_cloudflared(), Some(stub));
        std::env::remove_var(CLOUDFLARED_BIN_ENV);
    }

    // Spawn the STUB (no real binary / network): assert the URL is captured and
    // the process group is reaped on stop with no orphan.
    #[cfg(unix)]
    #[test]
    fn spawn_capture_teardown_with_stub() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let stub = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("binaries")
            .join("cloudflared-stub.sh");
        if !stub.is_file() {
            panic!("stub missing: {}", stub.display());
        }
        std::env::set_var(CLOUDFLARED_BIN_ENV, &stub);

        let state = TunnelState::default();
        // Port doesn't need a live server: the proxy binds, cloudflared-stub
        // ignores the --url, and we only assert URL capture + teardown.
        let status = start_tunnel(&state, 8000).expect("start_tunnel should succeed with the stub");
        assert!(status.running);
        assert_eq!(status.url.as_deref(), Some("https://fake-xyz.trycloudflare.com"));

        // Capture the child PID before teardown so we can assert it is reaped.
        let pid = {
            let slot = state.inner.lock().unwrap();
            slot.as_ref().unwrap().child.id() as i32
        };

        let stopped = stop_tunnel(&state).expect("stop_tunnel should succeed");
        assert!(!stopped.running);

        // The process group must be gone: kill -0 on the (now-dead) pgid fails.
        let alive = Command::new("/bin/kill")
            .arg("-0")
            .arg(format!("-{pid}"))
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        assert!(!alive, "cloudflared process group must be reaped (no orphan)");

        std::env::remove_var(CLOUDFLARED_BIN_ENV);
    }

    // Windows 실기(2026-07-23, 포트 10001): 포트를 점유한 외부 프로세스가 TCP만
    // 받고 HTTP 응답을 안 하면 프로브가 무한 대기 → 1초 폴링 경로에서 앱 전체
    // 프리즈. 상한(LIVE_PROBE_TIMEOUT)이 빠른 Err로 끊어야 한다.
    #[test]
    fn live_session_probe_times_out_on_silent_listener() {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        // accept만 하고 아무 응답도 하지 않는 리스너를 잡아둔다.
        let hold = std::thread::spawn(move || {
            let conn = listener.accept();
            std::thread::sleep(Duration::from_secs(6));
            drop(conn);
        });
        let started = Instant::now();
        let out = live_session_count(port);
        assert!(out.is_err(), "silent listener must fail the probe");
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "probe must be bounded by LIVE_PROBE_TIMEOUT, took {:?}",
            started.elapsed()
        );
        let _ = hold.join();
    }
}
// === ANCHOR: TUNNEL_TESTS_END ===
// === ANCHOR: TUNNEL_END ===
