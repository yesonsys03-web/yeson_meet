# Standalone Mac Sidecar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the packaged macOS `.app` run the Python sidecar as a true standalone — no repo, uv, PATH, or shipped Caddy CA dependence — by PyInstaller-bundling a lean native-only sidecar and trusting the OS certificate store for TLS.

**Architecture:** The Rust seam already exists (`sidecar.rs::locate_bundled_sidecar()` runs `yeson-sidecar-{triple}` next to the exe, else falls back to uv+python). This plan produces that binary and wires it into Tauri `externalBin`, plus makes `websockets` trust the OS cert store via `truststore`. No Rust changes.

**Tech Stack:** Python 3.12 (sidecar), PyInstaller (`--onefile`), `truststore` (OS trust store shim), Tauri v2 `externalBin`, uv workspace, pnpm.

**Design spec:** `docs/superpowers/specs/2026-05-28-standalone-mac-sidecar-design.md`

**Decisions (2026-05-28, confirmed):** CA trust = `truststore` (OS store, same source as the webview). Bundle scope = lean native-only (exclude `numpy`/`sounddevice`/`samplerate`). Build must be deterministic from a clean checkout (`uv run --project apps/client_sidecar ...`). This slice is **local/internal beta only** until codesign/notarization/updater are handled.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `apps/client_sidecar/audio/sources/factory.py` | provider→source selection | Modify: lazy-import `SoundDeviceSource` so native path drops `numpy`/`sounddevice`/`samplerate` |
| `apps/client_sidecar/tests/test_source_factory.py` | factory behavior tests | Modify: add lean-bundle guard test |
| `apps/client_sidecar/main.py` | sidecar entrypoint/bootstrap | Modify: redact startup URL secret (audio + fixture); add `_install_os_trust_store()`, call it in `run()` |
| `apps/client_sidecar/transport/server_ws.py` | server WS client | Modify: redact full URL in connect log |
| `apps/client_sidecar/tests/test_audio_main_smoke.py` | audio startup smoke | Modify: assert device key never appears in stdout |
| `apps/client_sidecar/tests/test_tls_bootstrap.py` | trust-store bootstrap test | Create |
| `apps/client_sidecar/pyproject.toml` | sidecar deps | Modify: `truststore` (runtime), `pyinstaller` (dev group) |
| `apps/client_sidecar/scripts/build-sidecar.sh` | PyInstaller build + externalBin staging | Create |
| `apps/desktop/src-tauri/tauri.macos.conf.json` | macOS bundle config | Modify: add sidecar to `externalBin`, prepend build step |
| `apps/desktop/src-tauri/tauri.conf.json` | base bundle config | Modify: raise `macOS.minimumSystemVersion` 11.0 → 14.2 |
| `apps/desktop/package.json` | desktop npm scripts | Modify: add `build:sidecar-mac` |
| `apps/desktop/src/setup/platformConfig.ts`, `apps/desktop/src/setup/SidecarRunnerPanel.tsx` | operator UX | Modify: Mac copy from BlackHole/uv-primary to bundled native sidecar; project folder as dev fallback |
| `docs/ROADMAP.md`, `docs/PRD.md`, `docs/plans/2026-05-27-native-audio.md` | docs sync | Modify: annotate β-5 / cross-link |

> `apps/desktop/src-tauri/binaries/` is **gitignored**; staged binaries are build artifacts, never committed. The stale `yeson-sidecar-x86_64-pc-windows-gnu.exe` there is **untracked** → removing it is a non-committed `rm`.

> **Blocker fixes folded into this plan:** no broad `--collect-submodules apps.client_sidecar`; no API key in stdout/app logs; macOS 14.2 native runtime gate; CA trust-store consumption is not CA deployment automation.

---

## Task 1: Lazy sounddevice import in the factory

**Files:**
- Modify: `apps/client_sidecar/audio/sources/factory.py:22-24,35-59`
- Test: `apps/client_sidecar/tests/test_source_factory.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/client_sidecar/tests/test_source_factory.py`:

