// === ANCHOR: MAIN_START ===
// Prevents additional console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    yeson_server_console_lib::run();
}
// === ANCHOR: MAIN_END ===
