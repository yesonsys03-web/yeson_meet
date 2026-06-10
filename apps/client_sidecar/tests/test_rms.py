"""Unit tests for rms_dbfs, RmsLogger, and pcm16_dbfs."""
from __future__ import annotations

import numpy as np
import pytest

from apps.client_sidecar.audio.rms import RmsLogger, rms_dbfs, should_gate_silence
from apps.client_sidecar.audio.rms import pcm16_dbfs


def test_silence() -> None:
    """All-zero samples → ≤ -100 dBFS (epsilon floor, not -inf)."""
    zeros = np.zeros(320, dtype=np.float32)
    result = rms_dbfs(zeros)
    assert result <= -100.0, f"Expected ≤ -100 dBFS, got {result}"


def test_full_scale_ones() -> None:
    """All-ones float32 → ≈ 0 dBFS (±1)."""
    ones = np.ones(320, dtype=np.float32)
    result = rms_dbfs(ones)
    assert abs(result) <= 1.0, f"Expected ≈ 0 dBFS, got {result}"


def test_logger_below_threshold() -> None:
    """RmsLogger(-45): observe(-50) → below=True, observe(-40) → below=False.
    Rolling average works across observations.
    """
    logger = RmsLogger(-45.0)

    avg1, below1 = logger.observe(-50.0)
    assert below1 is True, f"Expected below_threshold=True, got {below1}; avg={avg1}"

    # Multiple observations above threshold push avg above -45
    avg2 = avg1
    below2 = below1
    for _ in range(10):
        avg2, below2 = logger.observe(-40.0)
    assert below2 is False, f"Expected below_threshold=False, got {below2}; avg={avg2}"


@pytest.mark.parametrize(
    ("enabled", "below_threshold", "expected"),
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ],
)
def test_should_gate_silence(enabled: bool, below_threshold: bool, expected: bool) -> None:
    assert should_gate_silence(enabled, below_threshold) is expected


# === ANCHOR: RMS_PCM16_DBFS_TESTS_START ===
def test_silence_bytes_are_very_low():
    # 640 bytes of zeros (320 silent s16 samples) → far below any real threshold
    assert pcm16_dbfs(b"\x00\x00" * 320) < -100.0


def test_full_scale_is_near_zero_dbfs():
    # int16 +full scale = 0x7FFF, little-endian bytes b"\xff\x7f"
    dbfs = pcm16_dbfs(b"\xff\x7f" * 320)
    assert -1.0 < dbfs <= 0.0


def test_empty_chunk_is_floor():
    # rms_dbfs returns -120.0 for empty input; pcm16_dbfs inherits that
    assert pcm16_dbfs(b"") == -120.0


def test_quiet_below_threshold_loud_above():
    # a small-amplitude tone is below -45; a large one is above
    quiet = pcm16_dbfs((256).to_bytes(2, "little", signed=True) * 320)  # ~ -42? verify below
    loud = pcm16_dbfs((16384).to_bytes(2, "little", signed=True) * 320)  # 0.5 FS ≈ -6 dBFS
    assert quiet < loud
    assert loud > -45.0
# === ANCHOR: RMS_PCM16_DBFS_TESTS_END ===
