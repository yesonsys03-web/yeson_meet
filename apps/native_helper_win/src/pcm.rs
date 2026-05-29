// === ANCHOR: WIN_PCM_START ===
//! Interleaved source samples → 16 kHz mono s16le, framed to 640 bytes.
//! No cpal types (module-isolation rule, spec §4): inputs are plain slices + ints.
use rubato::{
    Resampler, SincFixedIn, SincInterpolationParameters, SincInterpolationType, WindowFunction,
};

const TARGET_RATE: usize = 16_000;
const FRAME_SAMPLES: usize = 320; // 20 ms @ 16 kHz
const FRAME_BYTES: usize = FRAME_SAMPLES * 2; // 640
const RESAMPLER_CHUNK: usize = 1024; // input frames per rubato call

pub struct PcmConverter {
    #[allow(dead_code)]
    source_rate: usize,
    channels: usize,
    resampler: Option<SincFixedIn<f32>>,
    in_mono: Vec<f32>,  // downmixed mono awaiting resample (source rate)
    out_i16le: Vec<u8>, // 16k s16le bytes awaiting framing
}

impl PcmConverter {
    pub fn new(source_rate: u32, channels: u16) -> Self {
        let source_rate = source_rate as usize;
        let channels = channels.max(1) as usize;
        let resampler = if source_rate == TARGET_RATE {
            None // passthrough, no resampling needed
        } else {
            let params = SincInterpolationParameters {
                sinc_len: 256,
                f_cutoff: 0.95,
                interpolation: SincInterpolationType::Linear,
                oversampling_factor: 256,
                window: WindowFunction::BlackmanHarris2,
            };
            Some(
                SincFixedIn::<f32>::new(
                    TARGET_RATE as f64 / source_rate as f64, // resample_ratio
                    2.0,                                     // max relative
                    params,
                    RESAMPLER_CHUNK,
                    1, // mono (after downmix)
                )
                .expect("rubato resampler init"),
            )
        };
        Self {
            source_rate,
            channels,
            resampler,
            in_mono: Vec::new(),
            out_i16le: Vec::new(),
        }
    }

    /// Feed interleaved f32 frames. Returns any complete 640-byte frames.
    pub fn push_f32(&mut self, interleaved: &[f32]) -> Vec<[u8; FRAME_BYTES]> {
        self.downmix_into_mono(interleaved);
        self.drain_to_frames()
    }

    /// Feed interleaved i16 frames (normalized to f32 internally).
    pub fn push_i16(&mut self, interleaved: &[i16]) -> Vec<[u8; FRAME_BYTES]> {
        let as_f32: Vec<f32> = interleaved.iter().map(|&s| s as f32 / 32768.0).collect();
        self.push_f32(&as_f32)
    }

    fn downmix_into_mono(&mut self, interleaved: &[f32]) {
        let ch = self.channels;
        // cpal normally delivers whole interleaved frames; a non-aligned buffer
        // would make chunks_exact silently DROP the trailing partial frame
        // (slow desync). Assert the assumption (no-op in release, spec §4).
        debug_assert!(
            interleaved.len() % ch == 0,
            "callback buffer not frame-aligned: len={} channels={}",
            interleaved.len(),
            ch
        );
        for frame in interleaved.chunks_exact(ch) {
            let sum: f32 = frame.iter().sum();
            self.in_mono.push(sum / ch as f32);
        }
    }

    fn drain_to_frames(&mut self) -> Vec<[u8; FRAME_BYTES]> {
        match self.resampler.as_mut() {
            None => {
                // Passthrough: quantize all mono samples directly.
                for &s in &self.in_mono {
                    push_i16le(&mut self.out_i16le, s);
                }
                self.in_mono.clear();
            }
            Some(rs) => {
                while self.in_mono.len() >= RESAMPLER_CHUNK {
                    let block: Vec<f32> = self.in_mono.drain(..RESAMPLER_CHUNK).collect();
                    let out = rs.process(&[block], None).expect("rubato process");
                    for &s in &out[0] {
                        push_i16le(&mut self.out_i16le, s);
                    }
                }
            }
        }
        self.take_full_frames()
    }

    fn take_full_frames(&mut self) -> Vec<[u8; FRAME_BYTES]> {
        let mut frames = Vec::new();
        while self.out_i16le.len() >= FRAME_BYTES {
            let mut frame = [0u8; FRAME_BYTES];
            frame.copy_from_slice(&self.out_i16le[..FRAME_BYTES]);
            frames.push(frame);
            self.out_i16le.drain(..FRAME_BYTES);
        }
        frames
    }
}

