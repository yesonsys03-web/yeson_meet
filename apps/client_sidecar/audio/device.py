"""BlackHole input device discovery."""
from __future__ import annotations

import logging
import re
from typing import Any

import sounddevice as sd

logger = logging.getLogger(__name__)


def find_input_device(name_regex: str, index_override: int | None = None) -> dict[str, Any]:
    """Locate a sounddevice input device matching ``name_regex``.

    If ``index_override`` is given, returns ``query_devices(index_override)`` directly
    (after asserting it has input channels). Raises RuntimeError with a setup hint if
    nothing matches — sidecar callers should let this propagate to exit 2 with a
    user-friendly message (see docs/SETUP_MEETING_PC.md).
    """
    if index_override is not None:
        dev = sd.query_devices(index_override)
        if dev["max_input_channels"] <= 0:
            raise RuntimeError(
                f"Device index {index_override} ({dev['name']!r}) has no input channels"
            )
        logger.info("audio device (index override): [%d] %s", index_override, dev["name"])
        return dev

    pattern = re.compile(name_regex)
    matches: list[tuple[int, dict[str, Any]]] = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and pattern.search(dev["name"]):
            matches.append((idx, dev))

    if not matches:
        raise RuntimeError(
            "BlackHole input not found. "
            "See docs/SETUP_MEETING_PC.md for setup, or set YESON_AUDIO_DEVICE_NAME / "
            "YESON_AUDIO_DEVICE_INDEX env."
        )
    if len(matches) > 1:
        logger.warning(
            "multiple input devices matched %r: %s — using first",
            name_regex, [(i, d["name"]) for i, d in matches],
        )
    idx, dev = matches[0]
    dev["_yeson_index"] = idx  # carry index for InputStream
    logger.info("audio device (regex match): [%d] %s native_rate=%s ch=%d",
                idx, dev["name"], dev["default_samplerate"], dev["max_input_channels"])
    return dev
