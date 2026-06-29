// === ANCHOR: SIDECAR_START ===
use serde::{Deserialize, Serialize};
use std::{
    io::{BufRead, BufReader, Read},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::Duration,
};
use tauri::Emitter;

#[derive(Default)]
pub struct SidecarState {
    child: Mutex<Option<Child>>,
}

impl Drop for SidecarState {
    fn drop(&mut self) {
        if let Ok(mut child_slot) = self.child.lock() {
            if let Some(mut child) = child_slot.take() {
                terminate_child(&mut child);
            }
        }
    }
}

/// Terminate the sidecar and its whole process group (uv -> python -> native
/// helper). The sidecar is spawned as a process-group leader, so signalling the
/// negative pgid reaps grandchildren that a direct `child.kill()` (SIGKILL to uv
/// only) would orphan — notably the macOS audio helper, which would otherwise
/// keep capturing after the app closes.
fn terminate_child(child: &mut Child) {
    #[cfg(unix)]
    {
        let pgid = child.id() as i32; // == pid: spawned via process_group(0)
        // SIGTERM the group → python runs its cleanup (helper.terminate) and the
        // helper's own SIGTERM handler stops the ScreenCaptureKit stream.
        let _ = Command::new("/bin/kill")
            .arg("-TERM")
            .arg(format!("-{pgid}"))
            .status();
        let mut exited = false;
        // ~3s: give the macOS helper time to await SCStream.stopCapture() so the
        // system audio tap is released cleanly. A 1s window let the SIGKILL backstop
        // cut teardown short, leaving the next capture silent on restart.
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
        // the TOP handle only. The sidecar is a PyInstaller `--onefile` tree
        // (bootloader -> python -> native audio helper), so killing just the
        // bootloader orphans python and the helper — they keep capturing audio
        // and hold the server WebSocket open, which blocks a fresh start / go
        // live. `taskkill /T` reaps the whole subtree by PID: the Windows analog
        // of the Unix `kill -TERM -pgid` above. CREATE_NO_WINDOW keeps it from
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

/// Stop the sidecar process group if running. Safe to call on app exit.
pub fn shutdown(state: &SidecarState) {
    if let Ok(mut child_slot) = state.child.lock() {
        if let Some(mut child) = child_slot.take() {
            terminate_child(&mut child);
        }
    }
}

/// Spawn the sidecar as a new process-group leader so the whole subtree
/// (uv -> python -> native helper) can be signalled together at shutdown.
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

/// Suppress the console window Windows would otherwise pop for a
/// console-subsystem child (the PyInstaller sidecar exe). Without
/// CREATE_NO_WINDOW a cmd window flashes on every meeting start when the GUI
/// app spawns the sidecar. No-op off Windows; does not affect the piped stdio.
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

/// Open the macOS Screen Recording privacy pane so the user can grant the
/// permission the native audio helper needs. Invoked from the capture-failure
/// banner. Best-effort: falls back to opening System Settings if the deep-link
/// pane is unavailable.
#[tauri::command]
pub fn open_screen_recording_settings() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")
            .spawn()
            .map_err(|error| format!("failed to open settings: {error}"))?;
        Ok(())
    }
    #[cfg(not(target_os = "macos"))]
    {
        Err("opening Screen Recording settings is only supported on macOS".to_string())
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SidecarStartRequest {
    server_ws_base: String,
    device_api_key: String,
    session_id: String,
    project_dir: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct SidecarStatus {
    running: bool,
    pid: Option<u32>,
    detail: String,
}

#[derive(Clone, Debug, Serialize)]
struct BackendLogEvent {
    level: &'static str,
    source: &'static str,
    message: String,
}

#[derive(Clone, Debug, Serialize)]
struct CaptureLevelEvent {
    dbfs: f32,
}

#[tauri::command]
pub fn start_sidecar(
    app: tauri::AppHandle,
    request: SidecarStartRequest,
    state: tauri::State<'_, SidecarState>,
) -> Result<SidecarStatus, String> {
    validate_request(&request)?;

    let mut child_slot = state
        .child
        .lock()
        .map_err(|_| "sidecar state lock failed".to_string())?;

    if let Some(child) = child_slot.as_mut() {
        if child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_none()
        {
            return Ok(status(true, Some(child.id()), "sidecar is already running"));
        }
    }
    *child_slot = None;

    let device_api_key = crate::credentials::resolve_device_key(&request.device_api_key)?; // vibelign: allow-secret

    let (mut child, detail) = match locate_bundled_sidecar() {
        Some(sidecar_exe) => {
            emit_backend_log(
                &app,
                "info",
                "sidecar",
                format!(
                    "starting bundled sidecar: {}",
                    sidecar_exe.display()
                ),
            );
            let mut command = Command::new(&sidecar_exe);
            command
                .env("SERVER_WS_BASE", request.server_ws_base.trim())
                .env("YESON_DEVICE_API_KEY", device_api_key.trim())
                .env("YESON_SESSION_ID", request.session_id.trim())
                .env("YESON_SIDECAR_MODE", "audio")
                .env("YESON_RMS_DBFS_THRESHOLD", "-60")
                .env("YESON_RMS_SILENCE_GATE_ENABLED", "0")
                .env("PYTHONIOENCODING", "utf-8")
                .env("PYTHONUTF8", "1")
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            add_native_helper_env(&mut command, &app);
            set_process_group(&mut command);
            set_no_window(&mut command);
            let child = command
                .spawn()
                .map_err(|error| format!("failed to start bundled sidecar: {error}"))?;
            let detail = format!("sidecar started (bundled: {})", sidecar_exe.display());
            (child, detail)
        }
        None => {
            let project_dir = resolve_project_dir(request.project_dir.as_deref())?;
            emit_backend_log(
                &app,
                "info",
                "sidecar",
                "starting dev sidecar via uv+python",
            );
            let mut command = Command::new("uv");
            command
                .args(["run", "python", "-m", "apps.client_sidecar.main"])
                .current_dir(&project_dir)
                .env("SERVER_WS_BASE", request.server_ws_base.trim())
                .env("YESON_DEVICE_API_KEY", device_api_key.trim())
                .env("YESON_SESSION_ID", request.session_id.trim())
                .env("YESON_SIDECAR_MODE", "audio")
                .env("YESON_RMS_DBFS_THRESHOLD", "-60")
                .env("YESON_RMS_SILENCE_GATE_ENABLED", "0")
                .env("PYTHONIOENCODING", "utf-8")
                .env("PYTHONUTF8", "1")
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            add_native_helper_env(&mut command, &app);
            set_process_group(&mut command);
            set_no_window(&mut command);
            let child = command
                .spawn()
                .map_err(|error| format!("failed to start sidecar with uv: {error}"))?;
            let detail = format!("sidecar started in {}", project_dir.display());
            (child, detail)
        }
    };

    let pid = child.id();
    spawn_output_forwarder(&app, "sidecar:stdout", "info", child.stdout.take());
    spawn_output_forwarder(&app, "sidecar:stderr", "warn", child.stderr.take());
    *child_slot = Some(child);

    Ok(status(true, Some(pid), detail))
}

/// Locate the native audio helper that Tauri's externalBin packages next
/// to the main exe (macOS: `Contents/MacOS/yeson-mac-audio-helper`, Windows:
/// `yeson-win-audio-helper.exe` alongside the exe, dev: `target/debug/...`).
/// Returns None when no bundled helper is present — caller then leaves
/// YESON_NATIVE_HELPER_BIN unset and Python's config default kicks in.
pub(crate) fn locate_bundled_native_helper() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;

    let (basename, target_triple) = if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        ("yeson-mac-audio-helper", "aarch64-apple-darwin")
    } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
        ("yeson-mac-audio-helper", "x86_64-apple-darwin")
    } else if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        ("yeson-win-audio-helper", "x86_64-pc-windows-msvc")
    } else {
        return None;
    };
    let suffix = if cfg!(target_os = "windows") { ".exe" } else { "" };
    let plain = format!("{basename}{suffix}");
    let with_triple = format!("{basename}-{target_triple}{suffix}");

    let candidates = [
        dir.join(&plain),
        dir.join(&with_triple),
        dir.join("binaries").join(&plain),
        dir.join("binaries").join(&with_triple),
    ];
    candidates.into_iter().find(|path| path.is_file())
}

