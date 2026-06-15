# native-only 컷오버 (Phase 4-A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make native (macOS ScreenCaptureKit / Windows WASAPI) the sole audio-capture path by removing the legacy sounddevice (BlackHole/Voicemeeter) path from the sidecar, the Tauri Rust layer, the desktop setup UX, and the docs.

**Architecture:** The sounddevice chain (`sounddevice_source → capture → resample / device`) is a self-contained subtree; delete it and simplify the factory to always build `NativePipeSource`. The `audioDeviceName` setup value threads desktop UI → `sidecarCommands`/`sidecarRunner` → Rust `SidecarStartRequest` → `YESON_AUDIO_DEVICE_NAME` env; remove it end-to-end. numpy stays (the level meter / RMS use it); only `sounddevice` + `samplerate` deps go.

**Tech Stack:** Python (sidecar, pytest), Rust (Tauri command + serde), React/TypeScript (vitest), Markdown docs.

**Spec:** `docs/superpowers/specs/2026-06-15-native-only-cutover-design.md`

---

## File Structure

**Sidecar (Python):**
- Modify: `apps/client_sidecar/audio/sources/factory.py` — native-only `make_source()`.
- Modify: `apps/client_sidecar/config/audio.py` — drop device-name/provider knobs.
- Delete: `apps/client_sidecar/audio/sources/sounddevice_source.py`, `audio/capture.py`, `audio/resample.py`, `audio/device.py`.
- Delete: `tests/test_sounddevice_source.py`, `tests/test_device_select.py`, `tests/test_resample.py`.
- Modify: `apps/client_sidecar/pyproject.toml` — drop `sounddevice`, `samplerate`.
- Modify (tests): `tests/test_source_factory.py` (rewrite), and fix any fallout in `tests/test_config_audio_paths.py`, `tests/test_audio_main_smoke.py`.

**Tauri (Rust):**
- Modify: `apps/desktop/src-tauri/src/sidecar.rs` — remove `audio_device_name` field + 2 `.env(YESON_AUDIO_DEVICE_NAME)` + 1 `require_value`.

**Desktop (TS):**
- Modify: `setup/types.ts`, `setup/setupValues.ts`, `setup/platformConfig.ts`, `setup/SetupAssistant.tsx`, `setup/sidecarCommands.ts`, `setup/sidecarRunner.ts`, `setup/SidecarRunnerPanel.tsx` (remove `audioDeviceName`).
- Modify: `setup/platformRunbook.ts`, `help/helpManualContent.ts` (native-first copy).

**Docs:** `docs/ROADMAP.md`, `docs/PRD.md`.

**Commands:**
- Sidecar tests: `uv run pytest apps/client_sidecar/tests -q`
- Desktop tests: `pnpm --filter @yeson-meet/desktop test`
- Desktop types: `pnpm --filter @yeson-meet/desktop exec tsc --noEmit`
- Rust check: `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml`

**Constraint for every task:** Do NOT modify/stage `PROJECT_CONTEXT.md` or `apps/desktop/scripts/vm_dump.py`. Commit trailer on every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Stay inside existing anchors.

---

## Task 1: Sidecar factory + config → native-only

**Files:**
- Modify: `apps/client_sidecar/audio/sources/factory.py`
- Modify: `apps/client_sidecar/config/audio.py`
- Test: `apps/client_sidecar/tests/test_source_factory.py` (rewrite)

- [ ] **Step 1: Rewrite the factory test to native-only expectations**

Replace the ENTIRE contents of `apps/client_sidecar/tests/test_source_factory.py` with:

