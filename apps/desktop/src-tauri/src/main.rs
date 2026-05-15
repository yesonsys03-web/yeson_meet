// === ANCHOR: MAIN_START ===
// Prevents additional console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    yeson_meet_lib::run();
}
// === ANCHOR: MAIN_END ===
