"""Unit tests for Resampler (samplerate conversion)."""
from __future__ import annotations

import numpy as np
import pytest

from apps.client_sidecar.audio.resample import Resampler


def test_resample_silence_48_to_16() -> None:
    """480 zeros at 48kHz → output shape <= 200 on first call (libsamplerate warmup).
    Second call should produce ~160 samples (ratio stable).
    """
    rs = Resampler(48000)
    silence = np.zeros(480, dtype=np.float32)

    out1 = rs.process(silence)
    assert out1.shape[0] <= 200, f"First call shape {out1.shape[0]} > 200"

    out2 = rs.process(silence)
    assert abs(out2.shape[0] - 160) <= 5, f"Second call shape {out2.shape[0]} not ~160"


def test_resample_sine_preserves_energy() -> None:
    """1s 1000Hz sine at 48kHz → RMS after resample should match input RMS ±0.05."""
    duration_s = 1.0
    src_rate = 48000
    t = np.linspace(0, duration_s, int(src_rate * duration_s), endpoint=False, dtype=np.float32)
    sine = (0.5 * np.sin(2.0 * np.pi * 1000.0 * t)).astype(np.float32)

    rms_in = float(np.sqrt(np.mean(sine ** 2)))

    rs = Resampler(src_rate)
    out = rs.process(sine)

    assert out.size > 0, "Resampler returned empty array"
    rms_out = float(np.sqrt(np.mean(out ** 2)))

    assert abs(rms_out - rms_in) <= 0.05, (
        f"RMS mismatch: in={rms_in:.4f} out={rms_out:.4f} diff={abs(rms_out - rms_in):.4f}"
    )


def test_resample_passthrough_same_rate() -> None:
    """src_rate == dst_rate (16000) → array returned as-is (same size, same values)."""
    rs = Resampler(16000)
    data = np.random.uniform(-1.0, 1.0, 320).astype(np.float32)
    out = rs.process(data)

    assert out.shape == data.shape, f"Shape mismatch: {out.shape} != {data.shape}"
    np.testing.assert_array_equal(out, data)
