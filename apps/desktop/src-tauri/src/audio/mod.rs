// === ANCHOR: AUDIO_MOD_START ===
//! Windows-only audio integration for yeson-meet desktop.
//!
//! Houses Voicemeeter Banana auto-routing, Windows default-device
//! transition, and on-disk recovery for crash-safe restore. Every
//! submodule is gated to Windows by the parent `#[cfg]` in `lib.rs`,
//! so this tree never compiles into Mac/Linux builds.

pub mod voicemeeter_ffi;
// === ANCHOR: AUDIO_MOD_END ===
