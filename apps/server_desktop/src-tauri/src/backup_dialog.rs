// === ANCHOR: BACKUP_DIALOG_START ===
//! Native folder picker for the backup panel (S2).
//!
//! A single `invoke`-able command that opens the OS folder chooser and returns
//! the selected absolute path (or `None` when the operator cancels). Kept
//! plugin-free to match `diagnostics::open_log_dir` — the panel adds a backup
//! destination from the returned path; a manual text field remains the fallback.

/// Open a native folder-picker and return the chosen directory's absolute path.
/// Returns `None` if the operator cancels. Async so the modal runs on rfd's
/// runtime without blocking Tauri's UI thread.
#[tauri::command]
pub async fn pick_backup_dir() -> Option<String> {
    rfd::AsyncFileDialog::new()
        .set_title("백업을 저장할 폴더 선택")
        .pick_folder()
        .await
        .map(|handle| handle.path().to_string_lossy().to_string())
}
// === ANCHOR: BACKUP_DIALOG_END ===