```python
"""Provider factory: native-only (sounddevice/auto removed, 2026-06-15 cutover)."""
from __future__ import annotations

import logging

import pytest

from apps.client_sidecar.audio.source import AudioSource


def _fake_helper(tmp_path):
    fake_bin = tmp_path / "yeson-mac-audio-helper"
    fake_bin.write_bytes(b"\x00")
    fake_bin.chmod(0o755)
    return fake_bin


def test_factory_returns_native_when_bin_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("YESON_AUDIO_PROVIDER", raising=False)
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", str(_fake_helper(tmp_path)))
    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
    assert isinstance(src, NativePipeSource)
    assert isinstance(src, AudioSource)


def test_factory_raises_when_bin_missing(monkeypatch):
    monkeypatch.delenv("YESON_AUDIO_PROVIDER", raising=False)
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", "/nonexistent/yeson-helper")
    from apps.client_sidecar.audio.sources.factory import make_source
    with pytest.raises(FileNotFoundError):
        make_source()


def test_factory_warns_and_uses_native_for_removed_provider(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("YESON_AUDIO_PROVIDER", "sounddevice")  # removed value
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", str(_fake_helper(tmp_path)))
    from apps.client_sidecar.audio.sources.factory import make_source
    with caplog.at_level(logging.WARNING):
        src = make_source()
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
    assert isinstance(src, NativePipeSource)
    assert any("removed" in r.getMessage() for r in caplog.records)


def test_factory_native_path_does_not_import_sounddevice(monkeypatch, tmp_path):
    """Lean-bundle guard: the native path must not import the sounddevice chain."""
    import sys

    monkeypatch.setitem(sys.modules, "sounddevice", None)
    monkeypatch.setitem(sys.modules, "samplerate", None)
    for name in list(sys.modules):
        if name.startswith("apps.client_sidecar.audio"):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.delenv("YESON_AUDIO_PROVIDER", raising=False)
    monkeypatch.setenv("YESON_NATIVE_HELPER_BIN", str(_fake_helper(tmp_path)))
    from apps.client_sidecar.audio.sources.factory import make_source
    src = make_source()
    from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
    assert isinstance(src, NativePipeSource)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/client_sidecar/tests/test_source_factory.py -q`
Expected: `test_factory_warns_and_uses_native_for_removed_provider` FAILS (current factory builds SoundDeviceSource for "sounddevice", no "removed" warning). Others may pass.

- [ ] **Step 3: Rewrite `factory.py`**

Replace the ENTIRE contents of `apps/client_sidecar/audio/sources/factory.py` with:

```python
# === ANCHOR: SOURCE_FACTORY_START ===
"""Select the AudioSource implementation.

Native-only policy (2026-06-15 cutover): the OS-level helper (macOS
ScreenCaptureKit / Windows WASAPI) is the sole capture path. The legacy
sounddevice (BlackHole/Voicemeeter) path and the `auto` transition mode were
removed once native landed on both platforms (see
docs/superpowers/specs/2026-06-15-native-only-cutover-design.md). A missing
helper binary raises FileNotFoundError so packaging gaps surface loudly.
"""
from __future__ import annotations

import logging
import os

from apps.client_sidecar.audio.source import AudioSource
from apps.client_sidecar.audio.sources.native_pipe_source import NativePipeSource
from apps.client_sidecar.config.audio import NATIVE_HELPER_BIN_PATH

logger = logging.getLogger(__name__)


def make_source() -> AudioSource:
    provider = os.environ.get("YESON_AUDIO_PROVIDER")
    if provider and provider.lower() != "native":
        logger.warning(
            "YESON_AUDIO_PROVIDER=%s is removed (native-only); using native", provider
        )
    bin_path = os.environ.get("YESON_NATIVE_HELPER_BIN", NATIVE_HELPER_BIN_PATH)
    if not os.path.isfile(bin_path):
        raise FileNotFoundError(f"native audio helper binary missing: {bin_path}")
    logger.info("audio provider: native (bin=%s)", bin_path)
    return NativePipeSource(bin_path=bin_path)
# === ANCHOR: SOURCE_FACTORY_END ===
```

- [ ] **Step 4: Edit `config/audio.py` — remove device-name + provider knobs**

In `apps/client_sidecar/config/audio.py`, DELETE this block (the "Device selection" lines):

```python
# Device selection (regex matched against sounddevice.query_devices()[i]['name'])
DEVICE_NAME_REGEX: str = os.environ.get("YESON_AUDIO_DEVICE_NAME", r"(?i)blackhole")
DEVICE_INDEX: int | None = (
    int(os.environ["YESON_AUDIO_DEVICE_INDEX"])
    if os.environ.get("YESON_AUDIO_DEVICE_INDEX")
    else None
)

```

