// === ANCHOR: REPORT_DIALOG_START ===
//! Native "save as" dialog + byte-write for report exports (MD/HTML/DOCX/PDF).
//! Plugin-free, matching `backup_dialog::pick_backup_dir` / `diagnostics::save_app_log`
//! — a single `invoke`-able command instead of pulling `tauri-plugin-dialog` +
//! `tauri-plugin-fs` (this app's convention; see the `rfd` comment in Cargo.toml).
//! Binary bytes travel as a JSON number array (Tauri's raw-body IPC optimization
//! needs a single-argument `Vec<u8>` command; this one also takes `default_name`),
//! which is fine at report-export sizes.

/// Open a native save-file dialog defaulted to `default_name`, write `bytes` to
/// the chosen path, and return it. Returns `None` if the operator cancels.
#[tauri::command]
pub async fn save_report_bytes(default_name: String, bytes: Vec<u8>) -> Result<Option<String>, String> {
    let handle = rfd::AsyncFileDialog::new()
        .set_title("보고서 저장")
        .set_file_name(&default_name)
        .save_file()
        .await;
    let Some(handle) = handle else { return Ok(None) };
    let path = handle.path().to_path_buf();
    std::fs::write(&path, &bytes)
        .map_err(|error| format!("failed to write {}: {error}", path.display()))?;
    Ok(Some(path.to_string_lossy().to_string()))
}
// === ANCHOR: REPORT_DIALOG_END ===
