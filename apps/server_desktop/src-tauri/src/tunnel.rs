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
        let _ = child.kill();
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
}

fn running_status(running: &RunningTunnel, detail: impl Into<String>) -> TunnelStatus {
    TunnelStatus {
        running: true,
        url: Some(running.url.clone()),
        vport: Some(running.proxy.vport),
        uptime_secs: Some(running.started_at.elapsed().as_secs()),
        detail: detail.into(),
    }
}

fn stopped_status(detail: impl Into<String>) -> TunnelStatus {
    TunnelStatus {
        running: false,
        url: None,
        vport: None,
        uptime_secs: None,
        detail: detail.into(),
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
        return Ok(stopped_status("tunnel is not running"));
    };
    running.proxy.stop();
    terminate_group(&mut running.child);
    Ok(stopped_status("tunnel stopped"))
}

/// Report whether the tunnel is running and its current URL.
pub fn tunnel_status(state: &TunnelState) -> Result<TunnelStatus, String> {
    let mut slot = state
        .inner
        .lock()
        .map_err(|_| "tunnel state lock failed".to_string())?;
    let Some(running) = slot.as_mut() else {
        return Ok(stopped_status("tunnel is not running"));
    };
    if running
        .child
        .try_wait()
        .map_err(|error| error.to_string())?
        .is_some()
    {
        let running = slot.take().unwrap();
        running.proxy.stop();
        return Ok(stopped_status("tunnel exited"));
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

// === ANCHOR: TUNNEL_COMMANDS_START ===
// Thin Tauri command wrappers. P4.1a registers these so they are callable, but
// they do ONLY spawn/capture/teardown — no keychain `viewer_base` write and no
// server restart (that lifecycle is P4.1b). `server_port` is supplied by the
// caller (the running bundled server's port); wiring it from `ServerProcessState`
// is deferred with the rest of the lifecycle.
#[tauri::command]
pub fn start_tunnel_cmd(
    server_port: u16,
    state: tauri::State<'_, TunnelState>,
) -> Result<TunnelStatus, String> {
    start_tunnel(&state, server_port)
}

#[tauri::command]
pub fn stop_tunnel_cmd(state: tauri::State<'_, TunnelState>) -> Result<TunnelStatus, String> {
    stop_tunnel(&state)
}

#[tauri::command]
pub fn tunnel_status_cmd(state: tauri::State<'_, TunnelState>) -> Result<TunnelStatus, String> {
    tunnel_status(&state)
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
}
// === ANCHOR: TUNNEL_TESTS_END ===
// === ANCHOR: TUNNEL_END ===
