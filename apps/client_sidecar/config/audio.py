"""Audio capture configuration (Slice 2 lock)."""
from __future__ import annotations

import os

TARGET_SAMPLE_RATE: int = 16000
TARGET_CHANNELS: int = 1
CHUNK_MS: int = 20
CHUNK_SAMPLES: int = 320  # 16000 * 0.02
CHUNK_BYTES: int = 640    # 320 samples * 2 bytes (int16)

# Device selection (regex matched against sounddevice.query_devices()[i]['name'])
DEVICE_NAME_REGEX: str = os.environ.get("YESON_AUDIO_DEVICE_NAME", r"(?i)blackhole")
DEVICE_INDEX: int | None = (
    int(os.environ["YESON_AUDIO_DEVICE_INDEX"])
    if os.environ.get("YESON_AUDIO_DEVICE_INDEX")
    else None
)

# RMS dBFS threshold (logged only — not enforced in S2; S3 will gate Gemini)
RMS_DBFS_THRESHOLD: float = float(os.environ.get("YESON_RMS_DBFS_THRESHOLD", "-45"))