```python
def test_native_make_source_does_not_require_sounddevice(monkeypatch, tmp_path):
    """Lean-bundle guard: the native path must NOT import the sounddevice chain.

    Blocks `sounddevice`/`samplerate` imports and clears cached sidecar audio
    modules, then forces a fresh factory import. Under eager imports the factory
    import itself raises ImportError; under lazy imports the native branch builds
    a NativePipeSource without touching sounddevice.
    """
    import sys

    monkeypatch.setitem(sys.modules, "sounddevice", None)
    monkeypatch.setitem(sys.modules, "samplerate", None)
    for name in list(sys.modules):
        if name.startswith("apps.client_sidecar.audio"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    fake_bin = tmp_path / "yeson-mac-audio-helper"
    fake_bin.write_bytes(b"\x00")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "native")
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", str(fake_bin))

    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
    assert isinstance(src, NativePipeSource)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/client_sidecar/tests/test_source_factory.py::test_native_make_source_does_not_require_sounddevice -v`
Expected: FAIL/ERROR — `ImportError` raised at `from apps.client_sidecar.audio.sources.factory import make_source` (eager top-level import of `sounddevice_source` → `capture` → `import sounddevice`, which is blocked).

- [ ] **Step 3: Make the implementation change (lazy import)**

In `apps/client_sidecar/audio/sources/factory.py`, delete the top-level import (currently line 24):

```python
from apps.client_sidecar.audio.sources.sounddevice_source import SoundDeviceSource
```

So the import block becomes exactly:

```python
from apps.client_sidecar.audio.source import AudioSource
from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
from apps.client_sidecar.config.audio import NATIVE_HELPER_BIN_PATH
```

Then add the import locally in the two branches that use it. The `sounddevice` branch:

```python
    if provider == "sounddevice":
        logger.warning(
            "audio provider: sounddevice (emergency fallback — opted in via env)"
        )
        from apps.client_sidecar.audio.sources.sounddevice_source import SoundDeviceSource
        return SoundDeviceSource()
```

And the `auto` deprecated fallback at the end of `make_source`:

```python
    logger.warning(
        "audio provider: sounddevice (auto fallback — native helper missing at %s; "
        "auto mode is deprecated, prefer YESON_AUDIO_PROVIDER=native or =sounddevice)",
        bin_path,
    )
    from apps.client_sidecar.audio.sources.sounddevice_source import SoundDeviceSource
    return SoundDeviceSource()
```

Leave the `native_pipe_source` import at the top and all `NativePipeSource` branches unchanged. Stay inside the `SOURCE_FACTORY` anchor.

- [ ] **Step 4: Run the full factory test file to verify pass + no regressions**

Run: `uv run pytest apps/client_sidecar/tests/test_source_factory.py -v`
Expected: PASS — all prior tests (sounddevice/native/auto/default) plus the new guard test pass.

- [ ] **Step 5: Refresh the project map (vibelign), then commit**