Then replace the provider comment + constant (top of the `AUDIO_PROVIDER` anchor). Change:

```python
# === ANCHOR: AUDIO_PROVIDER_START ===
# Provider selection (policy: native-only, no silent fallback).
#   native      — default; OS-level helper (macOS ScreenCaptureKit /
#                 Phase 2 Windows WASAPI). Missing binary → FileNotFoundError.
#   sounddevice — emergency fallback for BlackHole/Voicemeeter compat;
#                 opt-in only via env override.
#   auto        — transition aid (try native, silently fall back to
#                 sounddevice). Deprecated; remove once Windows native lands.
YESON_AUDIO_PROVIDER: str = os.environ.get("YESON_AUDIO_PROVIDER", "native").lower()
```

to:

```python
# === ANCHOR: AUDIO_PROVIDER_START ===
# Native-only capture (2026-06-15 cutover): the OS-level helper (macOS
# ScreenCaptureKit / Windows WASAPI) is the sole path. Missing binary →
# FileNotFoundError (see audio/sources/factory.py). The sounddevice path and
# the YESON_AUDIO_PROVIDER / device-name knobs were removed.
```

Leave everything else in the file (the `_REPO_ROOT` / `_NATIVE_HELPER_DEFAULT` / `NATIVE_HELPER_BIN_PATH` lines, RMS constants, TARGET_*/CHUNK_*) unchanged.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest apps/client_sidecar/tests/test_source_factory.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add apps/client_sidecar/audio/sources/factory.py apps/client_sidecar/config/audio.py apps/client_sidecar/tests/test_source_factory.py
git commit -m "refactor(sidecar): native-only source factory; drop provider/device-name knobs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Delete the sounddevice subtree + deps

**Files:**
- Delete: `apps/client_sidecar/audio/sources/sounddevice_source.py`, `apps/client_sidecar/audio/capture.py`, `apps/client_sidecar/audio/resample.py`, `apps/client_sidecar/audio/device.py`
- Delete: `apps/client_sidecar/tests/test_sounddevice_source.py`, `apps/client_sidecar/tests/test_device_select.py`, `apps/client_sidecar/tests/test_resample.py`
- Modify: `apps/client_sidecar/pyproject.toml`
- Modify (if needed): `apps/client_sidecar/tests/test_config_audio_paths.py`, `apps/client_sidecar/tests/test_audio_main_smoke.py`

- [ ] **Step 1: Delete the source files and their tests**

```bash
git rm apps/client_sidecar/audio/sources/sounddevice_source.py \
       apps/client_sidecar/audio/capture.py \
       apps/client_sidecar/audio/resample.py \
       apps/client_sidecar/audio/device.py \
       apps/client_sidecar/tests/test_sounddevice_source.py \
       apps/client_sidecar/tests/test_device_select.py \
       apps/client_sidecar/tests/test_resample.py
```

- [ ] **Step 2: Verify no remaining imports reference the deleted modules**

Run:
```bash
grep -rn "import sounddevice\|import samplerate\|sources.sounddevice_source\|audio.capture\|audio.resample\|audio.device\b\|DEVICE_NAME_REGEX\|DEVICE_INDEX\|YESON_AUDIO_DEVICE_NAME\|YESON_AUDIO_DEVICE_INDEX" apps/client_sidecar
```
Expected: NO matches (empty). If any appear (e.g. in `test_config_audio_paths.py` or `test_audio_main_smoke.py`), open that file and remove/adjust the offending reference so it no longer depends on the deleted modules/constants. Keep the test's other assertions intact.

- [ ] **Step 3: Drop the deps in `pyproject.toml`**

In `apps/client_sidecar/pyproject.toml`, delete the two dependency lines:

```
  "sounddevice>=0.5",
  "samplerate>=0.2.1",
```

Keep `"numpy>=2.1",` (the level meter / RMS use it).

- [ ] **Step 4: Run the full sidecar suite**

