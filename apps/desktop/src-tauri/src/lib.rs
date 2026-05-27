// === ANCHOR: LIB_START ===
#[cfg(target_os = "windows")]
pub mod audio;
mod diagnostics;
mod sidecar;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(sidecar::SidecarState::default())
        .invoke_handler(tauri::generate_handler![
            diagnostics::save_app_log,
            sidecar::start_sidecar,
            sidecar::stop_sidecar,
            sidecar::sidecar_status,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
// === ANCHOR: LIB_END ===
