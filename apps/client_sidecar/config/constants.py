# === ANCHOR: CONSTANTS_START ===
"""Sidecar runtime constants.

Locked in PRD §10 (Slice 0 결정 락):
- Sidecar ↔ Desktop IPC: 127.0.0.1 WebSocket on this port (JSON messages)
- Sidecar deploy: uv run (dev) — Slices 0-5 only. PyInstaller + Tauri externalBin in β-5.
"""

SIDECAR_LOCAL_WS_HOST: str = "127.0.0.1"
SIDECAR_LOCAL_WS_PORT: int = 27800
"""Localhost WebSocket port used by the Tauri desktop shell to talk to this sidecar."""

SERVER_WS_PATH: str = "/ws/sidecar"
"""Path the sidecar connects to on the FastAPI server (Slice 1+)."""
# === ANCHOR: CONSTANTS_END ===

import os

SERVER_WS_BASE: str = os.environ.get("SERVER_WS_BASE", "ws://localhost:8000")
"""Server WebSocket base URL. Sidecar connects to {SERVER_WS_BASE}{SERVER_WS_PATH} for /ws/sidecar."""
