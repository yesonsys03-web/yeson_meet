//! Shared modules for the yeson-meet Windows audio tooling.
//!
//! `ipc`/`pcm` are pure (build + unit-test everywhere). `capture` is the
//! Windows-only cpal WASAPI loopback. `source`/`stream` back the all-in-one
//! `stream_dump` test tool that captures and streams to the server directly.
pub mod device_watch;
pub mod ipc;
pub mod pcm;
#[cfg(windows)]
pub mod capture;
pub mod source;
pub mod stream;
