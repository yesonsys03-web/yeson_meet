// === ANCHOR: SIDECAR_START ===
use serde::{Deserialize, Serialize};
use std::{
    io::{BufRead, BufReader, Read},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
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
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SidecarStartRequest {
    server_ws_base: String,
    device_api_key: String,
    session_id: String,
    audio_device_name: String,
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
            let child = Command::new(&sidecar_exe)
                .env("SERVER_WS_BASE", request.server_ws_base.trim())
                .env("YESON_DEVICE_API_KEY", request.device_api_key.trim())
                .env("YESON_SESSION_ID", request.session_id.trim())
                .env("YESON_SIDECAR_MODE", "audio")
                .env("YESON_AUDIO_DEVICE_NAME", request.audio_device_name.trim())
                .env("YESON_RMS_DBFS_THRESHOLD", "-60")
                .env("YESON_RMS_SILENCE_GATE_ENABLED", "0")
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
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
            let child = Command::new("uv")
                .args(["run", "python", "-m", "apps.client_sidecar.main"])
                .current_dir(&project_dir)
                .env("SERVER_WS_BASE", request.server_ws_base.trim())
                .env("YESON_DEVICE_API_KEY", request.device_api_key.trim())
                .env("YESON_SESSION_ID", request.session_id.trim())
                .env("YESON_SIDECAR_MODE", "audio")
                .env("YESON_AUDIO_DEVICE_NAME", request.audio_device_name.trim())
                .env("YESON_RMS_DBFS_THRESHOLD", "-60")
                .env("YESON_RMS_SILENCE_GATE_ENABLED", "0")
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
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

/// Locate the PyInstaller-built sidecar binary that Tauri's externalBin
/// bundle ships alongside the main app executable. Returns None when the
/// binary is missing — caller falls back to dev mode (uv + python).
fn locate_bundled_sidecar() -> Option<PathBuf> {
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

    if child
        .try_wait()
        .map_err(|error| error.to_string())?
        .is_none()
    {
        child
            .kill()
            .map_err(|error| format!("failed to stop sidecar: {error}"))?;
        let _ = child.wait();
    }

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
    require_value("YESON_DEVICE_API_KEY", &request.device_api_key)?;
    require_value("YESON_SESSION_ID", &request.session_id)?;
    require_value("YESON_AUDIO_DEVICE_NAME", &request.audio_device_name)?;
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
        let reader = BufReader::new(pipe);
        for line in reader.lines() {
            match line {
                Ok(message) => {
                    let inferred_level = infer_sidecar_log_level(&message).unwrap_or(level);
                    emit_backend_log(&app, inferred_level, source, message)
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