```bash
vib anchor || true   # safe-zone refresh; ignore if vib unavailable
git add apps/client_sidecar/audio/sources/factory.py apps/client_sidecar/tests/test_source_factory.py
git commit -m "feat(sidecar): lazy-import sounddevice in factory for lean native bundle

Native path no longer pulls numpy/sounddevice/samplerate, so the PyInstaller
bundle can exclude them. Emergency sounddevice fallback still works in dev.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Redact sidecar startup URL before standalone logs

The desktop app forwards sidecar stdout/stderr to app logs (`sidecar.rs::spawn_output_forwarder`) and those logs are exportable. The Device API Key currently leaks via the `?key=` query string at **three** sites — fix all three in one commit:
- `apps/client_sidecar/main.py:53` (`audio_main` — the standalone path)
- `apps/client_sidecar/main.py:36` (`fixture_main`)
- `apps/client_sidecar/transport/server_ws.py:26` (`logger.info("connected to %s", url)`)

`audio_ws.py` already redacts via its `_safe_url`, so it is left unchanged.

**Files:**
- Modify: `apps/client_sidecar/main.py:36,53`
- Modify: `apps/client_sidecar/transport/server_ws.py:26`
- Test: `apps/client_sidecar/tests/test_audio_main_smoke.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/client_sidecar/tests/test_audio_main_smoke.py`:

```python
def test_audio_main_redacts_device_key(monkeypatch, capsys):
    """The audio startup line is forwarded to the desktop app log, so it must
    never contain the Device API Key or the raw ?key= query string."""
    import asyncio

    fake_key = "redactable-placeholder-value"
    monkeypatch.setenv("YESON_DEVICE_API_KEY", fake_key)
    monkeypatch.setenv("YESON_SESSION_ID", "00000000-0000-0000-0000-000000000000")

    class _FakeSource:
        async def chunks(self):
            return
            yield b""  # pragma: no cover — makes this an async generator

        async def close(self):
            pass

    async def _fake_stream_audio(url, chunks):
        return None

    # audio_main imports these names inside the function, so patch them at the
    # source module (the local `from … import` then binds the patched object).
    monkeypatch.setattr(
        "apps.client_sidecar.audio.sources.factory.make_source",
        lambda: _FakeSource(),
    )
    monkeypatch.setattr(
        "apps.client_sidecar.transport.audio_ws.stream_audio",
        _fake_stream_audio,
    )

    from apps.client_sidecar.main import audio_main
    asyncio.run(audio_main())

    out = capsys.readouterr().out
    assert fake_key not in out
    assert "key=<redacted>" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/client_sidecar/tests/test_audio_main_smoke.py -v`
Expected: FAIL — current `audio_main()` prints `url={url}` including `?key=<device key>`.

- [ ] **Step 3: Redact all three leak sites**

In `apps/client_sidecar/main.py`, `audio_main()` (line 53):

```python
    print(f"sidecar audio mode → source={type(source).__name__} url={url.split('?')[0]}?key=<redacted>")
```

In `apps/client_sidecar/main.py`, `fixture_main()` (line 36):

```python
    print(f"sidecar fixture mode → {url.split('?')[0]}?key=<redacted>")
```

In `apps/client_sidecar/transport/server_ws.py`, `send_events()` (line 26, inside the `SERVER_WS` anchor):

```python
                logger.info("connected to %s", url.split("?")[0])
```

The `?key=<redacted>` suffix mirrors the existing `audio_ws._safe_url` format; the inline duplication is intentional (minimal patch, no new shared module / cross-transport import).

- [ ] **Step 4: Run the smoke test file**

Run: `uv run pytest apps/client_sidecar/tests/test_audio_main_smoke.py -v`
Expected: PASS, including the new no-secret assertion.

- [ ] **Step 5: Commit**

```bash
git add apps/client_sidecar/main.py apps/client_sidecar/transport/server_ws.py apps/client_sidecar/tests/test_audio_main_smoke.py
git commit -m "fix(sidecar): redact device key from startup/connect logs

The desktop app forwards sidecar stdout/stderr to exportable app logs, so the
audio + fixture startup lines and the server_ws connect log must not contain
the raw /ws/sidecar query string with the Device API Key.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Trust the OS certificate store via truststore

**Files:**
- Modify: `apps/client_sidecar/pyproject.toml` (add `truststore` runtime dep)
- Modify: `apps/client_sidecar/main.py:103-110` (add `_install_os_trust_store()`, call in `run()`)
- Test: `apps/client_sidecar/tests/test_tls_bootstrap.py` (create)

- [ ] **Step 1: Add truststore as a runtime dependency**

Run (edits `[project].dependencies` and syncs the workspace venv):

```bash
cd apps/client_sidecar && uv add truststore && cd -
```

Expected: `pyproject.toml` gains `truststore>=…` under `dependencies`; `uv.lock` updated; venv synced.

- [ ] **Step 2: Write the failing test**

Create `apps/client_sidecar/tests/test_tls_bootstrap.py`:

