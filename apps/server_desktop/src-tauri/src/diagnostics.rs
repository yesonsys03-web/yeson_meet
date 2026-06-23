// === ANCHOR: DIAGNOSTICS_START ===
use serde::Serialize;
use std::{fs, time::{SystemTime, UNIX_EPOCH}};
use tauri::Manager;

#[derive(Debug, Serialize)]
pub struct SaveAppLogResult {
    path: String,
}

#[tauri::command]
pub fn save_app_log(app: tauri::AppHandle, contents: String) -> Result<SaveAppLogResult, String> {
    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("failed to resolve app data dir: {error}"))?;
    let log_dir = app_data_dir.join("logs");
    fs::create_dir_all(&log_dir)
        .map_err(|error| format!("failed to create log dir {}: {error}", log_dir.display()))?;

    let timestamp_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("system time error: {error}"))?
        .as_millis();
    let path = log_dir.join(format!("yeson-server-log-{timestamp_ms}.txt"));
    fs::write(&path, contents)
        .map_err(|error| format!("failed to write log file {}: {error}", path.display()))?;

    Ok(SaveAppLogResult {
        path: path.to_string_lossy().to_string(),
    })
}

/// Reveal the on-disk log directory (where the dated server-*.log files and
/// exported snapshots live) in the OS file manager. Best-effort: spawns the
/// platform opener and does not wait on it.
#[tauri::command]
pub fn open_log_dir(app: tauri::AppHandle) -> Result<(), String> {
    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("failed to resolve app data dir: {error}"))?;
    let log_dir = app_data_dir.join("logs");
    fs::create_dir_all(&log_dir)
        .map_err(|error| format!("failed to create log dir {}: {error}", log_dir.display()))?;

    #[cfg(target_os = "macos")]
    let mut command = std::process::Command::new("open");
    #[cfg(target_os = "windows")]
    let mut command = std::process::Command::new("explorer");
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    let mut command = std::process::Command::new("xdg-open");

    command
        .arg(&log_dir)
        .spawn()
        .map_err(|error| format!("failed to open log dir {}: {error}", log_dir.display()))?;
    Ok(())
}
// === ANCHOR: DIAGNOSTICS_END ===