Run: `uv run pytest apps/client_sidecar/tests -q`
Expected: PASS (the 3 deleted test files are gone; everything else green). If a test errors on a missing import, fix it per Step 2 guidance and re-run.

- [ ] **Step 5: Commit**

```bash
git add -A apps/client_sidecar
git commit -m "refactor(sidecar): delete sounddevice capture subtree + deps (native-only)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Rust — drop `audio_device_name` from the sidecar command

**Files:**
- Modify: `apps/desktop/src-tauri/src/sidecar.rs`

(No Rust unit harness; verified by `cargo check`. Must precede Task 4 so the intermediate state stays runnable: serde ignores the extra field the desktop still sends until Task 4 lands.)

- [ ] **Step 1: Remove the struct field**

In `SidecarStartRequest` (around line 128), delete the line:

```rust
    audio_device_name: String,
```

- [ ] **Step 2: Remove the two env injections**

There are two `.env("YESON_AUDIO_DEVICE_NAME", request.audio_device_name.trim())` lines (bundled + dev spawn arms, around lines 196 and 229). Delete BOTH lines.

- [ ] **Step 3: Remove the validation**

Delete the line (around line 380):

```rust
    require_value("YESON_AUDIO_DEVICE_NAME", &request.audio_device_name)?;
```

- [ ] **Step 4: Type-check**

Run: `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml`
Expected: `Finished` with no errors and no unused-variable warnings for `request.audio_device_name`. If the compiler flags an unused import/helper, leave `require_value` (still used by other fields) — only the one call line is removed.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src-tauri/src/sidecar.rs
git commit -m "refactor(desktop): drop audio_device_name from start_sidecar (native-only)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Desktop — remove `audioDeviceName` from the setup model

**Files:**
- Modify: `apps/desktop/src/setup/types.ts`, `setup/setupValues.ts`, `setup/platformConfig.ts`, `setup/SetupAssistant.tsx`, `setup/sidecarCommands.ts`, `setup/sidecarRunner.ts`, `setup/SidecarRunnerPanel.tsx`

- [ ] **Step 1: `types.ts` — drop the field**

In `SetupValues`, delete the line `  audioDeviceName: string;`.

- [ ] **Step 2: `platformConfig.ts` — drop the field from type + both platforms**

- In the `PlatformConfig` type, delete `  audioDeviceName: string;` and `  audioDeviceHelp: string;`.
- In `mac`, delete the `audioDeviceName: "(?i)blackhole",` and `audioDeviceHelp: "..."` lines.
- In `windows`, delete the `audioDeviceName: "Voicemeeter",` and `audioDeviceHelp: "..."` lines.

(Label/description copy is rewritten in Task 5 — leave those strings for now; this step only removes the two fields so types compile.)

- [ ] **Step 3: `setupValues.ts` — drop the field**

- In `defaultValues()`, delete the line `    audioDeviceName: PLATFORM_CONFIG[platform].audioDeviceName,`.
- In `loadValues()`, delete the line `      audioDeviceName: stored.audioDeviceName ?? PLATFORM_CONFIG[platform].audioDeviceName,`.
- If `PLATFORM_CONFIG` is now unused in this file after the edits, remove it from the import on line 2 (`import { defaultPlatform } from "./platformConfig";`). Verify by checking for other `PLATFORM_CONFIG` uses in the file first; `defaultPlatform` is still used, keep it.

- [ ] **Step 4: `SetupAssistant.tsx` — remove the input field + initializer**

- Delete the entire `<Field label="오디오 장치 이름" ... />` block (the one with `value={values.audioDeviceName}`), around lines 147–152.
- Delete the line `        audioDeviceName: PLATFORM_CONFIG[platform].audioDeviceName,` (around line 54, inside `updatePlatform`). If that leaves `PLATFORM_CONFIG` unused in the file, remove its import; otherwise keep it.

- [ ] **Step 5: `sidecarCommands.ts` — drop the env line + unused import**

- In `buildWindowsSidecarCommand`, delete the line:
  `` `$env:YESON_AUDIO_DEVICE_NAME=${powerShellValue(values.audioDeviceName, PLATFORM_CONFIG.windows.audioDeviceName)}`, ``
- In `buildMacSidecarCommand`, delete the line:
  `` `export YESON_AUDIO_DEVICE_NAME=${shellValue(values.audioDeviceName, PLATFORM_CONFIG.mac.audioDeviceName)}`, ``
- Remove the now-unused import `import { PLATFORM_CONFIG } from "./platformConfig";` (line 2). `powerShellValue`/`shellValue` stay (used by other fields).

- [ ] **Step 6: `sidecarRunner.ts` — drop the request field + validation**

- In `startSidecar`'s invoke request object, delete the line `        audioDeviceName: values.audioDeviceName,` (around line 33).
- In `validateSidecarValues`, delete the block:
  ```ts
  if (!values.audioDeviceName.trim()) {
    throw new Error("dev/fallback sounddevice 실행 시 오디오 장치 이름이 필요합니다.");
  }
  ```

- [ ] **Step 7: `SidecarRunnerPanel.tsx` — drop the validation hint**

Delete the line (around 89):
```ts
  if (!values.audioDeviceName.trim()) items.push("오디오 장치 이름이 필요합니다. dev/fallback sounddevice 실행 시 필요합니다.");