```python
"""TLS bootstrap: the sidecar trusts the OS cert store (Caddy private CA)."""
from __future__ import annotations


def test_install_os_trust_store_injects(monkeypatch):
    import truststore

    calls = []
    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: calls.append(True))

    from apps.client_sidecar.main import _install_os_trust_store
    _install_os_trust_store()

    assert calls == [True]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest apps/client_sidecar/tests/test_tls_bootstrap.py -v`
Expected: FAIL — `ImportError: cannot import name '_install_os_trust_store' from 'apps.client_sidecar.main'`.

- [ ] **Step 4: Implement `_install_os_trust_store()` and wire it into `run()`**

In `apps/client_sidecar/main.py`, insert a new anchored function between `# === ANCHOR: MAIN_MAIN_END ===` (line 103) and `# === ANCHOR: MAIN_RUN_START ===` (line 106):

```python
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
```

Then call it in `run()` (inside the existing `MAIN_RUN` anchor), before `asyncio.run`:

```python
def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _install_os_trust_store()
    asyncio.run(main())
```

Do not modify `audio_ws.py` or `server_ws.py` — the global injection makes their existing `websockets.connect(wss://…)` calls use the OS-trust-backed default SSL context.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest apps/client_sidecar/tests/test_tls_bootstrap.py -v`
Expected: PASS.

- [ ] **Step 6: Add TLS failure UX note to operator checklist**

Do not treat `truststore` as CA deployment automation. It only makes Python consume the OS trust store. Operator docs/checklists must still say:
- register the Caddy root CA in macOS Keychain,
- set it to trusted for SSL,
- if untrusted, sidecar logs should clearly point to Root CA trust registration rather than looking like a generic reconnect loop.

If a smoke run shows cert failures hidden behind repeated reconnect logs, add a small follow-up patch in `audio_ws.py` to detect `ssl.SSLCertVerificationError` and log a clear `ROOT_CA_UNTRUSTED` status before retrying.

- [ ] **Step 7: Refresh project map, then commit**

```bash
vib anchor || true
git add apps/client_sidecar/main.py apps/client_sidecar/tests/test_tls_bootstrap.py apps/client_sidecar/pyproject.toml uv.lock
git commit -m "feat(sidecar): trust OS cert store via truststore

Sidecar websockets connections now validate against the OS trust store
(macOS Keychain / Windows cert store) — same source as the webview — so the
Caddy private CA registered on the meeting PC is honored without shipping a
cert or SSL_CERT_FILE workaround.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

> Note: commands assume CWD = repo root (the `cd … && cd -` returns there). `uv.lock` is at the repo root. If `uv add` did not change the lock, drop it from the commit.

---

## Task 4: PyInstaller standalone build + Tauri externalBin wiring

**Files:**
- Modify: `apps/client_sidecar/pyproject.toml` (add `pyinstaller` dev dep)
- Create: `apps/client_sidecar/scripts/build-sidecar.sh`
- Modify: `apps/desktop/src-tauri/tauri.macos.conf.json`
- Modify: `apps/desktop/package.json:8-9`
- Housekeeping: remove untracked `apps/desktop/src-tauri/binaries/yeson-sidecar-x86_64-pc-windows-gnu.exe`

- [ ] **Step 1: Add PyInstaller as a dev dependency**

```bash
cd apps/client_sidecar && uv add --dev pyinstaller && cd -
```

Expected: `pyproject.toml` gains `[dependency-groups] dev = ["pyinstaller>=…"]`; venv synced (PyInstaller installed into the workspace venv).

> The build script must invoke PyInstaller through the sidecar project explicitly. Do **not** rely on repo-root `uv run pyinstaller`, because the root project has no PyInstaller dev dependency.

- [ ] **Step 2: Create the build script**

Create `apps/client_sidecar/scripts/build-sidecar.sh` (then `chmod +x` it):