/// If a bundled native helper exists, pin the sidecar to `native` provider
/// and point it at that binary. When no helper is bundled (dev CLI run,
/// or Windows pre-Phase-2), we leave the env unset and let Python's
/// `config/audio.py` defaults surface a clear missing-helper error rather
/// than silently overriding with a path that doesn't exist.
fn add_native_helper_env(command: &mut Command, app: &tauri::AppHandle) {
    if let Some(helper) = locate_bundled_native_helper() {
        emit_backend_log(
            app,
            "info",
            "sidecar",
            format!("native audio helper located: {}", helper.display()),
        );
        command
            .env("YESON_NATIVE_HELPER_BIN", &helper)
            .env("YESON_AUDIO_PROVIDER", "native");
    }
}

/// Locate the PyInstaller-built sidecar binary that Tauri's externalBin
/// bundle ships alongside the main app executable. Returns None when the
/// binary is missing — caller falls back to dev mode (uv + python).
pub(crate) fn locate_bundled_sidecar() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;

    let target_triple: &str = if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
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
    let with_triple = format!("yeson-sidecar-{target_triple}{suffix}");
    let without_triple = format!("yeson-sidecar{suffix}");

    let candidates = [
        dir.join(&with_triple),
        dir.join(&without_triple),
        dir.join("binaries").join(&with_triple),
        dir.join("binaries").join(&without_triple),
    ];
    candidates.into_iter().find(|path| path.is_file())
}

