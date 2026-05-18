"""Unit tests for rms_dbfs and RmsLogger."""
from __future__ import annotations

import numpy as np
import pytest

from apps.client_sidecar.audio.rms import RmsLogger, rms_dbfs


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
    for _ in range(10):
        avg2, below2 = logger.observe(-40.0)
    assert below2 is False, f"Expected below_threshold=False, got {below2}; avg={avg2}"