```bash
#!/usr/bin/env bash
set -euo pipefail

# Build the Python client sidecar into a standalone single-file executable and
# stage it where Tauri's externalBin expects it. Mirrors
# native_helper_mac/scripts/build-release.sh. Lean native-only: excludes
# numpy/sounddevice/samplerate (sounddevice fallback is dev-only).

# repo root = scripts/../../.. (apps/client_sidecar/scripts -> repo root)
cd "$(dirname "$0")/../../.."

echo "Building yeson-sidecar (PyInstaller, lean native-only)…"
uv run --project apps/client_sidecar pyinstaller \
    --noconfirm --clean --onefile \
    --name yeson-sidecar \
    --paths . \
    --collect-submodules truststore \
    --exclude-module sounddevice \
    --exclude-module samplerate \
    --exclude-module numpy \
    --distpath target/sidecar-dist \
    --workpath target/sidecar-build \
    --specpath target/sidecar-build \
    apps/client_sidecar/main.py

OUT="target/sidecar-dist/yeson-sidecar"
if [[ ! -f "$OUT" ]]; then
    echo "ERROR: expected binary at $OUT" >&2
    exit 1
fi

# Map host arch → Tauri target-triple suffix expected by externalBin.
# Single-arch (host) binary; universal (lipo) deferred to β-5 codesign.
case "$(uname -m)" in
    arm64)   TRIPLE="aarch64-apple-darwin" ;;
    x86_64)  TRIPLE="x86_64-apple-darwin" ;;
    *)
        echo "ERROR: unsupported host arch: $(uname -m)" >&2
        exit 1
        ;;
esac

DEST_BUNDLE="apps/desktop/src-tauri/binaries/yeson-sidecar-${TRIPLE}"
mkdir -p "$(dirname "$DEST_BUNDLE")"
cp "$OUT" "$DEST_BUNDLE"
echo "→ $DEST_BUNDLE"
echo "  size: $(stat -f%z "$DEST_BUNDLE") bytes"
```

Do **not** add `--collect-submodules apps.client_sidecar`: `apps/client_sidecar/tests/__init__.py` exists, so that glob would sweep in the test package (and its `pytest`/`sounddevice` imports), defeating the lean bundle. The narrow `--collect-submodules truststore` is safe and necessary — truststore picks its platform backend (`truststore._macos`) via a runtime import that PyInstaller's plain analysis can miss, and truststore ships no tests/heavy deps. Beyond that, rely on PyInstaller's normal import-following; add further narrowly-scoped hidden imports only if the clean smoke in Step 7 proves one is missing.

Then:

```bash
chmod +x apps/client_sidecar/scripts/build-sidecar.sh
```

- [ ] **Step 3: Add the desktop npm script**

In `apps/desktop/package.json`, under `"scripts"`, add a line next to the existing `build:native-helper-mac` (line 9):

```json
    "build:native-helper-mac": "../native_helper_mac/scripts/build-release.sh",
    "build:sidecar-mac": "../client_sidecar/scripts/build-sidecar.sh",
```

- [ ] **Step 4: Wire the sidecar into the macOS Tauri config**

Replace the entire contents of `apps/desktop/src-tauri/tauri.macos.conf.json` with:

```json
{
  "build": {
    "beforeDevCommand": "pnpm build:native-helper-mac && pnpm build:sidecar-mac && pnpm dev:vite",
    "beforeBuildCommand": "pnpm build:native-helper-mac && pnpm build:sidecar-mac && pnpm build:vite"
  },
  "bundle": {
    "externalBin": ["binaries/yeson-mac-audio-helper", "binaries/yeson-sidecar"]
  }
}
```

- [ ] **Step 5: Remove the stale untracked Windows placeholder**

```bash
rm -f apps/desktop/src-tauri/binaries/yeson-sidecar-x86_64-pc-windows-gnu.exe
```

Expected: no git change (file is untracked, `binaries/` is gitignored). Verify: `git status --short` does not list it.

- [ ] **Step 6: Build the sidecar binary and verify staging**