```

- [ ] **Step 8: Type-check + tests**

Run: `pnpm --filter @yeson-meet/desktop exec tsc --noEmit`
Expected: PASS — zero references to `audioDeviceName` remain (tsc would flag any straggler as a property error).

Run: `pnpm --filter @yeson-meet/desktop test`
Expected: PASS (no test references the removed field).

Also confirm no stragglers:
```bash
grep -rn "audioDeviceName" apps/desktop/src
```
Expected: NO matches.

- [ ] **Step 9: Commit**

```bash
git add apps/desktop/src/setup
git commit -m "refactor(desktop): remove audioDeviceName from setup model (native-only)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Desktop — native-first setup copy

**Files:**
- Modify: `apps/desktop/src/setup/platformConfig.ts`, `setup/platformRunbook.ts`, `help/helpManualContent.ts`

This task rewrites operator-facing copy so it no longer instructs Voicemeeter/BlackHole installation. The bundled native sidecar captures system audio directly on both platforms.

- [ ] **Step 1: `platformConfig.ts` — native-first labels**

Read the file. Rewrite the `windows` entry's `label` and `description`, and the `mac` entry's `commandTitle`/`commandHint`, so they describe native capture (no Voicemeeter, no "dev/fallback sounddevice"). Concretely:
- `windows.label`: `"Windows client (native system audio)"`
- `windows.description`: `"Windows 회의실 PC는 번들된 네이티브 WASAPI sidecar로 시스템 소리를 직접 캡처해 yeson-meet 서버로 보냅니다."`
- `mac.commandTitle`: `"2. 생성된 macOS zsh 명령 (dev 실행용)"`
- `mac.commandHint`: `"복사한 명령은 dev 실행 전용입니다. 패키지 앱은 번들된 네이티브 sidecar를 사용하므로 필요 없습니다."`
- `windows.commandHint`: keep referencing PowerShell + yeson-meet folder, but drop any Voicemeeter mention if present (current text has none — leave as is).

- [ ] **Step 2: `platformRunbook.ts` — rewrite to native-first**

Read the file. For BOTH platforms, rewrite `intro`, `steps`, and `reminder` so they:
- State the packaged app uses the bundled native sidecar to capture system audio directly; no virtual-audio install needed.
- Remove the Voicemeeter Banana install/reboot/B1-A1 routing steps (windows) and the "(dev/fallback) BlackHole + zsh" step + BlackHole reminders (mac).
- Keep genuinely native-relevant guidance (set output device you want captured = the system default; Device API Key not stored, paste before start; server address / session from Live Meeting).

