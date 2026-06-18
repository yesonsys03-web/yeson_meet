// === ANCHOR: LIB_START ===
mod diagnostics;
mod server_config;
mod server_process;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(server_process::ServerProcessState::default())
        .invoke_handler(tauri::generate_handler![
            diagnostics::save_app_log,
            server_process::start_server,
            server_process::stop_server,
            server_process::server_status,
            server_process::bootstrap_admin,
            server_config::save_server_config,
            server_config::server_config_meta,
            server_config::clear_server_config,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    // Reap the server process group on exit. Without this, closing the window can
    // std::process::exit() past ServerProcessState::drop, orphaning the frozen
    // server + its uvicorn worker (which would keep the port bound and the SQLite
    // file locked).
    app.run(|app_handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit = event {
            server_process::shutdown(&app_handle.state::<server_process::ServerProcessState>());
        }
    });
}
// === ANCHOR: LIB_END ===
