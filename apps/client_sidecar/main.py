"""Sidecar entrypoint with mode dispatch (S2)."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from uuid import UUID

from apps.client_sidecar.config.constants import SERVER_WS_BASE, SERVER_WS_PATH


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(f"missing env var: {name}\n")
        sys.exit(2)
    return value


async def fixture_main() -> None:
    """S1 fixture mode — 1Hz PRD 부록 B fixtures over text frames."""
    from apps.client_sidecar.transport.fixture_emitter import fixture_stream
    from apps.client_sidecar.transport.server_ws import send_events

    api_key = _required_env("YESON_DEVICE_API_KEY")
    session_id = UUID(_required_env("YESON_SESSION_ID"))
    url = f"{SERVER_WS_BASE}{SERVER_WS_PATH}?key={api_key}&session={session_id}"
    print(f"sidecar fixture mode → {url}")
    await send_events(url, fixture_stream(session_id))


async def audio_main() -> None:
    """S2 audio mode — sounddevice BlackHole capture → 16kHz mono PCM s16le WS push."""
    from apps.client_sidecar.audio.capture import capture_chunks
    from apps.client_sidecar.audio.device import find_input_device
    from apps.client_sidecar.config.audio import DEVICE_INDEX, DEVICE_NAME_REGEX
    from apps.client_sidecar.transport.audio_ws import stream_audio

    api_key = _required_env("YESON_DEVICE_API_KEY")
    session_id = UUID(_required_env("YESON_SESSION_ID"))

    device = find_input_device(DEVICE_NAME_REGEX, DEVICE_INDEX)
    url = f"{SERVER_WS_BASE}{SERVER_WS_PATH}?key={api_key}&session={session_id}"
    print(f"sidecar audio mode → device={device['name']!r} url={url}")

    chunks = capture_chunks(device)
    await stream_audio(url, chunks)


async def main() -> None:
    mode = os.environ.get("YESON_SIDECAR_MODE", "audio").lower()
    if mode == "fixture":
        await fixture_main()
    elif mode == "audio":
        await audio_main()
    else:
        sys.stderr.write(f"unknown YESON_SIDECAR_MODE: {mode!r} (must be 'fixture' or 'audio')\n")
        sys.exit(2)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())


if __name__ == "__main__":
    run()
