// === ANCHOR: SERVER_PROCESS_START ===
//! Lifecycle for the packaged `yeson-server` sidecar (the frozen FastAPI/uvicorn
//! server). This is a NET-NEW struct that REUSES the proven process-group spawn +
//! SIGTERM→grace→SIGKILL teardown PATTERN from the client app
//! (`apps/desktop/src-tauri/src/sidecar.rs`) — it is intentionally not a verbatim
//! copy: the env injected, the binary located, and the teardown grace rationale
//! all differ for a server (SQLite WAL checkpoint) rather than the client's audio
//! capture sidecar (ScreenCaptureKit).
use mdns_sd::{ServiceDaemon, ServiceInfo};
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::{
    io::{BufRead, BufReader, Read, Write as _},
    net::TcpListener,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{Mutex, OnceLock},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
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
    mdns: Option<ServiceDaemon>,
    /// Windows-only: Job Object with KILL_ON_JOB_CLOSE so the server tree is
    /// reaped automatically when this app process dies by any means. Kept here
    /// so its lifetime equals the running server; Drop closes the handle → OS
    /// terminates every process in the job (bootloader → uvicorn worker).
    #[cfg(windows)]
    _job: Option<crate::job::KillOnCloseJob>,
}

impl Drop for ServerProcessState {
    fn drop(&mut self) {
        if let Ok(mut slot) = self.inner.lock() {
            if let Some(mut running) = slot.take() {
                if let Some(mdns) = running.mdns.take() {
                    let _ = mdns.shutdown();
                }
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
        // Windows has no process groups: `child.kill()` is TerminateProcess on
        // the TOP handle only. The frozen server is a PyInstaller tree
        // (bootloader -> python -> uvicorn worker), so killing just the
        // bootloader orphans uvicorn — which keeps port 8000 bound and the
        // SQLite file locked, so the next start "binds then immediately stops".
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

/// Stop the server process group if running. Safe to call on app exit.
pub fn shutdown(state: &ServerProcessState) {
    if let Ok(mut slot) = state.inner.lock() {
        if let Some(mut running) = slot.take() {
            if let Some(mdns) = running.mdns.take() {
                let _ = mdns.shutdown();
            }
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
pub(crate) fn set_no_window(command: &mut Command) {
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
    pub(crate) port: Option<u16>,
    /// AI provider override; defaults to the server's gemini_live.
    pub(crate) provider: Option<String>,
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
    start_server_inner(&app, request, &state)
}

/// The actual start logic behind the `start_server` Tauri command. Taking
/// `&ServerProcessState` instead of `tauri::State` keeps it callable from an
/// in-process caller that already holds the managed state, reusing the proven
/// spawn path (secrets injection, port probe, process-group spawn). The tunnel
/// "Go Live" flow no longer restarts the server — it publishes the new
/// `VIEWER_BASE` via `{STORAGE_ROOT}/viewer_base.txt`, which the running server
/// reads fresh per session — so this is now driven only by the command.
pub fn start_server_inner(
    app: &tauri::AppHandle,
    request: ServerStartRequest,
    state: &ServerProcessState,
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

    // Provider precedence: explicit per-run request override → the keychain
    // config (what the Config panel's provider dropdown saves) → gemini_live.
    // The console UI always sends provider: null, so without the keychain
    // fallback the dropdown selection was silently ignored at start.
    let provider = request
        .provider
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| {
            crate::server_config::load_ensured()
                .ok()
                .map(|config| config.yeson_ai_provider.trim().to_string())
                .filter(|value| !value.is_empty())
        })
        .unwrap_or_else(|| "gemini_live".to_string());

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
    let log_dir = app_data_dir.join("logs");
    let _ = std::fs::create_dir_all(&log_dir);
    prune_old_logs(&log_dir, std::time::Duration::from_secs(7 * 86_400));
    let database_url = format!("sqlite+aiosqlite:///{}", db_path.display());

    let server_bin = locate_bundled_server()
        .ok_or_else(|| "bundled yeson-server binary not found".to_string())?;
    emit_backend_log(
        app,
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
        // AUDIO output is the only modality that connects on the current SDK/model
        // (TEXT fails with 1011) and it enables input-audio transcription, the
        // subtitle source. Code already defaults to AUDIO; pin it so a future SDK
        // default change can't silently break subtitles.
        .env("GEMINI_RESPONSE_MODALITY", "AUDIO")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // Dev only: a debug Tauri shell (pnpm tauri:dev) tells the bundled server to
    // trust the vite dev-server CORS origins (apps/server main.py). A packaged
    // release build never sets this, so prod never trusts a dev origin (security
    // review finding #1). The frozen server binary is identical either way; only
    // this spawn-time env differs.
    if cfg!(debug_assertions) {
        command.env("YESON_DEV", "1");
    }
    inject_secrets(&mut command)?;
    // Task 14: point the video-caption-studio ffmpeg calls (domain/video_captions
    // /ffmpeg.py::locate_ffmpeg reads YESON_FFMPEG_BIN before falling back to
    // PATH) at the bundled binary so a plain user install (no ffmpeg on PATH)
    // still works.
    if let Some(ffmpeg) = locate_bundled_ffmpeg() {
        command.env("YESON_FFMPEG_BIN", ffmpeg);
    }
    augment_path_for_summary_cli(&mut command);
    set_process_group(&mut command);
    set_no_window(&mut command);

    let mut child = command
        .spawn()
        .map_err(|error| format!("failed to start yeson-server: {error}"))?;

    // Windows: confine the child tree to a Job Object with KILL_ON_JOB_CLOSE so
    // the OS kills the entire server subtree (bootloader → uvicorn worker) when
    // this app dies by any means, including Task Manager kill or a crash that
    // bypasses the RunEvent teardown. Best-effort: if the Job API fails we still
    // run with the taskkill / RunEvent safety nets.
    #[cfg(windows)]
    let _job = crate::job::confine(&child);

    let pid = child.id();
    spawn_output_forwarder(app, "server:stdout", "info", child.stdout.take());
    spawn_output_forwarder(app, "server:stderr", "warn", child.stderr.take());

    let mdns = advertise_mdns(port);
    let running = RunningServer {
        child,
        port,
        started_at: Instant::now(),
        mdns,
        #[cfg(windows)]
        _job,
    };
    let status = running_status(&running, "server started");
    *slot = Some(running);
    emit_backend_log(
        app,
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
        // Report-summary backend/model selection (Config panel). Empty backend →
        // not injected → the server's generate_summary defaults to "auto" PATH
        // detection (claude → codex).
        ("YESON_SUMMARY_BACKEND", config.summary_backend.trim()),
        ("YESON_SUMMARY_MODEL", config.summary_model.trim()),
        // YESON_AI_PROVIDER is resolved at spawn time earlier (explicit start
        // request override, else the keychain dropdown value), so it is not
        // injected here where it could shadow that resolution.
    ];
    for (key, value) in pairs {
        if !value.is_empty() {
            command.env(key, value); // vibelign: allow-secret
        }
    }
    Ok(())
}

/// User home directory from the environment (no extra crate). `USERPROFILE` on
/// Windows, `HOME` elsewhere.
fn home_dir() -> Option<PathBuf> {
    #[cfg(windows)]
    let raw = std::env::var_os("USERPROFILE");
    #[cfg(unix)]
    let raw = std::env::var_os("HOME");
    raw.map(PathBuf::from)
}

/// Curated, per-OS directories where global CLIs (claude/codex) commonly land,
/// so summary-backend detection works even when the GUI-inherited PATH omits
/// them. Existence is checked by the caller.
fn common_bin_dirs() -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = Vec::new();
    let home = home_dir();
    #[cfg(windows)]
    {
        if let Some(appdata) = std::env::var_os("APPDATA") {
            // npm global shims (claude.cmd, codex.cmd) install here by default.
            dirs.push(PathBuf::from(appdata).join("npm"));
        }
        if let Some(pf) = std::env::var_os("ProgramFiles") {
            dirs.push(PathBuf::from(pf).join("nodejs"));
        }
        if let Some(local) = std::env::var_os("LOCALAPPDATA") {
            // Native installers (NOT npm .cmd shims): the OpenAI Codex installer
            // drops codex.exe here; the native Claude installer uses ~/.local/bin
            // (covered below).
            dirs.push(
                PathBuf::from(&local)
                    .join("Programs")
                    .join("OpenAI")
                    .join("Codex")
                    .join("bin"),
            );
        }
        if let Some(h) = &home {
            dirs.push(h.join(".local").join("bin"));
            dirs.push(h.join(".cargo").join("bin"));
            dirs.push(h.join(".bun").join("bin"));
        }
    }
    #[cfg(unix)]
    {
        for p in ["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"] {
            dirs.push(PathBuf::from(p));
        }
        if let Some(h) = &home {
            dirs.push(h.join(".local/bin"));
            dirs.push(h.join(".npm-global/bin"));
            dirs.push(h.join(".cargo/bin"));
            dirs.push(h.join(".bun/bin"));
            dirs.push(h.join(".deno/bin"));
        }
    }
    dirs
}

/// The login+interactive shell's PATH (unix). Captures version-managed
/// toolchains (nvm/homebrew) that a GUI app's minimal PATH misses. Runs the
/// user's `$SHELL -lic 'echo "$PATH"'` with a 3s timeout; any failure yields an
/// empty list (best-effort).
#[cfg(unix)]
fn login_shell_path_dirs() -> Vec<PathBuf> {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".to_string());
    let (tx, rx) = std::sync::mpsc::channel();
    thread::spawn(move || {
        let out = Command::new(&shell)
            .args(["-lic", "echo \"$PATH\""])
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .output();
        let _ = tx.send(out);
    });
    let output = match rx.recv_timeout(Duration::from_secs(3)) {
        Ok(Ok(out)) if out.status.success() => out,
        _ => return Vec::new(),
    };
    let path = String::from_utf8_lossy(&output.stdout);
    std::env::split_paths(path.trim()).map(PathBuf::from).collect()
}

/// Augment the spawned server's PATH so `shutil.which("claude"/"codex")` (the
/// report-summary backend detection) reliably finds CLIs the user installed,
/// even though a GUI-launched app inherits a minimal PATH that often omits the
/// shell's interactive entries. Merges (de-duplicated, existing dirs only) the
/// login-shell PATH (unix) and the common per-OS install dirs onto the current
/// PATH. Best-effort: any failure leaves the inherited PATH untouched, and the
/// CLI is still invoked by bare name so a dir now on PATH executes normally.
fn augment_path_for_summary_cli(command: &mut Command) {
    use std::collections::HashSet;

    let mut entries: Vec<PathBuf> = std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).collect())
        .unwrap_or_default();
    let mut seen: HashSet<PathBuf> = entries.iter().cloned().collect();

    let mut extra: Vec<PathBuf> = Vec::new();
    #[cfg(unix)]
    extra.extend(login_shell_path_dirs());
    extra.extend(common_bin_dirs());

    for dir in extra {
        if seen.insert(dir.clone()) && dir.is_dir() {
            entries.push(dir);
        }
    }
    if let Ok(joined) = std::env::join_paths(&entries) {
        command.env("PATH", joined);
    }
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
pub(crate) fn locate_bundled_server() -> Option<PathBuf> {
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

/// Locate the staged `ffmpeg-<triple>/ffmpeg[.exe]` binary vendored by
/// `scripts/fetch-ffmpeg.sh` (Task 14). Mirrors `tunnel.rs::locate_cloudflared`
/// and `locate_bundled_server` above: same bundled-resource + dev `binaries/`
/// candidate roots, so `cargo test`/`tauri dev` and a packaged app resolve the
/// same layout. Returns None when missing — the caller then relies on
/// `locate_ffmpeg()`'s PATH fallback (apps/server/domain/video_captions/ffmpeg.py).
fn locate_bundled_ffmpeg() -> Option<PathBuf> {
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
    let dir_name = format!("ffmpeg-{triple}");
    let bin_name = format!("ffmpeg{suffix}");

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

/// Absolute path of the bundled `yeson-server` entry binary the app spawns, for
/// callers outside this module (the startup orphan reaper). Reuses
/// `locate_bundled_server` verbatim so the reaper matches the exact same path the
/// start path spawns from. `None` when the binary is absent.
pub fn bundled_server_path() -> Option<PathBuf> {
    locate_bundled_server()
}

#[tauri::command]
pub fn stop_server(state: tauri::State<'_, ServerProcessState>) -> Result<ServerStatus, String> {
    stop_server_inner(&state)
}

/// Stop logic behind the `stop_server` command. Kept as a `&ServerProcessState`
/// helper so an in-process caller could reuse it without `tauri::State`.
pub fn stop_server_inner(state: &ServerProcessState) -> Result<ServerStatus, String> {
    let mut slot = state
        .inner
        .lock()
        .map_err(|_| "server state lock failed".to_string())?;

    let Some(mut running) = slot.take() else {
        return Ok(stopped_status("server is not running"));
    };
    if let Some(mdns) = running.mdns.take() {
        let _ = mdns.shutdown();
    }
    terminate_group(&mut running.child);
    Ok(stopped_status("server stopped"))
}

/// The server's `STORAGE_ROOT` (`<app_data_dir>/storage`) — the SAME derivation
/// `start_server_inner` injects as the `STORAGE_ROOT` env, so the desktop and the
/// running server agree on the path. The tunnel "Go Live" flow writes the public
/// `viewer_base.txt` here so the server picks it up at session creation WITHOUT a
/// restart (`apps/server/api/v1/sessions.py` reads `{STORAGE_ROOT}/viewer_base.txt`
/// fresh per call, precedence file > env > default).
pub fn storage_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("failed to resolve app data dir: {error}"))?;
    Ok(app_data_dir.join("storage"))
}

/// Atomically publish `url` as the runtime viewer base by writing
/// `{STORAGE_ROOT}/viewer_base.txt`. Write to a temp file in the SAME directory
/// then rename, so a session-creation read never observes a half-written file
/// (rename is atomic within a filesystem). Replaces the old server-restart step:
/// the server reads this file fresh per session, so a new public base takes
/// effect immediately with no restart blip.
pub fn write_viewer_base_file(app: &tauri::AppHandle, url: &str) -> Result<(), String> {
    let storage_root = storage_root(app)?;
    std::fs::create_dir_all(&storage_root)
        .map_err(|error| format!("failed to create storage dir: {error}"))?;
    let target = storage_root.join("viewer_base.txt");
    let tmp = storage_root.join("viewer_base.txt.tmp");
    std::fs::write(&tmp, url.as_bytes())
        .map_err(|error| format!("failed to write viewer_base temp file: {error}"))?;
    std::fs::rename(&tmp, &target).map_err(|error| {
        let _ = std::fs::remove_file(&tmp);
        format!("failed to publish viewer_base file: {error}")
    })?;
    Ok(())
}

/// Remove `{STORAGE_ROOT}/viewer_base.txt` so the server reverts to the env/LAN
/// viewer base on the next session. Best-effort: a missing file is success (the
/// public base is already absent), which is what "stop public" wants.
pub fn remove_viewer_base_file(app: &tauri::AppHandle) -> Result<(), String> {
    let target = storage_root(app)?.join("viewer_base.txt");
    match std::fs::remove_file(&target) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("failed to remove viewer_base file: {error}")),
    }
}

/// The port the server is CURRENTLY bound to, or `None` if it is not running.
/// The tunnel's `live_session_count_cmd` uses this to probe the running server.
pub fn current_port(state: &ServerProcessState) -> Option<u16> {
    let mut slot = state.inner.lock().ok()?;
    let running = slot.as_mut()?;
    if matches!(running.child.try_wait(), Ok(Some(_))) {
        return None;
    }
    Some(running.port)
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

/// Mask Bearer tokens and `key=value` secrets before a log line is emitted to
/// the UI or written to disk. Ports apps/server_desktop/src/appLog.ts `redact`.
fn redact(text: &str) -> String {
    static BEARER: OnceLock<Regex> = OnceLock::new();
    static KV: OnceLock<Regex> = OnceLock::new();
    let bearer =
        BEARER.get_or_init(|| Regex::new(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+").unwrap());
    let kv = KV.get_or_init(|| {
        Regex::new(
            r#"(?i)((?:password|token|api[_-]?key|secret)["']?\s*[:=]\s*["']?)[^"'\s,}]+"#,
        )
        .unwrap()
    });
    let masked = bearer.replace_all(text, "$1<redacted>").into_owned();
    kv.replace_all(&masked, "$1<redacted>").into_owned()
}

/// Civil date (UTC) from a Unix epoch seconds value — Howard Hinnant's
/// days-from-civil inverse. Avoids pulling chrono just for a filename stamp.
fn ymd_from_epoch_secs(secs: i64) -> (i64, u32, u32) {
    let days = secs.div_euclid(86_400);
    let z = days + 719_468;
    let era = (if z >= 0 { z } else { z - 146_096 }) / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = (if mp < 10 { mp + 3 } else { mp - 9 }) as u32;
    (y + if m <= 2 { 1 } else { 0 }, m, d)
}

fn log_date_string(secs: i64) -> String {
    let (y, m, d) = ymd_from_epoch_secs(secs);
    format!("{y:04}-{m:02}-{d:02}")
}

fn log_timestamp_string(secs: i64) -> String {
    let (y, m, d) = ymd_from_epoch_secs(secs);
    let tod = secs.rem_euclid(86_400);
    let (hh, mm, ss) = (tod / 3_600, (tod % 3_600) / 60, tod % 60);
    format!("{y:04}-{m:02}-{d:02} {hh:02}:{mm:02}:{ss:02}")
}

/// Delete dated log files whose mtime is older than `max_age`. Best-effort.
fn prune_old_logs(log_dir: &std::path::Path, max_age: std::time::Duration) {
    let now = SystemTime::now();
    let Ok(entries) = std::fs::read_dir(log_dir) else {
        return;
    };
    for entry in entries.flatten() {
        // Only delete the rotating dated logs this function owns.
        // User-exported snapshots (`yeson-server-log-<ms>.txt`) share the same
        // directory; skipping non-matching names keeps them safe.
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if !(name.starts_with("server-") && name.ends_with(".log")) {
            continue;
        }
        let Ok(meta) = entry.metadata() else {
            continue;
        };
        let Ok(modified) = meta.modified() else {
            continue;
        };
        if now
            .duration_since(modified)
            .map(|age| age > max_age)
            .unwrap_or(false)
        {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

/// Last time (unix secs) the dated logs were pruned, so a 24/7 server self-cleans
/// without a restart. `prune_old_logs` is otherwise only called once at spawn.
static LAST_PRUNE_UNIX_SECS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Append one redacted line to <app_data_dir>/logs/server-YYYY-MM-DD.log.
/// Best-effort: any failure is swallowed so logging never blocks the forwarder.
fn append_log_file(app: &tauri::AppHandle, level: &str, source: &str, message: &str) {
    let Ok(app_data_dir) = app.path().app_data_dir() else {
        return;
    };
    let log_dir = app_data_dir.join("logs");
    if std::fs::create_dir_all(&log_dir).is_err() {
        return;
    }
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let path = log_dir.join(format!("server-{}.log", log_date_string(secs)));
    let line = format!(
        "[{}] {} source={source} message={message}\n",
        log_timestamp_string(secs),
        level.to_uppercase()
    );
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    {
        let _ = file.write_all(line.as_bytes());
    }

    // Periodic prune: at most once per 24h, so a 24/7 server that is never
    // restarted still trims aged daily logs to the 7-day window. Cheap atomic
    // guard, no extra thread; cross-platform (prune_old_logs uses std::fs).
    use std::sync::atomic::Ordering;
    let now_secs = secs.max(0) as u64;
    let last = LAST_PRUNE_UNIX_SECS.load(Ordering::Relaxed);
    if now_secs.saturating_sub(last) >= 86_400
        && LAST_PRUNE_UNIX_SECS
            .compare_exchange(last, now_secs, Ordering::Relaxed, Ordering::Relaxed)
            .is_ok()
    {
        prune_old_logs(&log_dir, std::time::Duration::from_secs(7 * 86_400));
    }
}

pub(crate) fn emit_backend_log(
    app: &tauri::AppHandle,
    level: &'static str,
    source: &'static str,
    message: impl Into<String>,
) {
    let message = redact(&message.into());
    append_log_file(app, level, source, &message);
    let _ = app.emit(
        "app-log",
        BackendLogEvent {
            level,
            source,
            message,
        },
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

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

    #[test]
    fn redact_masks_bearer_and_kv_secrets() {
        assert_eq!(
            redact("Authorization: Bearer abc.DEF-123_x"),
            "Authorization: Bearer <redacted>"
        );
        assert_eq!(redact("api_key=SUPERSECRET"), "api_key=<redacted>");
        assert_eq!(redact("password: hunter2"), "password: <redacted>");
        assert_eq!(redact("token=xyz123"), "token=<redacted>");
        assert_eq!(redact("secret=abc987"), "secret=<redacted>");
        assert_eq!(redact("nothing to hide here"), "nothing to hide here");
    }

    #[test]
    fn log_date_and_timestamp_strings() {
        assert_eq!(log_date_string(0), "1970-01-01");
        // 1_700_000_000 = 2023-11-14 22:13:20 UTC
        assert_eq!(log_date_string(1_700_000_000), "2023-11-14");
        assert_eq!(log_timestamp_string(1_700_000_000), "2023-11-14 22:13:20");
    }

    #[test]
    fn prune_old_logs_removes_only_aged_files() {
        use std::time::Duration;
        let dir = std::env::temp_dir().join(format!("yeson-prune-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();

        let fresh = dir.join("server-fresh.log");
        let old = dir.join("server-old.log");
        fs::write(&fresh, "x").unwrap();
        fs::write(&old, "x").unwrap();

        // Backdate `old` to 8 days ago.
        let eight_days_ago = SystemTime::now() - Duration::from_secs(8 * 86_400);
        let ft = filetime::FileTime::from_system_time(eight_days_ago);
        filetime::set_file_mtime(&old, ft).unwrap();

        prune_old_logs(&dir, Duration::from_secs(7 * 86_400));

        assert!(fresh.exists(), "fresh log must survive");
        assert!(!old.exists(), "8-day-old log must be pruned");
        let _ = fs::remove_dir_all(&dir);
    }
}

/// Advertise the running server on the LAN so clients can auto-discover it.
/// Best-effort: a failure here never blocks server startup.
fn advertise_mdns(port: u16) -> Option<ServiceDaemon> {
    let ip = local_ip_address::local_ip().ok()?;
    let daemon = ServiceDaemon::new().ok()?;
    let host_name = format!("{}.local.", "yeson-meet-server");
    let info = ServiceInfo::new(
        "_yeson-meet._tcp.local.",
        "yeson-meet-server",
        &host_name,
        ip,
        port,
        &[("path", "/")][..],
    )
    .ok()?;
    daemon.register(info).ok()?;
    Some(daemon)
}

#[tauri::command]
pub fn detect_lan_ip() -> Result<String, String> {
    local_ip_address::local_ip()
        .map(|ip| ip.to_string())
        .map_err(|error| format!("LAN IP 감지 실패: {error}"))
}
// === ANCHOR: SERVER_PROCESS_END ===