Concrete replacements:
- `windows.intro`: `"Windows 회의실 PC는 번들된 네이티브 WASAPI sidecar가 기본 출력 장치의 소리를 직접 캡처해 yeson-meet 서버로 보냅니다. Voicemeeter 등 가상 오디오 설치는 필요 없습니다."`
- `windows.reminder`: `"캡처는 Windows 기본 출력 장치를 따라갑니다 — 자막을 내보낼 소리가 그 장치로 재생되게 두세요. Device API Key는 저장되지 않으니 sidecar 시작 직전에 다시 붙여넣으세요."`
- `mac.intro`: `"패키지 Mac 앱은 번들된 네이티브 ScreenCaptureKit sidecar로 시스템 소리를 직접 캡처해 yeson-meet 서버로 보냅니다. BlackHole 설치는 필요 없습니다."`
- `mac.reminder`: `"패키지 Mac 앱은 번들된 네이티브 sidecar를 사용하므로 가상 오디오/장치 이름 설정은 필요 없습니다. Device API Key는 저장하지 않으니 sidecar 시작 직전에 다시 붙여넣으세요."`
- For `steps`: remove Voicemeeter/BlackHole-specific entries; keep/condense to native-relevant steps. If a platform ends up with no install steps, keep a single step describing "기본 출력 장치로 회의 소리를 재생" + running the sidecar. Match the existing `RunbookStep` shape (title/detail).

- [ ] **Step 3: `help/helpManualContent.ts` — drop/rewrite virtual-audio sections**

