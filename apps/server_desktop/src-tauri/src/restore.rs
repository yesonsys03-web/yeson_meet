//! Backup restore orchestration (stop → one-shot swap → start), console side.
use crate::server_process::{
    current_port, emit_backend_log, locate_bundled_server, set_no_window, start_server_inner,
    stop_server_inner, ServerProcessState, ServerStartRequest,
};
use std::process::{Command, Stdio};
use tauri::Manager;

/// Spawn the bundled server as a one-shot (no uvicorn) with extra env vars
/// injected. Returns the child's combined stdout on success, or an error string
/// derived from the last stderr line on non-zero exit. Mirrors the spawn
/// conventions in `server_process::bootstrap_admin`.
fn run_bundle_oneshot(
    app: &tauri::AppHandle,
    extra_env: &[(&str, String)],
) -> Result<String, String> {
    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app data dir: {e}"))?;
    std::fs::create_dir_all(&app_data_dir)
        .map_err(|e| format!("mkdir app data: {e}"))?;
    let db_path = app_data_dir.join("yeson-meet.db");
    let storage_root = app_data_dir.join("storage");
    let database_url = format!("sqlite+aiosqlite:///{}", db_path.display());

    let server_bin =
        locate_bundled_server().ok_or_else(|| "bundled yeson-server not found".to_string())?;

    let mut command = Command::new(&server_bin);
    command
        .env("DATABASE_URL", &database_url)
        .env("STORAGE_ROOT", &storage_root)
        .env("YESON_APP_VERSION", app.package_info().version.to_string())
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (k, v) in extra_env {
        command.env(k, v);
    }
    set_no_window(&mut command);

    let out = command
        .output()
        .map_err(|e| format!("spawn failed: {e}"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        let detail = stderr.trim().lines().last().unwrap_or("one-shot failed");
        return Err(detail.to_string());
    }
    Ok(stdout)
}

/// Find the first line that starts with `marker` and parse the rest as JSON.
fn parse_marker(stdout: &str, marker: &str) -> Result<serde_json::Value, String> {
    let line = stdout
        .lines()
        .find_map(|l| l.strip_prefix(marker))
        .ok_or_else(|| format!("missing {marker} in output"))?;
    serde_json::from_str(line.trim()).map_err(|e| format!("bad json: {e}"))
}

/// Read-only backup preview: runs the bundle with `YESON_INSPECT_BACKUP=1` and
/// returns the parsed `INSPECT_RESULT=` JSON. Does not touch the live DB or
/// stop/start the server.
#[tauri::command]
pub fn inspect_backup(
    app: tauri::AppHandle,
    snapshot_path: String,
) -> Result<serde_json::Value, String> {
    let env = [
        ("YESON_INSPECT_BACKUP", "1".to_string()),
        ("YESON_SNAPSHOT_PATH", snapshot_path),
        ("YESON_CURRENT_VERSION", app.package_info().version.to_string()),
    ];
    let stdout = run_bundle_oneshot(&app, &env)?;
    parse_marker(&stdout, "INSPECT_RESULT=")
}

/// Full restore: stop server → swap DB+storage via one-shot → restart server.
/// Returns the parsed `RESTORE_RESULT=` JSON on success. Always attempts to
/// restart the server so the operator is never left with it stopped, even when
/// the restore itself fails.
#[tauri::command]
pub fn restore_backup(
    app: tauri::AppHandle,
    state: tauri::State<'_, ServerProcessState>,
    snapshot_path: String,
    storage_zip_path: Option<String>,
) -> Result<serde_json::Value, String> {
    // Fail fast before stopping the server: if the app-data dir can't resolve we
    // must not leave the operator with a stopped, un-restarted server.
    app.path()
        .app_data_dir()
        .map_err(|e| format!("failed to resolve app data dir: {e}"))?;

    // Capture the port the server is currently bound to BEFORE stopping it so the
    // restart brings it back on the same port. `current_port` returns None when the
    // server is not running; passing None falls back to DEFAULT_PORT (8000), which
    // matches the pre-existing behaviour and is safe for the not-running case.
    let prior_port = current_port(&*state);
    emit_backend_log(&app, "info", "server", "restore: stopping server");
    let _ = stop_server_inner(&*state);

    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app data dir: {e}"))?;
    let safety_dir = app_data_dir.join("pre-restore");

    let mut env: Vec<(&str, String)> = vec![
        ("YESON_RESTORE", "1".to_string()),
        ("YESON_SNAPSHOT_PATH", snapshot_path),
        ("YESON_SAFETY_DIR", safety_dir.display().to_string()),
    ];
    if let Some(z) = storage_zip_path {
        env.push(("YESON_STORAGE_ZIP", z));
    }

    let result = run_bundle_oneshot(&app, &env)
        .and_then(|out| parse_marker(&out, "RESTORE_RESULT="));

    // Restart regardless so the operator is never left with a stopped server.
    // Use the port captured before the stop so the server returns on the same
    // address LAN clients expect. Provider is not tracked in state; None lets
    // start_server_inner default to gemini_live (same as a fresh start).
    emit_backend_log(&app, "info", "server", "restore: starting server");
    let started = start_server_inner(
        &app,
        ServerStartRequest {
            port: prior_port,
            provider: None,
        },
        &*state,
    );

    match (result, started) {
        (Ok(v), Ok(_)) => Ok(v),
        (Ok(v), Err(e)) => {
            let safety = v.get("safety_dir").and_then(|s| s.as_str()).unwrap_or("the pre-restore folder");
            Err(format!(
                "restore completed but the server failed to restart: {e}. Your previous data was backed up to {safety} — restore that backup or restart the server manually."
            ))
        }
        (Err(e), _) => Err(format!("restore failed: {e}")),
    }
}

/// Return the file *names* (not full paths) inside `path`. Used by the console
/// restore UI to enumerate backup snapshots without requiring `plugin-fs`.
#[tauri::command]
pub fn list_dir(path: String) -> Result<Vec<String>, String> {
    let dir = std::path::Path::new(&path);
    let rd = std::fs::read_dir(dir).map_err(|e| format!("read_dir {path}: {e}"))?;
    let mut names: Vec<String> = rd
        .filter_map(|entry| {
            let entry = entry.ok()?;
            // Only regular files (skip subdirs, symlinks, etc.)
            let ft = entry.file_type().ok()?;
            if ft.is_file() {
                entry.file_name().into_string().ok()
            } else {
                None
            }
        })
        .collect();
    names.sort();
    Ok(names)
}