Run: `pnpm --filter @yeson-meet/desktop run build:sidecar-mac`
Expected: PyInstaller runs via `uv run --project apps/client_sidecar ...`, ending with:
```
→ apps/desktop/src-tauri/binaries/yeson-sidecar-aarch64-apple-darwin
  size: <several MB> bytes
```
Confirm: `ls -la apps/desktop/src-tauri/binaries/yeson-sidecar-*` shows the new arch-suffixed binary.
Also inspect the build log. If it imports or bundles `apps.client_sidecar.tests`, `pytest`, `sounddevice`, `samplerate`, or `numpy`, treat that as a failure for this lean slice.

- [ ] **Step 7: Standalone smoke — prove no repo/uv/PATH dependence**

Run (replace `<ABS>` with the absolute repo path):

```bash
cd /tmp && env -i PATH=/usr/bin:/bin HOME="$HOME" \
  "<ABS>/apps/desktop/src-tauri/binaries/yeson-sidecar-aarch64-apple-darwin"; echo "exit=$?"; cd -
```

Expected: prints `missing env var: YESON_DEVICE_API_KEY` and `exit=2`. This proves the bundled interpreter ran, `truststore.inject_into_ssl()` succeeded, and `apps.client_sidecar.*` imported — with `uv` off PATH and outside the repo. A `ModuleNotFoundError`, truststore backend ImportError, or `uv: command not found` means the bundle is incomplete.

- [ ] **Step 8: Commit (script + configs + dev dep; binary is gitignored)**

```bash
git add apps/client_sidecar/scripts/build-sidecar.sh apps/client_sidecar/pyproject.toml uv.lock \
        apps/desktop/src-tauri/tauri.macos.conf.json apps/desktop/package.json
git commit -m "build(sidecar): bundle standalone PyInstaller sidecar (macOS)

build-sidecar.sh produces a lean native-only single-file binary and stages it
for Tauri externalBin; tauri.macos.conf.json bundles it and auto-builds it
before dev/build. Activates the dormant locate_bundled_sidecar() path so the
.app no longer needs repo+uv at runtime.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

> Commands assume CWD = repo root. Run `git status --short` first to confirm only the intended files (and not the gitignored binary) are staged.

---

## Task 5: Standalone Mac operator UX and macOS runtime gate

**Files:**
- Modify: `apps/desktop/src-tauri/tauri.conf.json` (raise `macOS.minimumSystemVersion`)
- Modify: `apps/desktop/src/setup/platformConfig.ts`
- Modify: `apps/desktop/src/setup/SidecarRunnerPanel.tsx`
- Optional: `apps/desktop/src/setup/SetupAssistant.tsx` if the project-folder field needs clearer dev-only copy

- [ ] **Step 1: Update Mac copy away from BlackHole/uv-primary**

Change Mac platform labels/help so packaged Mac is described as native bundled sidecar first, not BlackHole first. BlackHole/sounddevice should read as fallback/dev compatibility only.

Examples:
- label: `Mac client (native system audio)`
- audio help: `Standalone Mac uses the bundled native ScreenCaptureKit helper; this field is only for dev/fallback sounddevice runs.`
- runner intro: mention bundled sidecar first, with `uv`/project folder as dev fallback only.

- [ ] **Step 2: Raise the packaged macOS floor to 14.2**

`apps/native_helper_mac/Package.swift` sets `platforms: [.macOS("14.2")]` (ScreenCaptureKit system-audio stability floor), while `apps/desktop/src-tauri/tauri.conf.json` sets `minimumSystemVersion: "11.0"`. That comment assumes "14.0–14.1 use BlackHole compat" — **but this lean bundle ships no sounddevice fallback**, so on macOS < 14.2 the packaged app has *no* working audio path (helper won't launch, sounddevice isn't bundled). Therefore raise the floor rather than ship a dead install.

In `apps/desktop/src-tauri/tauri.conf.json`, under `bundle.macOS`:

```json
    "macOS": {
      "minimumSystemVersion": "14.2"
    }
