"""Sample-rate conversion using libsamplerate (sinc_fastest)."""
from __future__ import annotations

import numpy as np
import samplerate

from apps.client_sidecar.config.audio import TARGET_SAMPLE_RATE


class Resampler:
    """Convert mono float32 from src_rate to TARGET_SAMPLE_RATE."""

    def __init__(self, src_rate: int, dst_rate: int = TARGET_SAMPLE_RATE) -> None:
        self.src_rate = int(src_rate)
        self.dst_rate = int(dst_rate)
        self._ratio = self.dst_rate / self.src_rate
        # State carried across calls to avoid edge artifacts. libsamplerate
        # supports streaming via Resampler obj; simple stateless call is fine
        # for 20ms chunks because we accumulate AFTER resample.
        self._engine = samplerate.Resampler("sinc_fastest", channels=1)

    def process(self, buf_float32_mono: np.ndarray) -> np.ndarray:
        if self.src_rate == self.dst_rate:
            return buf_float32_mono.astype(np.float32, copy=False)
        out = self._engine.process(buf_float32_mono, self._ratio, end_of_input=False)
        return out.astype(np.float32, copy=False)
