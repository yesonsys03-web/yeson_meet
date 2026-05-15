"""Sidecar entrypoint.

Slice 1: fixture mode — 1초마다 가짜 utterance.transcribed 발화를 서버로 보냄.
환경 변수:
  YESON_DEVICE_API_KEY  — seed가 발급한 평문
  YESON_SESSION_ID      — seed가 발급한 session external UUID
  SERVER_WS_BASE        — default ws://localhost:8000
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from uuid import UUID

from apps.client_sidecar.config.constants import SERVER_WS_BASE, SERVER_WS_PATH
from apps.client_sidecar.transport.fixture_emitter import fixture_stream
from apps.client_sidecar.transport.server_ws import send_events


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(f"missing env var: {name}\n")
        sys.exit(2)
    return value


async def main() -> None:
    api_key = _required_env("YESON_DEVICE_API_KEY")
    session_id = UUID(_required_env("YESON_SESSION_ID"))
    url = f"{SERVER_WS_BASE}{SERVER_WS_PATH}?key={api_key}&session={session_id}"
    print(f"sidecar fixture mode → {url}")
    await send_events(url, fixture_stream(session_id))


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())


if __name__ == "__main__":
    run()