```

(Optional polish, not required for this slice: a start-sidecar preflight that maps a `< 14.2` launch failure to a clear "Native standalone audio requires macOS 14.2+" message. The raised floor makes this an edge case, since the installer itself now blocks older macOS.) The dev path (`uv`/`tauri dev`) is unaffected — `minimumSystemVersion` only gates the packaged `.app`.

- [ ] **Step 3: Verify standalone does not fall back through stale project dir**

For packaged E2E, clear `Sidecar project folder` and assert the app log includes `starting bundled sidecar:`. If it starts `uv`, the test is not a standalone proof.

- [ ] **Step 4: Run desktop tests**

Run: `pnpm --filter @yeson-meet/desktop test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src-tauri/tauri.conf.json apps/desktop/src/setup/platformConfig.ts apps/desktop/src/setup/SidecarRunnerPanel.tsx apps/desktop/src/setup/SetupAssistant.tsx
git commit -m "feat(desktop): standalone Mac sidecar UX + raise macOS floor to 14.2

Mac setup copy now treats the bundled native sidecar as the primary packaged
path (uv/project-dir and BlackHole scoped to dev/fallback). Raise packaged
minimumSystemVersion to 14.2 to match the native helper's ScreenCaptureKit
floor — the lean bundle has no sounddevice fallback on older macOS.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

If `SetupAssistant.tsx` is unchanged, drop it from `git add`.

---

## Task 6: Documentation sync

**Files:**
- Modify: `docs/ROADMAP.md:304-311`
- Modify: `docs/PRD.md:296-297`
- Modify: `docs/plans/2026-05-27-native-audio.md:32`

- [ ] **Step 1: Annotate ROADMAP β-5**

In `docs/ROADMAP.md`, replace:

```markdown
### β-5 — 인스톨러 + 자동 업데이트 (3~4일)

- [ ] Tauri MSI 빌드 스크립트 1순위, DMG 빌드 스크립트 2순위
- [ ] PyInstaller로 sidecar 단일 실행파일
- [ ] macOS 코드사인 + 노타리제이션
- [ ] Windows 코드사인
- [ ] Tauri Updater (GitHub Releases 또는 사내 정적 서버)
- [ ] 사내 root CA 인증서 배포 자동화
```

with:

```markdown
### β-5 — 인스톨러 + 자동 업데이트 (3~4일)

> (2026-05-28) **macOS standalone sidecar local/internal beta seam** — PyInstaller lean 번들 + truststore(OS 신뢰저장소) TLS (`topyeson`). 설계: `docs/superpowers/specs/2026-05-28-standalone-mac-sidecar-design.md`. 잔여: MSI/DMG·codesign·notarization·Updater·Windows sidecar·CA 배포 자동화.

- [ ] Tauri MSI 빌드 스크립트 1순위, DMG 빌드 스크립트 2순위
- [ ] PyInstaller로 sidecar 단일 실행파일 — macOS local/internal beta seam ✅ 2026-05-28 (lean native-only); Windows ⏳ Phase 2
- [ ] macOS 코드사인 + 노타리제이션
- [ ] Windows 코드사인
- [ ] Tauri Updater (GitHub Releases 또는 사내 정적 서버)
- [ ] 사내 root CA 인증서 배포 자동화 — 미완료. 이번 slice 는 truststore 로 **소비 경로만** OS 신뢰저장소에 맞춤; 회의실 PC Keychain root CA 등록/Always Trust 절차는 별도 운영 과제
```

- [ ] **Step 2: Annotate PRD β-5**

In `docs/PRD.md`, replace:

```markdown
#### β-5 — 인스톨러 + 자동 업데이트
- Tauri MSI / DMG, PyInstaller로 sidecar 단일 실행파일
```

with:

```markdown
#### β-5 — 인스톨러 + 자동 업데이트
> (2026-05-28) macOS: sidecar PyInstaller 단일 실행파일 + truststore(OS 신뢰저장소) standalone **local/internal beta seam**. Windows sidecar·codesign·notarization·Updater·CA 배포 자동화 잔여.
- Tauri MSI / DMG, PyInstaller로 sidecar 단일 실행파일
```