Read the file. Remove or rewrite the Voicemeeter/BlackHole sections (e.g. the `windows-voicemeeter` entry and the body at line ~48 that says "Mac은 BlackHole/Multi-Output Device, Windows는 Voicemeeter..."). Replace with native-first guidance: "회의 소리를 기본 출력 장치로 재생하면 번들된 네이티브 sidecar가 자동으로 캡처합니다. 별도 가상 오디오 설치는 필요 없습니다." Keep the surrounding manual structure/IDs valid (don't leave dangling references to a removed section id elsewhere in the file).

- [ ] **Step 4: Type-check + tests + grep**

Run: `pnpm --filter @yeson-meet/desktop exec tsc --noEmit` → PASS.
Run: `pnpm --filter @yeson-meet/desktop test` → PASS.
Run: `grep -rni "voicemeeter\|blackhole" apps/desktop/src` → expect NO matches (or only an intentional "no longer required" mention; if any remain, confirm they're deliberate native-first copy, not stale instructions).

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/setup apps/desktop/src/help
git commit -m "refactor(desktop): native-first setup copy; drop Voicemeeter/BlackHole guidance

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Docs — demote virtual audio to removed legacy

**Files:**
- Modify: `docs/ROADMAP.md`, `docs/PRD.md`

- [ ] **Step 1: ROADMAP**

Add a native-track note near the other `(2026-06-xx)` Phase notes at the top recording the cutover. Example line to add after the slice-3 line:

```markdown
> - **(2026-06-15) Phase 4-A native-only 컷오버 완료(코드)**: sounddevice(BlackHole/Voicemeeter) 캡처 경로를 사이드카·Rust·데스크톱 setup UX·문서에서 제거. native(SCK/WASAPI)가 유일 캡처 경로. 제거: `sounddevice_source.py`/`capture.py`/`resample.py`/`device.py` + `sounddevice`·`samplerate` 의존성(numpy는 레벨미터·RMS가 써서 유지), `audioDeviceName` 엔드투엔드(데스크톱 모델·생성명령·검증·Rust `SidecarStartRequest`·`YESON_AUDIO_DEVICE_NAME` env). factory는 native 단독(헬퍼 없으면 FileNotFoundError, 제거된 provider값엔 경고). 검증: 사이드카 pytest + 데스크톱 vitest/tsc + cargo check 클린. 가상오디오 자료는 git 히스토리 보존. 후속: B/C 코드서명(인증서 선행), Rust voicemeeter 진단 바이너리 제거(저우선).
```

For the existing notes at the top (lines ~8, 10) and §S2/β-1 Voicemeeter items (lines ~126–127, ~268): append a short "(2026-06-15 native-only 컷오버로 제거됨, 히스토리 보존)" marker rather than deleting the historical text, so the roadmap's history stays readable.

- [ ] **Step 2: PRD §5.2**

In `docs/PRD.md`, update the §5.2 capture-path description (lines ~176–180, ~239, ~360) so native (SCK/WASAPI) is the sole path and Windows+Voicemeeter / Mac+BlackHole are marked as removed legacy (history preserved), consistent with the ROADMAP note. Don't delete the historical decision-log lines; annotate them.

- [ ] **Step 3: Commit**

```bash
git add docs/ROADMAP.md docs/PRD.md
git commit -m "docs(native-audio): demote Voicemeeter/BlackHole to removed legacy (Phase 4-A cutover)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Full verification + handoff

- [ ] **Step 1: Run everything**

Run: `uv run pytest apps/client_sidecar/tests -q` → PASS.
Run: `pnpm --filter @yeson-meet/desktop test` → PASS.
Run: `pnpm --filter @yeson-meet/desktop exec tsc --noEmit` → clean.
Run: `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml` → `Finished`.

- [ ] **Step 2: Final cutover grep (must be clean)**

Run:
```bash
grep -rni "sounddevice\|samplerate\|audioDeviceName\|YESON_AUDIO_DEVICE_NAME\|voicemeeter\|blackhole" \
  apps/client_sidecar apps/desktop/src apps/desktop/src-tauri/src
```
Expected: only intentional "removed / no longer required" mentions in copy, plus the Rust `voicemeeter_ffi.rs`/`voicemeeter_dump.rs` dev bins (explicitly out of scope — Spec §8). No live wiring, no install instructions, no deps.

- [ ] **Step 3: Confirm working tree**

Run: `git status --short`
Expected: only the two pre-existing leftovers (`PROJECT_CONTEXT.md`, `apps/desktop/scripts/vm_dump.py`).

- [ ] **Step 4: Report**

Report code complete + all checks green. Note: native capture E2E already verified pre-cutover (Phase 1/2); the cutover removes the unused path, so the main runtime re-check is just that a fresh meeting still captures (covered by the next Windows CI build the operator runs). Update memory `[[project_native_only_next_steps]]` + `[[project_win_wasapi_helper_status]]`: Phase 4-A done; next = B/C signing (cert-gated).

---

## Self-Review

**Spec coverage:**
- §3 delete subtree + deps → Task 2. ✓
- §4.1 factory native-only → Task 1 (Step 3). ✓
- §4.2 config drop knobs (incl. YESON_AUDIO_PROVIDER constant removed, factory reads env directly) → Task 1 (Step 3 factory reads `os.environ`; Step 4 config removal). ✓
- §4.3 update affected tests → Task 1 (factory test rewrite), Task 2 (Step 2 fixes config/smoke tests). ✓
- §5 desktop UX: model removal → Task 4; copy → Task 5. ✓ Plus the Rust `SidecarStartRequest`/env (discovered: `audioDeviceName` threads into Rust) → Task 3. ✓
- §6 docs → Task 6. ✓
- §7 testing → Tasks 1,2,4,5,7. ✓
- §8 out of scope (voicemeeter Rust bins, signing) → respected; Task 7 grep explicitly tolerates the Rust bins. ✓

**Placeholder scan:** No TBD/TODO. Prose-rewrite tasks (5,6) give concrete replacement strings + exact target lines; the judgment is bounded (native-first copy), not open-ended. ✓

**Type/identifier consistency:** `make_source()` native-only signature unchanged (no args) — callers (`main.py audio_main`) unaffected. `NATIVE_HELPER_BIN_PATH` kept in config, imported by factory. Rust field `audio_device_name` removed in all 4 sites (struct + 2 env + validation). Desktop `audioDeviceName` removed across types/values/config/UI/commands/runner/panel + Rust request — grep gate in Task 4 Step 8 and Task 7 Step 2 enforce zero stragglers. ✓

**Ordering note:** Task 3 (Rust) precedes Task 4 (desktop) so no intermediate state sends a field the Rust struct rejects (serde ignores unknown extra fields, so Rust-first is safe; desktop-first would break on a missing required field).
