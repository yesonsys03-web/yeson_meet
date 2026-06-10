// === ANCHOR: WIN_CAPTURE_START ===
//! cpal WASAPI loopback (Windows-only). Callback enqueues raw frames only.
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::Stream;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{channel, sync_channel, Receiver, SyncSender, TrySendError};
use std::sync::Arc;

/// Captured source format, reported in the `started` event.
pub struct CaptureFormat {
    pub device_name: String,
    pub sample_rate: u32,
    pub channels: u16,
}

pub enum CaptureError {
    NoDefaultRenderDevice,
    WasapiInitFailed(String),
    UnsupportedFormat(String),
}

/// One raw block of interleaved f32 frames from the audio callback.
pub type RawBlock = Vec<f32>;

pub struct Capture {
    _stream: Stream, // kept alive; dropping stops capture
    pub dropped: Arc<AtomicU64>,
}

/// Start loopback. Returns the live stream handle, source format, a receiver of
/// raw f32 blocks, and a receiver for asynchronous stream errors. Bounded audio
/// channel: on overflow the callback drops the newest block and bumps `dropped`
/// (explicit drop policy, never silent). Stream errors are unbounded because they
/// must always reach main and become a `fatal` event.
pub fn start() -> Result<(Capture, CaptureFormat, Receiver<RawBlock>, Receiver<String>), CaptureError>
{
    let host = cpal::default_host();
    let device = host
        .default_output_device()
        .ok_or(CaptureError::NoDefaultRenderDevice)?;
    let device_name = device.name().unwrap_or_else(|_| "unknown".into());

    let supported = device
        .default_output_config()
        .map_err(|e| CaptureError::WasapiInitFailed(e.to_string()))?;
    let sample_rate = supported.sample_rate().0;
    let channels = supported.channels();

    if supported.sample_format() != cpal::SampleFormat::F32 {
        // Real WASAPI shared-mode mix format is F32; bail loudly otherwise so
        // pcm.rs assumptions never silently break.
        return Err(CaptureError::UnsupportedFormat(format!(
            "{:?}",
            supported.sample_format()
        )));
    }

    let (tx, rx): (SyncSender<RawBlock>, Receiver<RawBlock>) = sync_channel(128);
    let (err_tx, err_rx) = channel::<String>();
    let dropped = Arc::new(AtomicU64::new(0));
    let dropped_cb = dropped.clone();

    let stream = device
        .build_input_stream(
            &supported.into(),
            move |data: &[f32], _| match tx.try_send(data.to_vec()) {
                Ok(()) => {}
                Err(TrySendError::Full(_)) => {
                    dropped_cb.fetch_add(1, Ordering::Relaxed);
                }
                Err(TrySendError::Disconnected(_)) => {}
            },
            move |err| {
                let _ = err_tx.send(err.to_string());
            },
            None,
        )
        .map_err(|e| CaptureError::WasapiInitFailed(e.to_string()))?;
    stream
        .play()
        .map_err(|e| CaptureError::WasapiInitFailed(e.to_string()))?;

    Ok((
        Capture {
            _stream: stream,
            dropped,
        },
        CaptureFormat {
            device_name,
            sample_rate,
            channels,
        },
        rx,
        err_rx,
    ))
}

/// Freshly re-query the current default output device name (each call hits
/// WASAPI `GetDefaultAudioEndpoint` — not cached). Used by the worker's device
/// poll to detect a default-device change. None if there is no default output.
pub fn current_default_device_name() -> Option<String> {
    let host = cpal::default_host();
    host.default_output_device().and_then(|d| d.name().ok())
}

#[cfg(all(test, windows))]
mod smoke {
    use super::*;
    use std::time::Duration;

    // Requires audio playing on the default output. Manual/CI-gated.
    #[test]
    #[ignore]
    fn loopback_delivers_at_least_one_block() {
        let (_cap, fmt, rx, _err_rx) = start().expect("start loopback");
        eprintln!(
            "device={} rate={} ch={}",
            fmt.device_name, fmt.sample_rate, fmt.channels
        );
        let block = rx
            .recv_timeout(Duration::from_secs(5))
            .expect("no audio block in 5s (is audio playing?)");
        assert!(!block.is_empty());
    }
}
// === ANCHOR: WIN_CAPTURE_END ===