#[tauri::command]
pub fn stop_sidecar(state: tauri::State<'_, SidecarState>) -> Result<SidecarStatus, String> {
    let mut child_slot = state
        .child
        .lock()
        .map_err(|_| "sidecar state lock failed".to_string())?;

    let Some(mut child) = child_slot.take() else {
        return Ok(status(false, None, "sidecar is not running"));
    };

    terminate_child(&mut child);

    Ok(status(false, None, "sidecar stopped"))
}

#[tauri::command]
pub fn sidecar_status(state: tauri::State<'_, SidecarState>) -> Result<SidecarStatus, String> {
    let mut child_slot = state
        .child
        .lock()
        .map_err(|_| "sidecar state lock failed".to_string())?;

    let Some(child) = child_slot.as_mut() else {
        return Ok(status(false, None, "sidecar is not running"));
    };

    if child
        .try_wait()
        .map_err(|error| error.to_string())?
        .is_some()
    {
        *child_slot = None;
        return Ok(status(false, None, "sidecar exited"));
    }

    Ok(status(true, Some(child.id()), "sidecar is running"))
}

fn validate_request(request: &SidecarStartRequest) -> Result<(), String> {
    require_value("SERVER_WS_BASE", &request.server_ws_base)?;
    require_value("YESON_SESSION_ID", &request.session_id)?;
    Ok(())
}

fn require_value(label: &str, value: &str) -> Result<(), String> {
    if value.trim().is_empty() || value.contains('<') {
        Err(format!("{label} is required before starting the sidecar"))
    } else {
        Ok(())
    }
}

fn resolve_project_dir(project_dir: Option<&str>) -> Result<PathBuf, String> {
    if let Some(project_dir) = project_dir.map(str::trim).filter(|value| !value.is_empty()) {
        let path = PathBuf::from(project_dir);
        if is_repo_root(&path) {
            return Ok(path);
        }
        return Err(format!(
            "project_dir is not yeson_meet root: {}",
            path.display()
        ));
    }

    let cwd = std::env::current_dir().map_err(|error| error.to_string())?;
    for candidate in [
        cwd.clone(),
        cwd.join("../.."),
        cwd.join("../../.."),
        cwd.join("../../../.."),
    ] {
        let normalized = candidate.canonicalize().unwrap_or(candidate);
        if is_repo_root(&normalized) {
            return Ok(normalized);
        }
    }

    Err("could not find yeson_meet root containing apps/client_sidecar/main.py".to_string())
}

fn is_repo_root(path: &Path) -> bool {
    path.join("apps/client_sidecar/main.py").is_file() && path.join("pyproject.toml").is_file()
}

fn status(running: bool, pid: Option<u32>, detail: impl Into<String>) -> SidecarStatus {
    SidecarStatus {
        running,
        pid,
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
        // Read raw bytes, not `lines()`: the pipe aggregates output from the
        // whole sidecar subtree (uv -> python -> native helper) and on Windows
        // some component can emit codepage bytes (e.g. cp949 Korean device
        // names) that aren't valid UTF-8. `lines()` would error AND break the
        // forwarder, silently dropping every later log line. `read_until` +
        // `from_utf8_lossy` tolerates any encoding; a `\n` boundary never splits
        // a UTF-8 multibyte char, so lossy decode only replaces truly bad bytes.
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
                    // Capture-level telemetry (~1/s) goes to a dedicated event,
                    // NOT the app-log, so the diagnostic log stays readable.
                    if let Some(rest) = message.strip_prefix("CAPTURE_LEVEL ") {
                        if let Ok(dbfs) = rest.trim().parse::<f32>() {
                            let _ = app.emit("capture-level", CaptureLevelEvent { dbfs });
                            continue;
                        }
                    }
                    let inferred_level = infer_sidecar_log_level(&message).unwrap_or(level);
                    emit_backend_log(&app, inferred_level, source, message);
                }
                Err(error) => {
                    emit_backend_log(
                        &app,
                        "warn",
                        source,
                        format!("failed to read sidecar output: {error}"),
                    );
                    break;
                }
            }
        }
    });
}

fn infer_sidecar_log_level(message: &str) -> Option<&'static str> {
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
// === ANCHOR: SIDECAR_END ===
