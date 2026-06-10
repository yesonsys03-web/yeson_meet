# === ANCHOR: RMS_START ===
"""RMS dBFS helpers. S2 logs only; S3 will gate Gemini cost."""
from __future__ import annotations

import math
import time
from collections import deque

import numpy as np


# === ANCHOR: RMS_RMS_DBFS_START ===
def rms_dbfs(samples_float32: np.ndarray) -> float:
    """Return RMS power in dBFS. -inf safe via small epsilon."""
    if samples_float32.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples_float32, dtype=np.float64))))
    return 20.0 * math.log10(rms + 1e-12)
# === ANCHOR: RMS_RMS_DBFS_END ===


# === ANCHOR: RMS_PCM16_DBFS_START ===
def pcm16_dbfs(chunk: bytes) -> float:
    """RMS dBFS of a 16-bit little-endian mono PCM chunk (what the sidecar sends).

    Converts to normalized float32 then defers to ``rms_dbfs``. Empty/odd input
    is treated as silence-floor by ``rms_dbfs`` (size 0 → -120.0)."""
    samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
    return rms_dbfs(samples)
# === ANCHOR: RMS_PCM16_DBFS_END ===


# === ANCHOR: RMS_RMSLOGGER_START ===
class RmsLogger:
    """1-second moving average to avoid log spam (≈50 chunks @ 20ms)."""

    # === ANCHOR: RMS___INIT___START ===
    def __init__(self, threshold_dbfs: float) -> None:
        self.threshold: float = threshold_dbfs
        self._values: deque[tuple[float, float]] = deque(maxlen=200)
    # === ANCHOR: RMS___INIT___END ===

    # === ANCHOR: RMS_OBSERVE_START ===
    def observe(self, dbfs: float) -> tuple[float, bool]:
        """Append a sample, return (1s_avg, below_threshold)."""
        now = time.monotonic()
        self._values.append((now, dbfs))
        cutoff = now - 1.0
# === ANCHOR: RMS_RMSLOGGER_END ===
        recent = [v for (t, v) in self._values if t >= cutoff]
        avg = sum(recent) / len(recent) if recent else dbfs
        return avg, avg < self.threshold
    # === ANCHOR: RMS_OBSERVE_END ===


# === ANCHOR: RMS_SHOULD_GATE_SILENCE_START ===
def should_gate_silence(enabled: bool, below_threshold: bool) -> bool:
    """Return True when an audio chunk should be withheld from Gemini."""
    return enabled and below_threshold
# === ANCHOR: RMS_SHOULD_GATE_SILENCE_END ===
# === ANCHOR: RMS_END ===
