"""RMS dBFS helpers. S2 logs only; S3 will gate Gemini cost."""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Deque

import numpy as np


def rms_dbfs(samples_float32: np.ndarray) -> float:
    """Return RMS power in dBFS. -inf safe via small epsilon."""
    if samples_float32.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples_float32, dtype=np.float64))))
    return 20.0 * math.log10(rms + 1e-12)


class RmsLogger:
    """1-second moving average to avoid log spam (≈50 chunks @ 20ms)."""

    def __init__(self, threshold_dbfs: float) -> None:
        self.threshold = threshold_dbfs
        self._values: Deque[tuple[float, float]] = deque(maxlen=200)

    def observe(self, dbfs: float) -> tuple[float, bool]:
        """Append a sample, return (1s_avg, below_threshold)."""
        now = time.monotonic()
        self._values.append((now, dbfs))
        cutoff = now - 1.0
        recent = [v for (t, v) in self._values if t >= cutoff]
        avg = sum(recent) / len(recent) if recent else dbfs
        return avg, avg < self.threshold