- [ ] **Step 3: Cross-link the native-audio plan**

In `docs/plans/2026-05-27-native-audio.md`, replace the line (32):

```markdown
**Next decision**: run Task 7 (4 baseline scenarios) → native adoption / Task 24-25 GO/HOLD per Task 7 Step 7 exit-criteria table. Tasks 8-23 are already implemented, so this gates measurement/smoke continuation rather than Phase 1 coding start.
```

with:

```markdown
**Next decision**: run Task 7 (4 baseline scenarios) → native adoption / Task 24-25 GO/HOLD per Task 7 Step 7 exit-criteria table. Tasks 8-23 are already implemented, so this gates measurement/smoke continuation rather than Phase 1 coding start.

> (2026-05-28) Packaging seam 후속: Python sidecar standalone(PyInstaller lean + truststore)은 별도 슬라이스로 분리 — `docs/superpowers/plans/2026-05-28-standalone-mac-sidecar.md` (spec: `docs/superpowers/specs/2026-05-28-standalone-mac-sidecar-design.md`). 범위는 codesign/notarization 전 local/internal beta.
```

- [ ] **Step 4: Commit**

```bash
git add docs/ROADMAP.md docs/PRD.md docs/plans/2026-05-27-native-audio.md
git commit -m "docs(native-audio): note macOS standalone sidecar slice (PyInstaller + truststore)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] **Full sidecar test suite:** `uv run pytest apps/client_sidecar/tests -v` → all green.
- [ ] **Lean bundle audit:** PyInstaller log/artifact inspection shows no `apps.client_sidecar.tests`, `pytest`, `sounddevice`, `samplerate`, or `numpy` in the standalone runtime.
- [ ] **No secret in logs:** start sidecar once and export/read app logs; confirm no `YESON_DEVICE_API_KEY`, raw device key, or `?key=` appears.
- [ ] **Clean standalone binary smoke:** from `/tmp`, with `env -i PATH=/usr/bin:/bin HOME="$HOME"`, run the staged sidecar binary and see `missing env var: YESON_DEVICE_API_KEY` + `exit=2` without `uv`, repo CWD, or `ModuleNotFoundError`.
- [ ] **Bundle contains the sidecar:** `pnpm --filter @yeson-meet/desktop run tauri:build` (or `pnpm tauri build` in `apps/desktop`), then `ls "apps/desktop/src-tauri/target/release/bundle/macos/yeson-meet.app/Contents/MacOS/"` shows both `yeson-sidecar` and `yeson-mac-audio-helper`.
- [ ] **macOS support gate:** verify the chosen 14.2+ policy (minimumSystemVersion or runtime preflight) before operator handoff.
- [ ] **Operator E2E (real standalone gate — handoff):** Launch the bundled `.app` from Finder (not terminal). Start a meeting → first subtitle appears, with **no** `SSL_CERT_FILE` env and **no** dev project-dir set in Setup. Confirm app logs include `starting bundled sidecar:` and not `starting dev sidecar via uv+python`. Confirms PyInstaller bundling + truststore both work end-to-end on a packaged app. Hand this step to the operator with the exact build path and a meeting server reachable over the LAN whose Caddy root CA is registered in the Mac Keychain with SSL trust enabled.

## Notes / known characteristics

- `--onefile` extracts to a tempdir on each start (~0.5–1s). Fine for a once-per-meeting sidecar; `externalBin` wants a single path.
- arm64 build → arm64-only binary. Universal (lipo) deferred to β-5 codesign (mirrors the helper script).
- Lean bundle = no in-`.app` sounddevice fallback; it remains available in dev (`uv`). Matches native-only cutover.
- No planned changes to `sidecar.rs`, `audio_ws.py`, or `server_ws.py` unless TLS failure smoke shows certificate errors are hidden behind an unhelpful reconnect loop.
- Codesign/notarization/updater are still out of scope, so the output is local/internal beta only.