fn push_i16le(buf: &mut Vec<u8>, sample_f32: f32) {
    let clamped = sample_f32.clamp(-1.0, 1.0);
    let v = (clamped * 32767.0).round() as i16;
    buf.extend_from_slice(&v.to_le_bytes());
}

#[cfg(test)]
mod tests {
    use super::*;

    const FRAME_BYTES: usize = 640;

    // ~1s of 48k stereo f32 → ~16000 mono samples → ~50 frames of 640B.
    #[test]
    fn resamples_48k_stereo_to_16k_mono_frames() {
        let mut conv = PcmConverter::new(48_000, 2);
        let frames_in = 48_000; // 1 second
        let mut interleaved = Vec::with_capacity(frames_in * 2);
        for n in 0..frames_in {
            let s = (n as f32 * 0.01).sin() * 0.5;
            interleaved.push(s); // L
            interleaved.push(s); // R
        }
        let out = conv.push_f32(&interleaved);
        // Every emitted buffer is exactly one 640-byte frame.
        assert!(out.iter().all(|f| f.len() == FRAME_BYTES));
        // ~16000 samples / 320 per frame ≈ 50 frames (allow resampler edge tolerance).
        assert!((45..=52).contains(&out.len()), "got {} frames", out.len());
    }

    #[test]
    fn carries_remainder_across_calls() {
        let mut conv = PcmConverter::new(16_000, 1); // no resample, mono
        // 100 mono samples < 320 → 0 full frames, remainder buffered.
        let chunk: Vec<f32> = (0..100).map(|_| 0.1).collect();
        assert_eq!(conv.push_f32(&chunk).len(), 0);
        // Feed enough to cross 320 total → exactly one frame.
        let chunk2: Vec<f32> = (0..240).map(|_| 0.1).collect();
        let out = conv.push_f32(&chunk2);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].len(), FRAME_BYTES);
    }

    #[test]
    fn downmixes_stereo_to_mono_average() {
        let mut conv = PcmConverter::new(16_000, 2);
        // L=+0.5, R=-0.5 → mono 0.0. 320 frames → exactly one silent 640B frame.
        let mut interleaved = Vec::new();
        for _ in 0..320 {
            interleaved.push(0.5);
            interleaved.push(-0.5);
        }
        let out = conv.push_f32(&interleaved);
        assert_eq!(out.len(), 1);
        assert!(out[0].iter().all(|&b| b == 0), "expected silence");
    }

    #[test]
    fn quantizes_full_scale_little_endian() {
        let mut conv = PcmConverter::new(16_000, 1);
        let chunk: Vec<f32> = (0..320).map(|_| 1.0).collect(); // +full scale
        let out = conv.push_f32(&chunk);
        assert_eq!(out.len(), 1);
        // i16 32767 = 0x7FFF, little-endian → [0xFF, 0x7F].
        assert_eq!(&out[0][0..2], &[0xFF, 0x7F]);
    }

    #[test]
    fn i16_input_matches_f32_path() {
        let mut conv = PcmConverter::new(16_000, 1);
        let chunk: Vec<i16> = (0..320).map(|_| 8192).collect(); // 0.25 full-scale
        let out = conv.push_i16(&chunk);
        assert_eq!(out.len(), 1);
        // 8192/32768 = 0.25 → 0.25*32767 = 8191.75 → f32::round (half away from
        // zero) → 8192 = 0x2000 → LE [0x00, 0x20].
        assert_eq!(&out[0][0..2], &[0x00, 0x20]);
    }

    #[test]
    fn handles_44100_and_96000() {
        for rate in [44_100u32, 96_000u32] {
            let mut conv = PcmConverter::new(rate, 2);
            let mut interleaved = Vec::new();
            for n in 0..rate as usize {
                let s = (n as f32 * 0.01).sin() * 0.3;
                interleaved.push(s);
                interleaved.push(s);
            }
            let out = conv.push_f32(&interleaved);
            // ~1s → ~16000 samples → ~50 frames; loose bound for edge effects.
            assert!((44..=53).contains(&out.len()), "rate {rate}: {} frames", out.len());
        }
    }
}
// === ANCHOR: WIN_PCM_END ===
