// === ANCHOR: LIB_START ===
mod backup_dialog;
mod diagnostics;
#[cfg(windows)]
mod job;
mod orphan_reaper;
mod report_dialog;
mod restore;
mod server_config;
mod server_process;
mod tunnel;
mod tunnel_proxy;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        // Reap leftover cloudflared / yeson-server processes from a prior app
        // instance BEFORE the webview/commands are usable. The RunEvent::Exit
        // handler below misses the dev Ctrl+C path (tauri:dev killed at the
        // terminal), which orphans those children; the next launch would then
        // hit "port 8000 already in use" / "Go Live" tunnel timeouts. This is
        // additive — it only handles that missed path. Best-effort: it logs to
        // stderr (the dev console where this pain shows up) and never fails
        // launch. Single-instance assumption; see orphan_reaper for scope.
        .setup(|_app| {
            orphan_reaper::reap_orphans(|line| eprintln!("[orphan-reaper] {line}"));
            Ok(())
        })
        .manage(server_process::ServerProcessState::default())
        .manage(tunnel::TunnelState::default())
        .invoke_handler(tauri::generate_handler![
            diagnostics::save_app_log,
            diagnostics::open_log_dir,
            backup_dialog::pick_backup_dir,
            report_dialog::save_report_bytes,
            server_process::start_server,
            server_process::stop_server,
            server_process::server_status,
            server_process::bootstrap_admin,
            server_config::save_server_config,
            server_config::server_config_meta,
            server_config::clear_server_config,
            tunnel::start_tunnel_cmd,
            tunnel::stop_tunnel_cmd,
            tunnel::tunnel_status_cmd,
            tunnel::live_session_count_cmd,
            tunnel::lan_viewer_base_cmd,
            server_process::detect_lan_ip,
            restore::inspect_backup,
            restore::restore_backup,
            restore::list_dir,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    // Reap the server process group on exit. Without this, closing the window can
    // std::process::exit() past ServerProcessState::drop, orphaning the frozen
    // server + its uvicorn worker (which would keep the port bound and the SQLite
    // file locked).
    app.run(|app_handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit = event {
            // Reap the cloudflared group BEFORE the server so the public edge is
            // torn down first (no orphan cloudflared keeping the tunnel alive).
            tunnel::shutdown(&app_handle.state::<tunnel::TunnelState>());
            server_process::shutdown(&app_handle.state::<server_process::ServerProcessState>());
        }
    });
}
// === ANCHOR: LIB_END ===
