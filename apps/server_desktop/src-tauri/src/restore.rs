//! Backup restore orchestration (stop → one-shot swap → start), console side.
use crate::server_process::{
    emit_backend_log, locate_bundled_server, set_no_window, start_server_inner,
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
    emit_backend_log(&app, "info", "server", "restore: starting server");
    let started = start_server_inner(
        &app,
        ServerStartRequest {
            port: None,
            provider: None,
        },
        &*state,
    );

    match (result, started) {
        (Ok(v), Ok(_)) => Ok(v),
        (Ok(_), Err(e)) => Err(format!("restore done but server restart failed: {e}")),
        (Err(e), _) => Err(format!("restore failed: {e}")),
    }
}
