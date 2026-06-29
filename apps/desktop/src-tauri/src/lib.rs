// === ANCHOR: LIB_START ===
mod credentials;
mod diagnostics;
mod discovery;
mod orphan_reaper;
mod sidecar;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(sidecar::SidecarState::default())
        .setup(|_app| {
            orphan_reaper::reap_orphans(|line| eprintln!("[orphan-reaper] {line}"));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            diagnostics::save_app_log,
            sidecar::start_sidecar,
            sidecar::stop_sidecar,
            sidecar::sidecar_status,
            sidecar::open_screen_recording_settings,
            credentials::save_credentials,
            credentials::clear_credentials,
            credentials::credentials_meta,
            credentials::load_operator_login,
            credentials::update_server_ws_base,
            discovery::discover_server,
            discovery::device_label,
            discovery::scan_subnet,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    // Reap the sidecar process group on exit. Without this, closing the window
    // can std::process::exit() past SidecarState::drop, orphaning the sidecar
    // and the native audio helper (which would keep capturing).
    app.run(|app_handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit = event {
            sidecar::shutdown(&app_handle.state::<sidecar::SidecarState>());
        }
    });
}
// === ANCHOR: LIB_END ===
