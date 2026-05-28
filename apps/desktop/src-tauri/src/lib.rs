// === ANCHOR: LIB_START ===
#[cfg(target_os = "windows")]
pub mod audio;
mod diagnostics;
mod sidecar;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(sidecar::SidecarState::default())
        .invoke_handler(tauri::generate_handler![
            diagnostics::save_app_log,
            sidecar::start_sidecar,
            sidecar::stop_sidecar,
            sidecar::sidecar_status,
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
