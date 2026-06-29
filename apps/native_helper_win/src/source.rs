// === ANCHOR: SOURCE_START ===
//! Swappable capture source. Yields interleaved f32 blocks at a source
//! rate/channels — exactly what `pcm::PcmConverter::push_f32` consumes, so the
//! WASAPI path (Windows) and the PCM-file path (any OS, for macOS dry-runs)
//! share the identical downstream pipeline.

use std::time::Duration;

pub trait AudioSource {
    /// (sample_rate, channels) of the interleaved blocks this source yields.
    fn format(&self) -> (u32, u16);
    /// Next interleaved-f32 block, or None when the stream ends.
    fn next_block(&mut self) -> Option<Vec<f32>>;
}

/// Reads a raw **interleaved f32 little-endian** file and replays it paced at
/// ~real time (20 ms blocks), so the server's live AI pipeline sees a realistic
/// cadence. Builds on every OS — this is the macOS dry-run source.
pub struct FileSource {
    rate: u32,
    channels: u16,
    samples: Vec<f32>, // interleaved
    pos: usize,
    block_frames: usize,
}

impl FileSource {
    pub fn open(path: &str, rate: u32, channels: u16) -> std::io::Result<Self> {
        let bytes = std::fs::read(path)?;
        if bytes.len() % 4 != 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "PCM file length not a multiple of 4 (expected f32le)",
            ));
        }
        let samples: Vec<f32> = bytes
            .chunks_exact(4)
            .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
            .collect();
        let block_frames = (rate as usize / 50).max(1); // 20 ms
        Ok(Self {
            rate,
            channels,
            samples,
            pos: 0,
            block_frames,
        })
    }
}

impl AudioSource for FileSource {
    fn format(&self) -> (u32, u16) {
        (self.rate, self.channels)
    }

    fn next_block(&mut self) -> Option<Vec<f32>> {
        if self.pos >= self.samples.len() {
            return None;
        }
        let block_samples = self.block_frames * self.channels as usize;
        let end = (self.pos + block_samples).min(self.samples.len());
        let block = self.samples[self.pos..end].to_vec();
        self.pos = end;
        // Pace to ~real time so the live pipeline isn't flooded.
        std::thread::sleep(Duration::from_millis(20));
        Some(block)
    }
}

/// Windows-only WASAPI loopback source, wrapping `capture::start()`.
#[cfg(windows)]
pub struct WasapiSource {
    _capture: crate::capture::Capture,
    rx: std::sync::mpsc::Receiver<crate::capture::RawBlock>,
    err_rx: std::sync::mpsc::Receiver<String>,
    rate: u32,
    channels: u16,
    pub device_name: String,
}

#[cfg(windows)]
impl WasapiSource {
    pub fn start() -> Result<Self, String> {
        let (capture, fmt, rx, err_rx) = crate::capture::start().map_err(|e| match e {
            crate::capture::CaptureError::NoDefaultRenderDevice => {
                "no_default_render_device".to_string()
            }
            crate::capture::CaptureError::WasapiInitFailed(d) => format!("wasapi_init_failed: {d}"),
            crate::capture::CaptureError::UnsupportedFormat(d) => format!("unsupported_format: {d}"),
        })?;
        Ok(Self {
            _capture: capture,
            rx,
            err_rx,
            rate: fmt.sample_rate,
            channels: fmt.channels,
            device_name: fmt.device_name,
        })
    }
}

#[cfg(windows)]
impl AudioSource for WasapiSource {
    fn format(&self) -> (u32, u16) {
        (self.rate, self.channels)
    }

    fn next_block(&mut self) -> Option<Vec<f32>> {
        use std::sync::mpsc::RecvTimeoutError;
        loop {
            if let Ok(detail) = self.err_rx.try_recv() {
                eprintln!("[stream] capture stream_error: {detail}");
                return None;
            }
            match self.rx.recv_timeout(Duration::from_millis(250)) {
                Ok(block) => return Some(block),
                Err(RecvTimeoutError::Timeout) => continue, // silence → keep waiting
                Err(RecvTimeoutError::Disconnected) => return None,
            }
        }
    }
}
// === ANCHOR: SOURCE_END ===
