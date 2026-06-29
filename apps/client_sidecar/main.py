# === ANCHOR: MAIN_START ===
"""Sidecar entrypoint with mode dispatch (S2)."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import threading
import time
from uuid import UUID

from apps.client_sidecar.config.constants import SERVER_WS_BASE, SERVER_WS_PATH

logger = logging.getLogger(__name__)


# === ANCHOR: MAIN__REQUIRED_ENV_START ===
def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(f"missing env var: {name}\n")
        sys.exit(2)
    return value
# === ANCHOR: MAIN__REQUIRED_ENV_END ===


# === ANCHOR: MAIN_FIXTURE_MAIN_START ===
async def fixture_main() -> None:
    """S1 fixture mode — 1Hz PRD 부록 B fixtures over text frames."""
    from apps.client_sidecar.transport.fixture_emitter import fixture_stream
    from apps.client_sidecar.transport.server_ws import send_events

    api_key = _required_env("YESON_DEVICE_API_KEY")
    session_id = UUID(_required_env("YESON_SESSION_ID"))
    url = f"{SERVER_WS_BASE}{SERVER_WS_PATH}?key={api_key}&session={session_id}"
    print(f"sidecar fixture mode → {url.split('?')[0]}?key=<redacted>")
    await send_events(url, fixture_stream(session_id))
# === ANCHOR: MAIN_FIXTURE_MAIN_END ===


# === ANCHOR: MAIN_AUDIO_MAIN_START ===
async def audio_main() -> None:
    """S2 audio mode — provider factory selects source, then stream to server WS."""
    from apps.client_sidecar.audio.sources.factory import make_source
    from apps.client_sidecar.audio.sources.native_pipe_source import NativeCaptureError
    from apps.client_sidecar.transport.audio_ws import stream_audio
    from apps.client_sidecar.config.audio import RMS_DBFS_THRESHOLD
    from apps.client_sidecar.transport.capture_status import (
        CaptureStatusReporter,
        run_watchdog,
    )

    api_key = _required_env("YESON_DEVICE_API_KEY")
    session_id = UUID(_required_env("YESON_SESSION_ID"))

    source = make_source()
    url = f"{SERVER_WS_BASE}{SERVER_WS_PATH}?key={api_key}&session={session_id}"
    print(f"sidecar audio mode → source={type(source).__name__} url={url.split('?')[0]}?key=<redacted>")

    # Live capture-status heartbeat: the watchdog prints CAPTURE_STATUS <state>
    # on each transition (forwarded to the desktop app log → status chip).
    reporter = CaptureStatusReporter(rms_threshold_dbfs=RMS_DBFS_THRESHOLD)
    watchdog = asyncio.create_task(
        run_watchdog(reporter, lambda line: print(line, flush=True))
    )
    try:
        await stream_audio(url, source.chunks(), reporter)
    except NativeCaptureError as exc:
        # Native-only target: no silent death. Emit a recognizable status line
        # (forwarded to the desktop app log) so the cause is visible/actionable.
        print(f"NATIVE_STATUS {exc.reason}", flush=True)
        logger.error("native capture failed: reason=%s", exc.reason)
        raise
    finally:
        watchdog.cancel()
        try:
            await watchdog
        except asyncio.CancelledError:
            pass
        await source.close()
# === ANCHOR: MAIN_AUDIO_MAIN_END ===


# === ANCHOR: MAIN_MAIN_START ===
async def main() -> None:
    mode = os.environ.get("YESON_SIDECAR_MODE", "audio").lower()
    if mode == "fixture":
        run_coro = fixture_main()
    elif mode == "audio":
        run_coro = audio_main()
    else:
        sys.stderr.write(f"unknown YESON_SIDECAR_MODE: {mode!r} (must be 'fixture' or 'audio')\n")
        sys.exit(2)

    # Graceful shutdown: on SIGTERM/SIGINT, cancel the running task so its
    # finally-block (source.close → native helper terminate) runs. A default
    # SIGTERM would kill the process without cleanup, orphaning the helper.
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass  # unsupported (e.g. Windows / non-main thread)

    work = asyncio.ensure_future(run_coro)
    waiter = asyncio.ensure_future(stop.wait())
    done, _ = await asyncio.wait({work, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if work in done:
        waiter.cancel()
        await work  # surface result / exception
        return
    # signal received → cancel work so its finally-block runs cleanup
    work.cancel()
    try:
        await work
    except asyncio.CancelledError:
        pass
# === ANCHOR: MAIN_MAIN_END ===


# === ANCHOR: MAIN__INSTALL_PARENT_DEATH_WATCHDOG_START ===
def _install_parent_death_watchdog() -> None:
    """Self-exit if the desktop app that spawned us dies abruptly.

    On a clean close the app reaps us (Rust RunEvent → process-group kill), but a
    Ctrl+C / SIGKILL / crash skips that and orphans this sidecar — it keeps the
    server WebSocket open, so the meeting stays "live" and the operator can't go
    public. ``getppid()`` is unreliable here (the PyInstaller onefile bootloader,
    not the app, is our parent), so the app passes its pid in ``YESON_PARENT_PID``
    and we poll its liveness. Once it's gone we SIGTERM ourselves so ``main()``'s
    handler runs the normal cleanup (source.close → native helper terminate → WS
    close). No-op when the env var is absent/invalid (standalone dev runs).

    POSIX only: the liveness probe is ``os.kill(pid, 0)`` (sends no signal). On
    Windows ``os.kill`` would TerminateProcess the parent, so we skip it there and
    rely on the app's ``taskkill /T`` teardown + the startup orphan reaper.
    """
    if sys.platform == "win32":
        return
    raw = os.environ.get("YESON_PARENT_PID")
    if not raw:
        return
    try:
        parent_pid = int(raw)
    except ValueError:
        return
    if parent_pid <= 1:
        return

    def _watch() -> None:
        while True:
            time.sleep(2.0)
            try:
                os.kill(parent_pid, 0)  # signal 0 = liveness probe, sends nothing
            except ProcessLookupError:
                pass  # parent gone → self-terminate below
            except PermissionError:
                continue  # alive but not signalable by us → keep watching
            else:
                continue  # still alive
            print(
                "PARENT_EXIT detected (app pid gone) -- shutting down sidecar",
                file=sys.stderr,
                flush=True,
            )
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:  # noqa: BLE001
                os._exit(0)
            time.sleep(5.0)
            os._exit(0)  # backstop if graceful shutdown stalls

    threading.Thread(target=_watch, name="parent-death-watchdog", daemon=True).start()
# === ANCHOR: MAIN__INSTALL_PARENT_DEATH_WATCHDOG_END ===


# === ANCHOR: MAIN__INSTALL_OS_TRUST_STORE_START ===
def _install_os_trust_store() -> None:
    """Make stdlib ssl (used by websockets) trust the OS certificate store.

    The meeting server runs behind Caddy ``tls internal`` (private CA). Instead
    of shipping/pinning that CA, defer to the OS trust store — the same source
    the desktop webview uses — so a root CA registered once on the meeting PC
    (ROADMAP: "회의실 PC Root CA 신뢰 등록") is honored by the sidecar too.
    Identical on macOS (Keychain) and Windows (cert store).
    """
    import truststore

    truststore.inject_into_ssl()
# === ANCHOR: MAIN__INSTALL_OS_TRUST_STORE_END ===


# === ANCHOR: MAIN_RUN_START ===
def run() -> None:
    # Force UTF-8 stdio in-process: more reliable than PYTHONUTF8 in frozen
    # (PyInstaller) builds. Korean device names logged on Windows would otherwise
    # be encoded as cp949 — invalid UTF-8 to the desktop log reader.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _install_parent_death_watchdog()
    _install_os_trust_store()
    asyncio.run(main())
# === ANCHOR: MAIN_RUN_END ===


if __name__ == "__main__":
    run()
# === ANCHOR: MAIN_END ===
