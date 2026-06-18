// === ANCHOR: SERVER_PROCESS_START ===
//! Lifecycle for the packaged `yeson-server` sidecar (the frozen FastAPI/uvicorn
//! server). This is a NET-NEW struct that REUSES the proven process-group spawn +
//! SIGTERM→grace→SIGKILL teardown PATTERN from the client app
//! (`apps/desktop/src-tauri/src/sidecar.rs`) — it is intentionally not a verbatim
//! copy: the env injected, the binary located, and the teardown grace rationale
//! all differ for a server (SQLite WAL checkpoint) rather than the client's audio
//! capture sidecar (ScreenCaptureKit).
use serde::{Deserialize, Serialize};
use std::{
    io::{BufRead, BufReader, Read},
    net::TcpListener,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{Emitter, Manager};

/// Default port the packaged server binds when the operator hasn't picked one.
/// Matches `apps/server/main.py run()` PORT default.
const DEFAULT_PORT: u16 = 8000;

#[derive(Default)]
pub struct ServerProcessState {
    inner: Mutex<Option<RunningServer>>,
}

/// A live server child plus the metadata the status UI needs (bound port, PID,
/// when it started so uptime can be derived in the frontend).
struct RunningServer {
    child: Child,
    port: u16,
    started_at: Instant,
}

impl Drop for ServerProcessState {
    fn drop(&mut self) {
        if let Ok(mut slot) = self.inner.lock() {
            if let Some(mut running) = slot.take() {
                terminate_group(&mut running.child);
            }
        }
    }
}

/// Terminate the server and its whole process group. The server is spawned as a
/// process-group leader (`process_group(0)`), so signalling the negative pgid
/// reaps the frozen launcher AND the uvicorn worker(s) it forks — a direct
/// `child.kill()` would SIGKILL only the launcher and orphan uvicorn, which would
/// keep the port bound and the SQLite file locked.
fn terminate_group(child: &mut Child) {
    #[cfg(unix)]
    {
        let pgid = child.id() as i32; // == pid: spawned via process_group(0)
        // SIGTERM the group → uvicorn runs its graceful shutdown, FastAPI's
        // lifespan teardown completes, and SQLAlchemy/aiosqlite closes the
        // connection so SQLite can checkpoint the WAL back into the main db file.
        let _ = Command::new("/bin/kill")
            .arg("-TERM")
            .arg(format!("-{pgid}"))
            .status();
        let mut exited = false;
        // ~3s grace: give SQLite time to checkpoint the WAL on a clean close so
        // the next launch opens a consistent db with no dirty `-wal`/`-shm` left
        // behind. (NOT the client's ScreenCaptureKit-release reason — this window
        // exists purely for the durable DB shutdown the audio sidecar never had.)
        for _ in 0..30 {
            if matches!(child.try_wait(), Ok(Some(_))) {
                exited = true;
                break;
            }
            thread::sleep(Duration::from_millis(100));
        }
        // Backstop: SIGKILL the group if anything is still alive after ~3s.
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

/// Stop the server process group if running. Safe to call on app exit.
pub fn shutdown(state: &ServerProcessState) {
    if let Ok(mut slot) = state.inner.lock() {
        if let Some(mut running) = slot.take() {
            terminate_group(&mut running.child);
        }
    }
}

/// Spawn the server as a new process-group leader so the whole subtree
/// (frozen launcher -> uvicorn) can be signalled together at shutdown.
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

/// Suppress the console window Windows would otherwise pop for the
/// console-subsystem frozen server exe. No-op off Windows; piped stdio is
/// unaffected. (Same rationale as the client sidecar.)
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

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerStartRequest {
    /// Port to bind. Defaults to 8000 when omitted/zero.
    port: Option<u16>,
    /// AI provider override; defaults to the server's gemini_live.
    provider: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerStatus {
    running: bool,
    pid: Option<u32>,
    port: Option<u16>,
    /// Seconds since the server was started, for the uptime display.
    uptime_secs: Option<u64>,
    detail: String,
}

#[derive(Clone, Debug, Serialize)]
struct BackendLogEvent {
    level: &'static str,
    source: &'static str,
    message: String,
}

#[tauri::command]
pub fn start_server(
    app: tauri::AppHandle,
    request: ServerStartRequest,
    state: tauri::State<'_, ServerProcessState>,
) -> Result<ServerStatus, String> {
    let mut slot = state
        .inner
        .lock()
        .map_err(|_| "server state lock failed".to_string())?;

    if let Some(running) = slot.as_mut() {
        if running
            .child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_none()
        {
            return Ok(running_status(running, "server is already running"));
        }
    }
    *slot = None;

    let port = match request.port {
        Some(p) if p != 0 => p,
        _ => DEFAULT_PORT,
    };
    // Port-conflict probe (AC3.4): if the chosen port is already bound, refuse to
    // spawn and surface a clear error so the operator can pick another. We bind a
    // throwaway listener and drop it immediately — a TOCTOU race is acceptable
    // here because uvicorn would still fail loudly if the port was taken in the
    // gap, and this gives a fast, friendly message in the common case.
    if !is_port_free(port) {
        return Err(format!(
            "port {port} is already in use — choose another port"
        ));
    }

    let provider = request
        .provider
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("gemini_live")
        .to_string();

    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("failed to resolve app data dir: {error}"))?;
    std::fs::create_dir_all(&app_data_dir)
        .map_err(|error| format!("failed to create app data dir: {error}"))?;
    let db_path = app_data_dir.join("yeson-meet.db");
    let storage_root = app_data_dir.join("storage");
    std::fs::create_dir_all(&storage_root)
        .map_err(|error| format!("failed to create storage dir: {error}"))?;
    let database_url = format!("sqlite+aiosqlite:///{}", db_path.display());

    let server_bin = locate_bundled_server()
        .ok_or_else(|| "bundled yeson-server binary not found".to_string())?;
    emit_backend_log(
        &app,
        "info",
        "server",
        format!("starting yeson-server: {}", server_bin.display()),
    );

    let mut command = Command::new(&server_bin);
    command
        .env("DATABASE_URL", &database_url)
        .env("STORAGE_ROOT", &storage_root)
        .env("PORT", port.to_string())
        .env("HOST", "0.0.0.0")
        .env("YESON_AI_PROVIDER", &provider)
        // Subtitle-latency tuning — matches the proven deploy `.env`. The bundled
        // server reads these from env; without them it uses code defaults
        // (120s segment cap) which makes subtitles arrive in large ~20-30s
        // bursts. Injected here so the console matches the docker deploy's
        // smooth, frequent subtitles (10s segments + fast partial translation).
        .env("GEMINI_SEGMENT_MAX_SPEECH_MS", "10000")
        .env("GEMINI_SEGMENT_HARD_MAX_SPEECH_MS", "12000")
        .env("GEMINI_VAD_SILENCE_DURATION_MS", "100")
        .env("GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED", "1")
        .env("GEMINI_PARTIAL_TRANSLATION_TIMEOUT_MS", "3000")
        .env("GEMINI_PARTIAL_MIN_CHARS", "12")
        .env("GEMINI_PARTIAL_MIN_WORDS", "2")
        .env("GEMINI_PARTIAL_MIN_DELTA_CHARS", "6")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    inject_secrets(&mut command)?;
    set_process_group(&mut command);
    set_no_window(&mut command);

    let mut child = command
        .spawn()
        .map_err(|error| format!("failed to start yeson-server: {error}"))?;

    let pid = child.id();
    spawn_output_forwarder(&app, "server:stdout", "info", child.stdout.take());
    spawn_output_forwarder(&app, "server:stderr", "warn", child.stderr.take());

    let running = RunningServer {
        child,
        port,
        started_at: Instant::now(),
    };
    let status = running_status(&running, "server started");
    *slot = Some(running);
    emit_backend_log(
        &app,
        "info",
        "server",
        format!("yeson-server pid={pid} bound port {port} (db: {})", db_path.display()),
    );
    Ok(status)
}

/// Inject the operator secrets + config from the OS keychain at spawn — same
/// "secrets injected at process spawn, never written to disk" pattern as the
/// client sidecar. Secrets live ONLY in the keychain (AC4.3); we hand them to
/// the child as env and nothing is ever written to plaintext on disk.
///
/// Slice 4: the keychain store (`server_config`) is the source of truth. We read
/// the generated-once `JWT_SECRET` (the server REQUIRES it,
/// `apps/server/auth/jwt.py:20`), the `GEMINI_API_KEY` (empty → Gemini disabled,
/// AC4.1), the Google_* STT/Translate vars, and `VIEWER_BASE`, and inject only
/// the non-empty ones. `YESON_AI_PROVIDER` is intentionally injected from the
/// start request (the per-run operator choice), not here. `load_ensured` mints +
/// persists the JWT_SECRET on first read, so it is always present here; a
/// keychain failure propagates to the operator UI rather than booting a server
/// with an unusable auth secret.
fn inject_secrets(command: &mut Command) -> Result<(), String> {
    let config = crate::server_config::load_ensured()?;
    // JWT_SECRET is guaranteed non-empty by load_ensured (generated-once).
    command.env("JWT_SECRET", &config.jwt_secret); // vibelign: allow-secret
    let pairs = [
        ("GEMINI_API_KEY", config.gemini_api_key.trim()),
        (
            "GOOGLE_APPLICATION_CREDENTIALS_JSON",
            config.google_application_credentials_json.trim(),
        ),
        ("GOOGLE_CLOUD_PROJECT", config.google_cloud_project.trim()),
        (
            "GOOGLE_STT_LANGUAGE_CODE",
            config.google_stt_language_code.trim(),
        ),
        (
            "GOOGLE_TRANSLATE_TARGET_LANGUAGE",
            config.google_translate_target_language.trim(),
        ),
        ("VIEWER_BASE", config.viewer_base.trim()),
        // YESON_AI_PROVIDER is set from the start request earlier (it is the
        // per-run operator choice the GUI selector forwards), so it is not
        // injected here to avoid the keychain silently overriding that choice.
    ];
    for (key, value) in pairs {
        if !value.is_empty() {
            command.env(key, value); // vibelign: allow-secret
        }
    }
    Ok(())
}

/// True when `port` can be bound on loopback right now (i.e. it is free).
fn is_port_free(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

/// Locate the staged onedir `yeson-server-<triple>/yeson-server` entry binary
/// that ships with the app. Tauri's `externalBin` only handles single files, so
/// the onedir tree is shipped as a bundle RESOURCE (see tauri.conf.json
/// `bundle.resources`) and unpacked next to the app; the candidates below cover
/// the bundled resource layout AND the dev `binaries/` staging dir so
/// `cargo test`/`tauri dev` resolve the same path. Returns None when missing.
fn locate_bundled_server() -> Option<PathBuf> {
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
    let dir_name = format!("yeson-server-{triple}");
    let bin_name = format!("yeson-server{suffix}");

    let mut roots: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            // bundled-resource layout (next to the app exe) + a `binaries/` subdir
            roots.push(dir.to_path_buf());
            roots.push(dir.join("binaries"));
            // macOS .app: Contents/MacOS/<exe> → resources live in Contents/Resources
            if let Some(contents) = dir.parent() {
                roots.push(contents.join("Resources"));
                roots.push(contents.join("Resources").join("binaries"));
            }
        }
    }
    // dev/test: the staged tree under src-tauri/binaries (CARGO_MANIFEST_DIR)
    roots.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries"));

    roots
        .into_iter()
        .map(|root| root.join(&dir_name).join(&bin_name))
        .find(|path| path.is_file())
}

#[tauri::command]
pub fn stop_server(state: tauri::State<'_, ServerProcessState>) -> Result<ServerStatus, String> {
    let mut slot = state
        .inner
        .lock()
        .map_err(|_| "server state lock failed".to_string())?;

    let Some(mut running) = slot.take() else {
        return Ok(stopped_status("server is not running"));
    };
    terminate_group(&mut running.child);
    Ok(stopped_status("server stopped"))
}

#[tauri::command]
pub fn server_status(state: tauri::State<'_, ServerProcessState>) -> Result<ServerStatus, String> {
    let mut slot = state
        .inner
        .lock()
        .map_err(|_| "server state lock failed".to_string())?;

    let Some(running) = slot.as_mut() else {
        return Ok(stopped_status("server is not running"));
    };

    if running
        .child
        .try_wait()
        .map_err(|error| error.to_string())?
        .is_some()
    {
        *slot = None;
        return Ok(stopped_status("server exited"));
    }
    Ok(running_status(running, "server is running"))
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BootstrapAdminRequest {
    email: String,
    password: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BootstrapAdminResult {
    /// True when this call created the first operator; false when an operator
    /// already existed (the bundle's bootstrap mode is a no-op then).
    created: bool,
    detail: String,
}

/// Securely create the FIRST operator account entirely locally (AC1.6): run the
/// staged `yeson-server` ONCE in a one-shot bootstrap mode that calls
/// `create_schema()` + `bootstrap_admin(email, password)` and EXITS without
/// starting uvicorn. The credentials are passed via env (never written to disk)
/// and there is NO exposed network endpoint, so admin creation can never leak
/// over the Slice 5 tunnel. The known `seed.py` defaults stay unusable because
/// `bootstrap_admin` no-ops once any operator row exists.
#[tauri::command]
pub fn bootstrap_admin(
    app: tauri::AppHandle,
    request: BootstrapAdminRequest,
) -> Result<BootstrapAdminResult, String> {
    let email = request.email.trim().to_string();
    let password = request.password; // vibelign: allow-secret — operator-supplied value, not a literal
    if email.is_empty() {
        return Err("email is required to create an operator account".to_string());
    }
    if password.trim().is_empty() {
        return Err("password is required to create an operator account".to_string());
    }

    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("failed to resolve app data dir: {error}"))?;
    std::fs::create_dir_all(&app_data_dir)
        .map_err(|error| format!("failed to create app data dir: {error}"))?;
    let db_path = app_data_dir.join("yeson-meet.db");
    let storage_root = app_data_dir.join("storage");
    let database_url = format!("sqlite+aiosqlite:///{}", db_path.display());

    let server_bin = locate_bundled_server()
        .ok_or_else(|| "bundled yeson-server binary not found".to_string())?;
    emit_backend_log(
        &app,
        "info",
        "server",
        format!("creating operator account for {email} (one-shot bootstrap)"),
    );

    let mut command = Command::new(&server_bin);
    command
        .env("DATABASE_URL", &database_url)
        .env("STORAGE_ROOT", &storage_root)
        .env("YESON_BOOTSTRAP_ADMIN", "1")
        .env("BOOTSTRAP_ADMIN_EMAIL", &email)
        .env("BOOTSTRAP_ADMIN_PASSWORD", &password) // vibelign: allow-secret
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    set_no_window(&mut command);

    let output = command
        .output()
        .map_err(|error| format!("failed to run bootstrap: {error}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    if !output.status.success() {
        let detail = stderr.trim().lines().last().unwrap_or("bootstrap failed");
        emit_backend_log(&app, "error", "server", format!("bootstrap failed: {detail}"));
        return Err(format!("operator account creation failed: {detail}"));
    }
    // The bundle prints a marker line so we can tell "created" from "already
    // existed" without echoing any secret.
    let created = stdout.contains("BOOTSTRAP_ADMIN_CREATED=1");
    let detail = if created {
        "operator account created".to_string()
    } else {
        "an operator account already exists".to_string()
    };
    emit_backend_log(&app, "info", "server", detail.clone());
    Ok(BootstrapAdminResult { created, detail })
}

fn running_status(running: &RunningServer, detail: impl Into<String>) -> ServerStatus {
    ServerStatus {
        running: true,
        pid: Some(running.child.id()),
        port: Some(running.port),
        uptime_secs: Some(running.started_at.elapsed().as_secs()),
        detail: detail.into(),
    }
}

fn stopped_status(detail: impl Into<String>) -> ServerStatus {
    ServerStatus {
        running: false,
        pid: None,
        port: None,
        uptime_secs: None,
        detail: detail.into(),
    }
}

fn spawn_output_forwarder<R>(
    app: &tauri::AppHandle,
    source: &'static str,
    level: &'static str,
    pipe: Option<R>,
) where
    R: Read + Send + 'static,
{
    let Some(pipe) = pipe else {
        return;
    };
    let app = app.clone();
    thread::spawn(move || {
        // Read raw bytes, not `lines()`: uvicorn/grpc can emit non-UTF-8 bytes,
        // and `lines()` would error AND break the forwarder, silently dropping
        // every later log line. `read_until` + `from_utf8_lossy` tolerates any
        // encoding; a `\n` boundary never splits a UTF-8 multibyte char.
        let mut reader = BufReader::new(pipe);
        let mut buf = Vec::new();
        loop {
            buf.clear();
            match reader.read_until(b'\n', &mut buf) {
                Ok(0) => break, // EOF
                Ok(_) => {
                    while matches!(buf.last(), Some(b'\n') | Some(b'\r')) {
                        buf.pop();
                    }
                    let message = String::from_utf8_lossy(&buf).into_owned();
                    let inferred_level = infer_log_level(&message).unwrap_or(level);
                    emit_backend_log(&app, inferred_level, source, message);
                }
                Err(error) => {
                    emit_backend_log(
                        &app,
                        "warn",
                        source,
                        format!("failed to read server output: {error}"),
                    );
                    break;
                }
            }
        }
    });
}

fn infer_log_level(message: &str) -> Option<&'static str> {
    let normalized = message.to_ascii_uppercase();
    if contains_log_token(&normalized, "ERROR") || contains_log_token(&normalized, "CRITICAL") {
        return Some("error");
    }
    if contains_log_token(&normalized, "WARNING") || contains_log_token(&normalized, "WARN") {
        return Some("warn");
    }
    if contains_log_token(&normalized, "INFO") {
        return Some("info");
    }
    if contains_log_token(&normalized, "DEBUG") {
        return Some("debug");
    }
    None
}

fn contains_log_token(message: &str, token: &str) -> bool {
    message.split_whitespace().any(|part| part == token)
}

fn emit_backend_log(
    app: &tauri::AppHandle,
    level: &'static str,
    source: &'static str,
    message: impl Into<String>,
) {
    let _ = app.emit(
        "app-log",
        BackendLogEvent {
            level,
            source,
            message: message.into(),
        },
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    // AC3.4: the port-conflict probe must report an OCCUPIED port as not free
    // and a FREE port as free — this is the gate `start_server` uses to refuse a
    // spawn on a taken port and to surface a friendly error to the operator.
    #[test]
    fn port_probe_detects_occupied_and_free() {
        // Bind a port to occupy it.
        let occupied = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let busy_port = occupied.local_addr().unwrap().port();
        assert!(!is_port_free(busy_port), "occupied port must read as not free");

        // Pick a free port, then release it so the probe sees it free.
        let free_port = {
            let probe = TcpListener::bind(("127.0.0.1", 0)).unwrap();
            probe.local_addr().unwrap().port()
        };
        assert!(is_port_free(free_port), "released port must read as free");
    }
}
// === ANCHOR: SERVER_PROCESS_END ===
