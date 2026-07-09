# Desktop Auto-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give both Tauri desktop apps — the client (`apps/desktop`) and the server console (`apps/server_desktop`) — Sparkle/cmux-style silent auto-update: after the first install, the app checks GitHub Releases in the background, downloads the new version, and shows a "restart to apply" banner. No more manual reinstall.

**Architecture:** Use the official `tauri-plugin-updater` (Rust) + `@tauri-apps/plugin-updater` (JS) reading a per-app signed JSON manifest attached to the repo's public GitHub Releases, plus `tauri-plugin-process` for relaunch-after-install. Updater signing uses a dedicated ed25519 keypair (unrelated to Apple/Windows code signing, which stays out of scope). Each installer CI workflow gains: signing env on the build, upload of the updater artifact (`.app.tar.gz`/NSIS `-setup.exe` + `.sig`), and a jq-based manifest-merge step that folds its own platform entry into the release's manifest. A shared, unit-tested pure state machine drives a small banner near each app's existing version display; every updater failure is logged only and never blocks the app.

**Tech Stack:** Tauri v2, Rust, React 18 + TypeScript, Vitest, GitHub Actions (`softprops/action-gh-release@v2`, `gh` CLI, `jq`), pnpm workspaces.

## Global Constraints

- **Repo (public):** `yesonsys03-web/yeson_meet`. Anonymous downloads work; no auth in the manifest endpoints.
- **Release channel:** GitHub Releases. Drop `prerelease: true` in all 4 workflows — `releases/latest/download/…` only resolves **full** releases.
- **Manifest endpoints (verbatim):**
  - Client: `https://github.com/yesonsys03-web/yeson_meet/releases/latest/download/latest-client.json`
  - Server: `https://github.com/yesonsys03-web/yeson_meet/releases/latest/download/latest-server.json`
- **One updater keypair signs BOTH apps.** Public key → both `tauri.conf.json` `plugins.updater.pubkey`. Private key + password → GitHub secrets `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.
- **Updater signing ≠ code signing.** Both apps remain unsigned/ad-hoc (existing parked decision). The ed25519 signature is mandatory and separate.
- **`bundle.createUpdaterArtifacts: true`** in every bundle config. CRITICAL: `windows-desktop.yml` does `$baseConfig.bundle = $windowsConfig.bundle` (full replace), so this flag MUST also live in `apps/desktop/src-tauri/tauri.windows.conf.json`, not only the base config.
- **Version single source:** `apps/<app>/src-tauri/tauri.conf.json` `.version` (all 4 workflows derive `v${VERSION}` from it via `jq`). Both apps are currently `1.1.3`.
- **Platform keys in the manifest:** `darwin-aarch64` (both macOS CI runners are Apple Silicon) and `windows-x86_64`. `darwin-x86_64` (Intel) is **out of scope** — the Intel dmg is a local, unsigned `hdiutil makehybrid` build with no CI/signing/manifest path; Intel users reinstall manually.
- **Never block the app:** every check/download/install error is caught, logged (`console.warn`), and retried next cycle. Background check runs on startup and every 4 hours.
- **UX copy (verbatim):** ready banner reads `vX.Y.Z 준비됨 — 재시작하여 적용`; manual button reads `지금 업데이트 확인`; client-only macOS note reads `Mac은 업데이트 후 화면기록 권한 재확인이 필요할 수 있습니다.`
- **Anchor discipline (repo rule):** edit only within existing `// === ANCHOR: NAME_* ===` regions; new files get their own anchor pair matching the file's convention.
- **Commit trailer:** every commit message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- **Branch:** all work lands on the already-checked-out `web_auto_update` branch. Do not switch branches.

---

### Task 1: Updater signing key + app config (both apps)

One-time operator key generation, GitHub secrets, and wiring `pubkey` / `endpoints` / `createUpdaterArtifacts` into all bundle configs. No app code yet — this is pure configuration and is verifiable with `jq`.

**Files:**
- Modify: `apps/desktop/src-tauri/tauri.conf.json:28-38` (bundle) and add a new top-level `plugins` block after bundle
- Modify: `apps/desktop/src-tauri/tauri.windows.conf.json:2-7` (bundle — add `createUpdaterArtifacts`)
- Modify: `apps/server_desktop/src-tauri/tauri.conf.json:28-39` (bundle) and add a new top-level `plugins` block after bundle

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `plugins.updater.pubkey` + `plugins.updater.endpoints` and `bundle.createUpdaterArtifacts: true` present in both apps' configs; GitHub secrets `TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` set on the repo. Tasks 2 and 7 rely on these.

- [ ] **Step 1: Generate the updater keypair (operator, once)**

Run from the repo root. `-w` writes the key files; choose a strong password when prompted (it becomes the `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` secret).

```bash
pnpm --filter @yeson-meet/desktop exec tauri signer generate -w ~/.tauri/yeson-meet-updater.key
```

Expected output ends with a block containing:
```
Your keypair was generated successfully
Private: ~/.tauri/yeson-meet-updater.key (Keep it secret!)
Public: ~/.tauri/yeson-meet-updater.key.pub
...
Public key:
dW50cnVzdGVkIGNvbW1lbnQ6...    <-- this base64 string is the pubkey
```

- [ ] **Step 2: Back up the private key (operator)**

**Losing this key breaks the update chain permanently** (a new key can't sign updates the installed base already trusts). Store both files in the team password manager / keychain now:

```bash
cat ~/.tauri/yeson-meet-updater.key       # private key contents — copy into the secrets vault
cat ~/.tauri/yeson-meet-updater.key.pub   # public key contents — used in Step 4
```

- [ ] **Step 3: Set the GitHub Actions secrets (operator)**

```bash
gh secret set TAURI_SIGNING_PRIVATE_KEY --repo yesonsys03-web/yeson_meet < ~/.tauri/yeson-meet-updater.key
gh secret set TAURI_SIGNING_PRIVATE_KEY_PASSWORD --repo yesonsys03-web/yeson_meet
# (paste the password you chose in Step 1 when prompted)
```

Verify:
```bash
gh secret list --repo yesonsys03-web/yeson_meet
```
Expected: both `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` listed.

- [ ] **Step 4: Wire the client base config**

Replace the `bundle` block in `apps/desktop/src-tauri/tauri.conf.json` and append a `plugins` block. Paste the **public key from Step 1** in place of `PASTE_YOUR_TAURI_PUBLIC_KEY_HERE` (the full single-line base64 string, no line breaks).

Current (lines 28-38):
```json
  "bundle": {
    "active": true,
    "targets": ["app", "dmg", "nsis", "msi"],
    "icon": ["icons/icon.icns", "icons/icon.ico", "icons/icon.png"],
    "category": "Business",
    "shortDescription": "yeson-meet meeting room client",
    "longDescription": "Desktop client for starting yeson-meet meeting room caption sidecars.",
    "macOS": {
      "minimumSystemVersion": "14.2"
    }
  }
```

New (note the added `createUpdaterArtifacts` line and the new `plugins` sibling of `bundle`):
```json
  "bundle": {
    "active": true,
    "targets": ["app", "dmg", "nsis", "msi"],
    "createUpdaterArtifacts": true,
    "icon": ["icons/icon.icns", "icons/icon.ico", "icons/icon.png"],
    "category": "Business",
    "shortDescription": "yeson-meet meeting room client",
    "longDescription": "Desktop client for starting yeson-meet meeting room caption sidecars.",
    "macOS": {
      "minimumSystemVersion": "14.2"
    }
  },
  "plugins": {
    "updater": {
      "pubkey": "PASTE_YOUR_TAURI_PUBLIC_KEY_HERE",
      "endpoints": [
        "https://github.com/yesonsys03-web/yeson_meet/releases/latest/download/latest-client.json"
      ]
    }
  }
```

- [ ] **Step 5: Wire the client Windows override config**

`apps/desktop/src-tauri/tauri.windows.conf.json` is applied by `windows-desktop.yml` as a full `bundle` replacement, so `createUpdaterArtifacts` must be repeated here.

Current:
```json
{
  "bundle": {
    "active": true,
    "targets": ["nsis", "msi"],
    "icon": ["icons/icon.ico", "icons/icon.png"],
    "externalBin": ["binaries/yeson-win-audio-helper", "binaries/yeson-sidecar"]
  }
}
```

New:
```json
{
  "bundle": {
    "active": true,
    "targets": ["nsis", "msi"],
    "createUpdaterArtifacts": true,
    "icon": ["icons/icon.ico", "icons/icon.png"],
    "externalBin": ["binaries/yeson-win-audio-helper", "binaries/yeson-sidecar"]
  }
}
```

(No change needed in `tauri.macos.conf.json`: Tauri deep-merges it, so the base `bundle.createUpdaterArtifacts` survives.)

- [ ] **Step 6: Wire the server console config**

Replace the `bundle` block in `apps/server_desktop/src-tauri/tauri.conf.json` and append a `plugins` block with the SAME pubkey and the `latest-server.json` endpoint.

Current (lines 28-39):
```json
  "bundle": {
    "active": true,
    "targets": ["app", "dmg", "nsis", "msi"],
    "icon": ["icons/icon.icns", "icons/icon.ico", "icons/icon.png"],
    "category": "Business",
    "shortDescription": "yeson-meet server operator console",
    "longDescription": "Desktop console that runs and supervises the packaged yeson-meet server.",
    "resources": ["binaries/yeson-server-*/**/*", "binaries/cloudflared-*/**/*", "binaries/ffmpeg-*/**/*"],
    "macOS": {
      "minimumSystemVersion": "14.2"
    }
  }
```

New:
```json
  "bundle": {
    "active": true,
    "targets": ["app", "dmg", "nsis", "msi"],
    "createUpdaterArtifacts": true,
    "icon": ["icons/icon.icns", "icons/icon.ico", "icons/icon.png"],
    "category": "Business",
    "shortDescription": "yeson-meet server operator console",
    "longDescription": "Desktop console that runs and supervises the packaged yeson-meet server.",
    "resources": ["binaries/yeson-server-*/**/*", "binaries/cloudflared-*/**/*", "binaries/ffmpeg-*/**/*"],
    "macOS": {
      "minimumSystemVersion": "14.2"
    }
  },
  "plugins": {
    "updater": {
      "pubkey": "PASTE_YOUR_TAURI_PUBLIC_KEY_HERE",
      "endpoints": [
        "https://github.com/yesonsys03-web/yeson_meet/releases/latest/download/latest-server.json"
      ]
    }
  }
```

- [ ] **Step 7: Verify all three configs parse and carry the new keys**

```bash
for f in apps/desktop/src-tauri/tauri.conf.json apps/server_desktop/src-tauri/tauri.conf.json; do
  echo "== $f"; jq '{createUpdaterArtifacts: .bundle.createUpdaterArtifacts, endpoints: .plugins.updater.endpoints, pubkeySet: (.plugins.updater.pubkey | length > 40)}' "$f"
done
jq '.bundle.createUpdaterArtifacts' apps/desktop/src-tauri/tauri.windows.conf.json
```
Expected: `createUpdaterArtifacts: true` everywhere, the correct `latest-client.json`/`latest-server.json` endpoint per app, and `pubkeySet: true`.

- [ ] **Step 8: Commit**

```bash
git add apps/desktop/src-tauri/tauri.conf.json apps/desktop/src-tauri/tauri.windows.conf.json apps/server_desktop/src-tauri/tauri.conf.json
git commit -m "$(cat <<'EOF'
feat(update): configure tauri updater endpoints + signing artifacts for both apps

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Updater/process plugin dependencies, registration, and capabilities (both apps)

Add the Rust crates, JS packages, builder registration, and capability permissions so `check()` / `download()` / `install()` / `relaunch()` are callable.

**Files:**
- Modify: `apps/desktop/src-tauri/Cargo.toml:16-31` (add two deps)
- Modify: `apps/desktop/src-tauri/src/lib.rs:16-24` (register two plugins in the builder chain)
- Modify: `apps/desktop/src-tauri/capabilities/default.json:6-16` (add two permissions)
- Modify: `apps/desktop/package.json:17-25` (add two JS deps)
- Modify: `apps/server_desktop/src-tauri/Cargo.toml:16-46` (add two deps)
- Modify: `apps/server_desktop/src-tauri/src/lib.rs:17-32` (register two plugins)
- Modify: `apps/server_desktop/src-tauri/capabilities/default.json:6-8` (add two permissions)
- Modify: `apps/server_desktop/package.json:13-18` (add two JS deps)

**Interfaces:**
- Consumes: Task 1's config (`plugins.updater.*`).
- Produces: JS modules `@tauri-apps/plugin-updater` (`check`, `Update`) and `@tauri-apps/plugin-process` (`relaunch`) resolvable in both apps; Rust plugins registered; capabilities allow `updater:default` + `process:allow-restart`. Tasks 3-6 import these.

- [ ] **Step 1: Add client Rust deps**

In `apps/desktop/src-tauri/Cargo.toml`, add after the `tauri-plugin-opener = "2"` line (line 19):

```toml
tauri-plugin-updater = "2"
tauri-plugin-process = "2"
```

- [ ] **Step 2: Register the client plugins**

In `apps/desktop/src-tauri/src/lib.rs`, the builder chain currently starts:

```rust
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
```

Add the two updater plugins immediately after the opener plugin:

```rust
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
```

- [ ] **Step 3: Grant client capabilities**

In `apps/desktop/src-tauri/capabilities/default.json`, the `permissions` array ends with two `fs:` objects. Add `"updater:default"` and `"process:allow-restart"` as the first two entries so the block reads:

```json
  "permissions": [
    "core:default",
    "updater:default",
    "process:allow-restart",
    "core:webview:allow-create-webview-window",
    "core:window:allow-close",
    "core:window:allow-set-focus",
    "dialog:allow-open",
    "dialog:allow-save",
    "opener:allow-open-path",
    { "identifier": "fs:allow-write-file", "allow": [{ "path": "$HOME/**" }] },
    { "identifier": "fs:allow-create", "allow": [{ "path": "$HOME/**" }] }
  ]
```

- [ ] **Step 4: Add client JS deps**

In `apps/desktop/package.json`, add to `dependencies` (alphabetical, after `@tauri-apps/plugin-opener`):

```json
    "@tauri-apps/plugin-opener": "^2.5.4",
    "@tauri-apps/plugin-process": "^2.2.0",
    "@tauri-apps/plugin-updater": "^2.7.0",
```

- [ ] **Step 5: Add server Rust deps**

In `apps/server_desktop/src-tauri/Cargo.toml`, add after the `serde_json = "1"` line (line 19):

```toml
tauri-plugin-updater = "2"
tauri-plugin-process = "2"
```

- [ ] **Step 6: Register the server plugins**

In `apps/server_desktop/src-tauri/src/lib.rs`, the builder is:

```rust
    let app = tauri::Builder::default()
        .setup(|_app| {
            orphan_reaper::reap_orphans(|line| eprintln!("[orphan-reaper] {line}"));
            Ok(())
        })
        .manage(server_process::ServerProcessState::default())
```

Add the two plugins ahead of the `.setup(` call:

```rust
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .setup(|_app| {
            orphan_reaper::reap_orphans(|line| eprintln!("[orphan-reaper] {line}"));
            Ok(())
        })
        .manage(server_process::ServerProcessState::default())
```

- [ ] **Step 7: Grant server capabilities**

In `apps/server_desktop/src-tauri/capabilities/default.json`, change:

```json
  "permissions": [
    "core:default"
  ]
```

to:

```json
  "permissions": [
    "core:default",
    "updater:default",
    "process:allow-restart"
  ]
```

- [ ] **Step 8: Add server JS deps**

In `apps/server_desktop/package.json`, add to `dependencies` (after `@tauri-apps/api`):

```json
    "@tauri-apps/api": "^2.1.1",
    "@tauri-apps/plugin-process": "^2.2.0",
    "@tauri-apps/plugin-updater": "^2.7.0",
```

- [ ] **Step 9: Install JS deps + type-check both apps**

```bash
pnpm install
pnpm --filter @yeson-meet/desktop exec tsc --noEmit
pnpm --filter @yeson-meet/server-console exec tsc --noEmit
```
Expected: `pnpm install` resolves the two new packages for each app; both `tsc --noEmit` runs exit 0 (the imports resolve even though nothing consumes them yet).

- [ ] **Step 10: Compile-check the Rust side (capabilities validated by tauri-build)**

```bash
cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml
cargo check --manifest-path apps/server_desktop/src-tauri/Cargo.toml
```
Expected: both compile. `tauri-build` validates `capabilities/default.json` against the newly added plugins' permission schemas; an invalid permission identifier would fail here.

- [ ] **Step 11: Commit**

```bash
git add apps/desktop/src-tauri/Cargo.toml apps/desktop/src-tauri/src/lib.rs apps/desktop/src-tauri/capabilities/default.json apps/desktop/package.json apps/server_desktop/src-tauri/Cargo.toml apps/server_desktop/src-tauri/src/lib.rs apps/server_desktop/src-tauri/capabilities/default.json apps/server_desktop/package.json pnpm-lock.yaml Cargo.lock
git commit -m "$(cat <<'EOF'
feat(update): add updater + process plugins, registration, and capabilities

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Client update state machine + tests (pure logic)

A Tauri-free reducer that drives the banner, plus a pure `isMacOS` helper for the macOS-only note. TDD.

**Files:**
- Create: `apps/desktop/src/updater/autoUpdate.ts`
- Test: `apps/desktop/src/updater/autoUpdate.test.ts`

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `type UpdateStatus`, `type UpdateAction`, `const initialUpdateStatus: UpdateStatus`, `function updateReducer(state, action): UpdateStatus`, `function isMacOS(platform: string): boolean`. Task 4's hook and banner import these.

- [ ] **Step 1: Write the failing test**

Create `apps/desktop/src/updater/autoUpdate.test.ts`:

```ts
// === ANCHOR: AUTO_UPDATE_TEST_START ===
import { describe, expect, it } from "vitest";

import { initialUpdateStatus, isMacOS, updateReducer, type UpdateStatus } from "./autoUpdate";

describe("updateReducer", () => {
  it("moves idle → checking on check-start", () => {
    expect(updateReducer(initialUpdateStatus, { type: "check-start" })).toEqual({ kind: "checking" });
  });

  it("keeps a ready banner across a later background check", () => {
    const ready: UpdateStatus = { kind: "ready", version: "1.2.0" };
    expect(updateReducer(ready, { type: "check-start" })).toEqual(ready);
    expect(updateReducer(ready, { type: "up-to-date" })).toEqual(ready);
  });

  it("tracks download progress", () => {
    const started = updateReducer({ kind: "checking" }, { type: "download-start", version: "1.2.0" });
    expect(started).toEqual({ kind: "downloading", version: "1.2.0", percent: null });
    expect(updateReducer(started, { type: "download-progress", percent: 42 })).toEqual({
      kind: "downloading",
      version: "1.2.0",
      percent: 42,
    });
  });

  it("ignores progress when not downloading", () => {
    expect(updateReducer({ kind: "idle" }, { type: "download-progress", percent: 10 })).toEqual({ kind: "idle" });
  });

  it("reaches ready on download-done", () => {
    const downloading: UpdateStatus = { kind: "downloading", version: "1.2.0", percent: 100 };
    expect(updateReducer(downloading, { type: "download-done", version: "1.2.0" })).toEqual({
      kind: "ready",
      version: "1.2.0",
    });
  });

  it("shows error but never over a ready banner", () => {
    expect(updateReducer({ kind: "checking" }, { type: "fail", message: "network" })).toEqual({
      kind: "error",
      message: "network",
    });
    const ready: UpdateStatus = { kind: "ready", version: "1.2.0" };
    expect(updateReducer(ready, { type: "fail", message: "network" })).toEqual(ready);
  });
});

describe("isMacOS", () => {
  it("detects mac platforms", () => {
    expect(isMacOS("MacIntel")).toBe(true);
    expect(isMacOS("Win32")).toBe(false);
    expect(isMacOS("Linux x86_64")).toBe(false);
  });
});
// === ANCHOR: AUTO_UPDATE_TEST_END ===
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --filter @yeson-meet/desktop exec vitest run src/updater/autoUpdate.test.ts`
Expected: FAIL — `Failed to resolve import "./autoUpdate"` (file doesn't exist yet).

- [ ] **Step 3: Write the module**

Create `apps/desktop/src/updater/autoUpdate.ts`:

```ts
// === ANCHOR: AUTO_UPDATE_START ===
// Pure state machine for the background auto-updater banner. Deliberately free of
// any Tauri imports so it runs (and is unit-tested) in plain vitest; the hook in
// useAutoUpdate.ts is the only place that touches the @tauri-apps plugins.

export type UpdateStatus =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "downloading"; version: string; percent: number | null }
  | { kind: "ready"; version: string }
  | { kind: "error"; message: string };

export type UpdateAction =
  | { type: "check-start" }
  | { type: "up-to-date" }
  | { type: "download-start"; version: string }
  | { type: "download-progress"; percent: number }
  | { type: "download-done"; version: string }
  | { type: "fail"; message: string };

export const initialUpdateStatus: UpdateStatus = { kind: "idle" };

export function updateReducer(state: UpdateStatus, action: UpdateAction): UpdateStatus {
  switch (action.type) {
    case "check-start":
      // A staged ("ready") or in-flight download must survive the next 4h poll.
      if (state.kind === "ready" || state.kind === "downloading") return state;
      return { kind: "checking" };
    case "up-to-date":
      // A background poll that finds nothing must not erase a ready banner.
      if (state.kind === "ready" || state.kind === "downloading") return state;
      return { kind: "idle" };
    case "download-start":
      return { kind: "downloading", version: action.version, percent: null };
    case "download-progress":
      if (state.kind !== "downloading") return state;
      return { kind: "downloading", version: state.version, percent: action.percent };
    case "download-done":
      return { kind: "ready", version: action.version };
    case "fail":
      // Never bury a usable "ready" banner under a later transient failure.
      if (state.kind === "ready") return state;
      return { kind: "error", message: action.message };
    default:
      return state;
  }
}

// navigator.platform / userAgent contain "Mac" inside the Tauri webview on macOS.
// Pure helper so the mac-only permission note stays unit-testable.
export function isMacOS(platform: string): boolean {
  return /mac/i.test(platform);
}
// === ANCHOR: AUTO_UPDATE_END ===
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm --filter @yeson-meet/desktop exec vitest run src/updater/autoUpdate.test.ts`
Expected: PASS — 8 assertions across 7 `it` blocks green.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/updater/autoUpdate.ts apps/desktop/src/updater/autoUpdate.test.ts
git commit -m "$(cat <<'EOF'
feat(update): client update-banner state machine + tests

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Client update hook, banner, and wiring

Wire the Tauri updater into a hook, render a banner beneath the version line in the sidebar, and mount the hook at the console root.

**Files:**
- Create: `apps/desktop/src/updater/useAutoUpdate.ts`
- Create: `apps/desktop/src/console/UpdateBanner.tsx`
- Modify: `apps/desktop/src/console/consoleStyles.ts:4-408` (add banner styles inside the `CONSOLE_STYLES` anchor)
- Modify: `apps/desktop/src/console/ConsoleNav.tsx:1-44` (accept + render an `updateBanner` node)
- Modify: `apps/desktop/src/console/DesktopConsole.tsx:1-96` (call the hook, pass the banner)

**Interfaces:**
- Consumes: `updateReducer`, `initialUpdateStatus`, `isMacOS`, `UpdateStatus` from Task 3; `check`/`Update` from `@tauri-apps/plugin-updater` and `relaunch` from `@tauri-apps/plugin-process` (Task 2).
- Produces: `function useAutoUpdate(): { status: UpdateStatus; checkNow: () => void; applyNow: () => void }` and `<UpdateBanner status onCheckNow onApplyNow />`. Nothing later consumes these (leaf feature).

- [ ] **Step 1: Write the hook**

Create `apps/desktop/src/updater/useAutoUpdate.ts`:

```ts
// === ANCHOR: USE_AUTO_UPDATE_START ===
import { useCallback, useEffect, useReducer, useRef } from "react";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";

import { initialUpdateStatus, updateReducer, type UpdateStatus } from "./autoUpdate";

// Background check on startup + every 4h. Download is silent; the banner only
// asks the user to restart once an update is staged. Every failure is swallowed
// (log only) so the updater can never block the app.
const CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;

function hasTauriRuntime(): boolean {
  return (
    typeof window !== "undefined" &&
    Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
  );
}

export type UseAutoUpdate = {
  status: UpdateStatus;
  checkNow: () => void;
  applyNow: () => void;
};

export function useAutoUpdate(): UseAutoUpdate {
  const [status, dispatch] = useReducer(updateReducer, initialUpdateStatus);
  // The staged Update handle: download() populates it, install() consumes it.
  const pending = useRef<Update | null>(null);
  const busy = useRef(false);

  const runCheck = useCallback(async () => {
    if (!hasTauriRuntime() || busy.current) return;
    busy.current = true;
    dispatch({ type: "check-start" });
    try {
      const update = await check();
      if (!update) {
        dispatch({ type: "up-to-date" });
        return;
      }
      pending.current = update;
      dispatch({ type: "download-start", version: update.version });
      let downloaded = 0;
      let total = 0;
      await update.download((event) => {
        if (event.event === "Started") {
          total = event.data.contentLength ?? 0;
        } else if (event.event === "Progress") {
          downloaded += event.data.chunkLength;
          if (total > 0) {
            dispatch({ type: "download-progress", percent: Math.round((downloaded / total) * 100) });
          }
        }
      });
      dispatch({ type: "download-done", version: update.version });
    } catch (err) {
      // Network down, signature mismatch, or a 404 before the first updater-
      // enabled release ships — all non-fatal. Log and retry next cycle.
      console.warn("[auto-update] check/download failed:", err);
      dispatch({ type: "fail", message: err instanceof Error ? err.message : String(err) });
    } finally {
      busy.current = false;
    }
  }, []);

  const applyNow = useCallback(() => {
    void (async () => {
      const update = pending.current;
      if (!update) return;
      try {
        await update.install();
        await relaunch();
      } catch (err) {
        console.warn("[auto-update] install/relaunch failed:", err);
        dispatch({ type: "fail", message: err instanceof Error ? err.message : String(err) });
      }
    })();
  }, []);

  const checkNow = useCallback(() => {
    void runCheck();
  }, [runCheck]);

  useEffect(() => {
    void runCheck();
    const id = window.setInterval(() => void runCheck(), CHECK_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [runCheck]);

  return { status, checkNow, applyNow };
}
// === ANCHOR: USE_AUTO_UPDATE_END ===
```

- [ ] **Step 2: Add banner styles**

In `apps/desktop/src/console/consoleStyles.ts`, add these keys inside the `consoleStyles` object (place them right after the `version:` block, before `nav:`), staying within the `CONSOLE_STYLES` anchor:

```ts
  updateBox: {
    margin: "0 0 16px",
    display: "grid",
    gap: 8,
  },
  updateReady: {
    margin: 0,
    fontSize: 12,
    fontWeight: 800,
    color: "var(--ys-success-text)",
    lineHeight: 1.4,
  },
  updateHint: {
    margin: 0,
    fontSize: 11,
    color: "var(--ys-text-faint)",
    lineHeight: 1.4,
  },
  updateNote: {
    margin: 0,
    fontSize: 11,
    color: "var(--ys-warning-text)",
    lineHeight: 1.4,
  },
  updateApply: {
    padding: "8px 10px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-accent-strong)",
    background: "var(--ys-accent-strong)",
    color: "var(--ys-on-accent)",
    fontSize: 12,
    fontWeight: 900,
    cursor: "pointer",
  },
  updateCheck: {
    padding: "6px 10px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-border-subtle)",
    background: "transparent",
    color: "var(--ys-text-label)",
    fontSize: 11,
    fontWeight: 700,
    cursor: "pointer",
  },
```

- [ ] **Step 3: Write the banner component**

Create `apps/desktop/src/console/UpdateBanner.tsx`:

```tsx
// === ANCHOR: UPDATE_BANNER_START ===
import { consoleStyles } from "./consoleStyles";
import { isMacOS, type UpdateStatus } from "../updater/autoUpdate";

type UpdateBannerProps = {
  status: UpdateStatus;
  onCheckNow: () => void;
  onApplyNow: () => void;
};

// macOS re-checks the screen-recording (TCC) grant after the binary's cdhash
// changes (unsigned app). Warn the operator so a post-update prompt isn't a
// surprise — same root cause as the existing "ghost permission" banner.
const MAC_PERMISSION_NOTE = "Mac은 업데이트 후 화면기록 권한 재확인이 필요할 수 있습니다.";

export function UpdateBanner({ status, onCheckNow, onApplyNow }: UpdateBannerProps) {
  const onMac = typeof navigator !== "undefined" && isMacOS(navigator.platform);
  const checking = status.kind === "checking" || status.kind === "downloading";
  return (
    <div style={consoleStyles.updateBox}>
      {status.kind === "ready" ? (
        <>
          <p style={consoleStyles.updateReady}>v{status.version} 준비됨 — 재시작하여 적용</p>
          <button type="button" style={consoleStyles.updateApply} onClick={onApplyNow}>
            재시작하여 업데이트
          </button>
          {onMac ? <p style={consoleStyles.updateNote}>{MAC_PERMISSION_NOTE}</p> : null}
        </>
      ) : status.kind === "downloading" ? (
        <p style={consoleStyles.updateHint}>
          업데이트 내려받는 중{status.percent != null ? ` (${status.percent}%)` : "…"}
        </p>
      ) : status.kind === "error" ? (
        <p style={consoleStyles.updateHint}>업데이트 확인 실패 — 다음에 다시 시도합니다.</p>
      ) : null}
      <button type="button" style={consoleStyles.updateCheck} onClick={onCheckNow} disabled={checking}>
        {status.kind === "checking" ? "확인 중…" : "지금 업데이트 확인"}
      </button>
    </div>
  );
}
// === ANCHOR: UPDATE_BANNER_END ===
```

- [ ] **Step 4: Let ConsoleNav render the banner**

In `apps/desktop/src/console/ConsoleNav.tsx`, add a `ReactNode` import and an `updateBanner` prop, and render it under the version line.

Change the top imports:
```tsx
// === ANCHOR: CONSOLE_NAV_START ===
import type { ReactNode } from "react";
import { consoleStyles } from "./consoleStyles";
import type { ConsoleView } from "./types";

type ConsoleNavProps = {
  activeView: ConsoleView;
  onChange: (view: ConsoleView) => void;
  appVersion?: string;
  updateBanner?: ReactNode;
};
```

Change the function signature + the version line region:
```tsx
export function ConsoleNav({ activeView, onChange, appVersion, updateBanner }: ConsoleNavProps) {
  return (
    <aside style={consoleStyles.sidebar}>
      <p style={consoleStyles.brand}>yeson-meet operator</p>
      {appVersion ? <p style={consoleStyles.version}>v{appVersion}</p> : null}
      {updateBanner ?? null}
      <nav style={consoleStyles.nav} aria-label="Desktop console sections">
```

(Leave the rest of the component unchanged.)

- [ ] **Step 5: Mount the hook in DesktopConsole**

In `apps/desktop/src/console/DesktopConsole.tsx`, add imports and the hook, and pass the banner into `ConsoleNav`.

Add to the import block (after the `ConsoleNav` import on line 9):
```tsx
import { ConsoleNav } from "./ConsoleNav";
import { UpdateBanner } from "./UpdateBanner";
import { useAutoUpdate } from "../updater/useAutoUpdate";
```

Inside `DesktopConsole`, after the `appVersion` state (line 27), add:
```tsx
  // Background auto-update: silent check/download, restart-to-apply banner.
  const update = useAutoUpdate();
```

Change the `<ConsoleNav ... />` render (line 57) to pass the banner:
```tsx
      <ConsoleNav
        activeView={activeView}
        onChange={setActiveView}
        appVersion={appVersion}
        updateBanner={
          <UpdateBanner status={update.status} onCheckNow={update.checkNow} onApplyNow={update.applyNow} />
        }
      />
```

- [ ] **Step 6: Type-check + run the client tests**

```bash
pnpm --filter @yeson-meet/desktop exec tsc --noEmit
pnpm --filter @yeson-meet/desktop test
```
Expected: `tsc --noEmit` exits 0; vitest run is green (existing suites + Task 3's `autoUpdate.test.ts`).

- [ ] **Step 7: Commit**

```bash
git add apps/desktop/src/updater/useAutoUpdate.ts apps/desktop/src/console/UpdateBanner.tsx apps/desktop/src/console/consoleStyles.ts apps/desktop/src/console/ConsoleNav.tsx apps/desktop/src/console/DesktopConsole.tsx
git commit -m "$(cat <<'EOF'
feat(update): client auto-update hook + restart-to-apply banner

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Server update state machine + tests (pure logic)

Mirror of Task 3 for the server console. The server banner has no macOS screen-recording note, so `isMacOS` is omitted here.

**Files:**
- Create: `apps/server_desktop/src/updater/autoUpdate.ts`
- Test: `apps/server_desktop/src/updater/autoUpdate.test.ts`

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `type UpdateStatus`, `type UpdateAction`, `const initialUpdateStatus`, `function updateReducer(state, action)`. Task 6's hook and banner import these.

- [ ] **Step 1: Write the failing test**

Create `apps/server_desktop/src/updater/autoUpdate.test.ts`:

```ts
// === ANCHOR: AUTO_UPDATE_TEST_START ===
import { describe, expect, it } from "vitest";

import { initialUpdateStatus, updateReducer, type UpdateStatus } from "./autoUpdate";

describe("updateReducer (server)", () => {
  it("moves idle → checking on check-start", () => {
    expect(updateReducer(initialUpdateStatus, { type: "check-start" })).toEqual({ kind: "checking" });
  });

  it("keeps a ready banner across a later background check", () => {
    const ready: UpdateStatus = { kind: "ready", version: "1.2.0" };
    expect(updateReducer(ready, { type: "check-start" })).toEqual(ready);
    expect(updateReducer(ready, { type: "up-to-date" })).toEqual(ready);
  });

  it("tracks download progress", () => {
    const started = updateReducer({ kind: "checking" }, { type: "download-start", version: "1.2.0" });
    expect(started).toEqual({ kind: "downloading", version: "1.2.0", percent: null });
    expect(updateReducer(started, { type: "download-progress", percent: 42 })).toEqual({
      kind: "downloading",
      version: "1.2.0",
      percent: 42,
    });
  });

  it("ignores progress when not downloading", () => {
    expect(updateReducer({ kind: "idle" }, { type: "download-progress", percent: 10 })).toEqual({ kind: "idle" });
  });

  it("reaches ready on download-done", () => {
    const downloading: UpdateStatus = { kind: "downloading", version: "1.2.0", percent: 100 };
    expect(updateReducer(downloading, { type: "download-done", version: "1.2.0" })).toEqual({
      kind: "ready",
      version: "1.2.0",
    });
  });

  it("shows error but never over a ready banner", () => {
    expect(updateReducer({ kind: "checking" }, { type: "fail", message: "network" })).toEqual({
      kind: "error",
      message: "network",
    });
    const ready: UpdateStatus = { kind: "ready", version: "1.2.0" };
    expect(updateReducer(ready, { type: "fail", message: "network" })).toEqual(ready);
  });
});
// === ANCHOR: AUTO_UPDATE_TEST_END ===
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --filter @yeson-meet/server-console exec vitest run src/updater/autoUpdate.test.ts`
Expected: FAIL — `Failed to resolve import "./autoUpdate"`.

- [ ] **Step 3: Write the module**

Create `apps/server_desktop/src/updater/autoUpdate.ts`:

```ts
// === ANCHOR: AUTO_UPDATE_START ===
// Pure state machine for the server console's background auto-updater banner.
// Free of Tauri imports so it runs (and is unit-tested) in plain vitest; the
// hook in useAutoUpdate.ts is the only place that touches the @tauri-apps plugins.

export type UpdateStatus =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "downloading"; version: string; percent: number | null }
  | { kind: "ready"; version: string }
  | { kind: "error"; message: string };

export type UpdateAction =
  | { type: "check-start" }
  | { type: "up-to-date" }
  | { type: "download-start"; version: string }
  | { type: "download-progress"; percent: number }
  | { type: "download-done"; version: string }
  | { type: "fail"; message: string };

export const initialUpdateStatus: UpdateStatus = { kind: "idle" };

export function updateReducer(state: UpdateStatus, action: UpdateAction): UpdateStatus {
  switch (action.type) {
    case "check-start":
      if (state.kind === "ready" || state.kind === "downloading") return state;
      return { kind: "checking" };
    case "up-to-date":
      if (state.kind === "ready" || state.kind === "downloading") return state;
      return { kind: "idle" };
    case "download-start":
      return { kind: "downloading", version: action.version, percent: null };
    case "download-progress":
      if (state.kind !== "downloading") return state;
      return { kind: "downloading", version: state.version, percent: action.percent };
    case "download-done":
      return { kind: "ready", version: action.version };
    case "fail":
      if (state.kind === "ready") return state;
      return { kind: "error", message: action.message };
    default:
      return state;
  }
}
// === ANCHOR: AUTO_UPDATE_END ===
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm --filter @yeson-meet/server-console exec vitest run src/updater/autoUpdate.test.ts`
Expected: PASS — 6 `it` blocks green.

- [ ] **Step 5: Commit**

```bash
git add apps/server_desktop/src/updater/autoUpdate.ts apps/server_desktop/src/updater/autoUpdate.test.ts
git commit -m "$(cat <<'EOF'
feat(update): server console update-banner state machine + tests

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Server update hook, banner, and wiring

Mirror of Task 4 for the server console. The banner mounts in the sidebar under the version span.

**Files:**
- Create: `apps/server_desktop/src/updater/useAutoUpdate.ts`
- Create: `apps/server_desktop/src/UpdateBanner.tsx`
- Modify: `apps/server_desktop/src/ServerConsole.tsx:1-13` (imports), `:118-141` (mount hook), `:393-398` (render banner)

**Interfaces:**
- Consumes: `updateReducer`, `initialUpdateStatus`, `UpdateStatus` (Task 5); `check`/`Update`, `relaunch` (Task 2).
- Produces: `function useAutoUpdate(): { status: UpdateStatus; checkNow: () => void; applyNow: () => void }` and `<UpdateBanner status onCheckNow onApplyNow />`. Leaf feature.

- [ ] **Step 1: Write the hook**

Create `apps/server_desktop/src/updater/useAutoUpdate.ts` (identical logic to the client hook; the server has no screen-recording concern but the flow is the same):

```ts
// === ANCHOR: USE_AUTO_UPDATE_START ===
import { useCallback, useEffect, useReducer, useRef } from "react";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";

import { initialUpdateStatus, updateReducer, type UpdateStatus } from "./autoUpdate";

// Background check on startup + every 4h. Silent download; the banner only asks
// to restart once an update is staged. Failures are swallowed (log only) so the
// updater can never block the console.
const CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;

function hasTauriRuntime(): boolean {
  return (
    typeof window !== "undefined" &&
    Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
  );
}

export type UseAutoUpdate = {
  status: UpdateStatus;
  checkNow: () => void;
  applyNow: () => void;
};

export function useAutoUpdate(): UseAutoUpdate {
  const [status, dispatch] = useReducer(updateReducer, initialUpdateStatus);
  const pending = useRef<Update | null>(null);
  const busy = useRef(false);

  const runCheck = useCallback(async () => {
    if (!hasTauriRuntime() || busy.current) return;
    busy.current = true;
    dispatch({ type: "check-start" });
    try {
      const update = await check();
      if (!update) {
        dispatch({ type: "up-to-date" });
        return;
      }
      pending.current = update;
      dispatch({ type: "download-start", version: update.version });
      let downloaded = 0;
      let total = 0;
      await update.download((event) => {
        if (event.event === "Started") {
          total = event.data.contentLength ?? 0;
        } else if (event.event === "Progress") {
          downloaded += event.data.chunkLength;
          if (total > 0) {
            dispatch({ type: "download-progress", percent: Math.round((downloaded / total) * 100) });
          }
        }
      });
      dispatch({ type: "download-done", version: update.version });
    } catch (err) {
      console.warn("[auto-update] check/download failed:", err);
      dispatch({ type: "fail", message: err instanceof Error ? err.message : String(err) });
    } finally {
      busy.current = false;
    }
  }, []);

  const applyNow = useCallback(() => {
    void (async () => {
      const update = pending.current;
      if (!update) return;
      try {
        await update.install();
        await relaunch();
      } catch (err) {
        console.warn("[auto-update] install/relaunch failed:", err);
        dispatch({ type: "fail", message: err instanceof Error ? err.message : String(err) });
      }
    })();
  }, []);

  const checkNow = useCallback(() => {
    void runCheck();
  }, [runCheck]);

  useEffect(() => {
    void runCheck();
    const id = window.setInterval(() => void runCheck(), CHECK_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [runCheck]);

  return { status, checkNow, applyNow };
}
// === ANCHOR: USE_AUTO_UPDATE_END ===
```

- [ ] **Step 2: Write the banner component**

Create `apps/server_desktop/src/UpdateBanner.tsx` (self-contained styles using the shared `--ys-*` tokens, matching `ServerConsole`'s style approach):

```tsx
// === ANCHOR: UPDATE_BANNER_START ===
import type { CSSProperties } from "react";

import type { UpdateStatus } from "./updater/autoUpdate";

type UpdateBannerProps = {
  status: UpdateStatus;
  onCheckNow: () => void;
  onApplyNow: () => void;
};

export function UpdateBanner({ status, onCheckNow, onApplyNow }: UpdateBannerProps) {
  const checking = status.kind === "checking" || status.kind === "downloading";
  return (
    <div style={styles.box}>
      {status.kind === "ready" ? (
        <>
          <p style={styles.ready}>v{status.version} 준비됨 — 재시작하여 적용</p>
          <button type="button" style={styles.apply} onClick={onApplyNow}>
            재시작하여 업데이트
          </button>
        </>
      ) : status.kind === "downloading" ? (
        <p style={styles.hint}>
          업데이트 내려받는 중{status.percent != null ? ` (${status.percent}%)` : "…"}
        </p>
      ) : status.kind === "error" ? (
        <p style={styles.hint}>업데이트 확인 실패 — 다음에 다시 시도합니다.</p>
      ) : null}
      <button type="button" style={styles.check} onClick={onCheckNow} disabled={checking}>
        {status.kind === "checking" ? "확인 중…" : "지금 업데이트 확인"}
      </button>
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  box: { display: "grid", gap: 8 },
  ready: { margin: 0, fontSize: 12, fontWeight: 800, color: "var(--ys-success-text)", lineHeight: 1.4 },
  hint: { margin: 0, fontSize: 11, color: "var(--ys-text-faint)", lineHeight: 1.4 },
  apply: {
    padding: "8px 10px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-accent-strong)",
    background: "var(--ys-accent-strong)",
    color: "var(--ys-on-accent)",
    fontSize: 12,
    fontWeight: 900,
    cursor: "pointer",
  },
  check: {
    padding: "6px 10px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-border-subtle)",
    background: "transparent",
    color: "var(--ys-text-label)",
    fontSize: 11,
    fontWeight: 700,
    cursor: "pointer",
  },
};
// === ANCHOR: UPDATE_BANNER_END ===
```

- [ ] **Step 3: Import the hook + banner in ServerConsole**

In `apps/server_desktop/src/ServerConsole.tsx`, add after the `import ReportsPanel from "./ReportsPanel";` line (line 12):

```tsx
import ReportsPanel from "./ReportsPanel";
import { UpdateBanner } from "./UpdateBanner";
import { useAutoUpdate } from "./updater/useAutoUpdate";
```

- [ ] **Step 4: Mount the hook**

Inside `ServerConsole`, after the `appVersion` state (line 135: `const [appVersion, setAppVersion] = useState<string>("");`), add:

```tsx
  const [appVersion, setAppVersion] = useState<string>("");
  // Background auto-update: silent check/download, restart-to-apply banner.
  const update = useAutoUpdate();
```

- [ ] **Step 5: Render the banner in the sidebar**

In the sidebar block, the version span is at line 395:

```tsx
        <p style={styles.brand}>yeson server console</p>
        {appVersion ? <span style={styles.version}>v{appVersion}</span> : null}
        <span style={{ ...styles.badge, ...(running ? styles.badgeOn : styles.badgeOff) }}>
```

Insert the banner between the version span and the status badge:

```tsx
        <p style={styles.brand}>yeson server console</p>
        {appVersion ? <span style={styles.version}>v{appVersion}</span> : null}
        <UpdateBanner status={update.status} onCheckNow={update.checkNow} onApplyNow={update.applyNow} />
        <span style={{ ...styles.badge, ...(running ? styles.badgeOn : styles.badgeOff) }}>
```

- [ ] **Step 6: Type-check + run the server tests**

```bash
pnpm --filter @yeson-meet/server-console exec tsc --noEmit
pnpm --filter @yeson-meet/server-console test
```
Expected: `tsc --noEmit` exits 0; vitest run green (existing suites + Task 5's `autoUpdate.test.ts`).

- [ ] **Step 7: Commit**

```bash
git add apps/server_desktop/src/updater/useAutoUpdate.ts apps/server_desktop/src/UpdateBanner.tsx apps/server_desktop/src/ServerConsole.tsx
git commit -m "$(cat <<'EOF'
feat(update): server console auto-update hook + restart-to-apply banner

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: CI — sign, upload updater artifacts, merge manifest, drop prerelease (all 4 workflows)

Add a shared jq-based manifest-merge script, then update each of the 4 installer workflows to: inject signing env into the build, upload the updater artifact + `.sig` alongside the existing installer, merge its platform entry into the per-app manifest, and publish a full (non-pre) release.

**Files:**
- Create: `.github/scripts/merge-update-manifest.sh`
- Modify: `.github/workflows/macos-desktop.yml:65-67, 77-103`
- Modify: `.github/workflows/windows-desktop.yml:78-125, 161-187`
- Modify: `.github/workflows/server-desktop-macos.yml:62-64, 74-100`
- Modify: `.github/workflows/server-desktop-windows.yml:84-87, 102-129`

**Interfaces:**
- Consumes: Task 1's `createUpdaterArtifacts`/endpoints/pubkey and repo secrets. Bundle output paths: macOS updater artifact `…/target/release/bundle/macos/*.app.tar.gz` (+ `.sig`); Windows NSIS updater artifact `…/target/release/bundle/nsis/*-setup.exe` (+ `.sig`).
- Produces: each release (`vX.Y.Z`) carries `latest-client.json` and `latest-server.json` with `{version, pub_date, platforms.{darwin-aarch64|windows-x86_64}.{signature,url}}`, satisfying the endpoints the apps poll.

- [ ] **Step 1: Create the shared merge script**

Create `.github/scripts/merge-update-manifest.sh`:

```bash
#!/usr/bin/env bash
# Merge THIS platform's updater entry into the per-app update manifest attached
# to the GitHub release, then re-upload it. Called by each installer workflow
# AFTER the release is published. Runs on both macOS and Windows runners
# (Windows uses git-bash via `shell: bash`; gh + jq are preinstalled on both).
#
# Required env:
#   VERSION       release version without the leading v, e.g. 1.1.4
#   REPO          owner/repo, e.g. yesonsys03-web/yeson_meet
#   MANIFEST      latest-client.json | latest-server.json
#   PLATFORM_KEY  darwin-aarch64 | windows-x86_64
#   ARTIFACT_GLOB glob to the updater artifact (…/*.app.tar.gz | …/*-setup.exe)
#   GH_TOKEN      token for gh (release download/upload)
set -euo pipefail

# shellcheck disable=SC2086  # ARTIFACT_GLOB must expand as a glob
ARTIFACT=$(ls $ARTIFACT_GLOB 2>/dev/null | head -n1 || true)
if [ -z "$ARTIFACT" ]; then
  echo "ERROR: no updater artifact matched: $ARTIFACT_GLOB" >&2
  exit 1
fi
SIG_FILE="$ARTIFACT.sig"
if [ ! -f "$SIG_FILE" ]; then
  echo "ERROR: missing signature $SIG_FILE — was TAURI_SIGNING_PRIVATE_KEY set on the build step?" >&2
  exit 1
fi
SIG=$(cat "$SIG_FILE")
FILENAME=$(basename "$ARTIFACT")
URL="https://github.com/$REPO/releases/download/v$VERSION/$FILENAME"
PUBDATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Start from the manifest already on the release (the other platform's workflow
# may have run first); fall back to an empty object for the first platform.
if ! gh release download "v$VERSION" --repo "$REPO" --pattern "$MANIFEST" --dir . --clobber 2>/dev/null; then
  echo "{}" > "$MANIFEST"
fi

jq \
  --arg version "$VERSION" \
  --arg pubdate "$PUBDATE" \
  --arg key "$PLATFORM_KEY" \
  --arg sig "$SIG" \
  --arg url "$URL" \
  '. + {
     version: $version,
     pub_date: (.pub_date // $pubdate),
     platforms: ((.platforms // {}) + { ($key): { signature: $sig, url: $url } })
   }' "$MANIFEST" > "$MANIFEST.tmp"
mv "$MANIFEST.tmp" "$MANIFEST"

echo "merged $PLATFORM_KEY into $MANIFEST:"
cat "$MANIFEST"

gh release upload "v$VERSION" "$MANIFEST" --repo "$REPO" --clobber
```

Make it executable:
```bash
chmod +x .github/scripts/merge-update-manifest.sh
```

- [ ] **Step 2: Validate the merge script locally (no network)**

Simulate two platforms merging into one manifest with a stub `gh`/artifact so the jq logic is proven before CI:

```bash
mkdir -p /tmp/upd/bundle && cd /tmp/upd
printf 'FAKE_TARBALL' > bundle/yeson-meet.app.tar.gz
printf 'FAKE_SIGNATURE_AARCH64' > bundle/yeson-meet.app.tar.gz.sig
# stub gh: "download" always fails (nothing on release yet) → script seeds {}
cat > gh <<'STUB'
#!/usr/bin/env bash
if [ "$1" = "release" ] && [ "$2" = "download" ]; then exit 1; fi
if [ "$1" = "release" ] && [ "$2" = "upload" ]; then echo "gh upload: $*"; exit 0; fi
exit 0
STUB
chmod +x gh
PATH="/tmp/upd:$PATH" VERSION=9.9.9 REPO=yesonsys03-web/yeson_meet MANIFEST=latest-client.json \
  PLATFORM_KEY=darwin-aarch64 ARTIFACT_GLOB='/tmp/upd/bundle/*.app.tar.gz' GH_TOKEN=x \
  bash /Users/usabatch/coding/yeson_dev/yeson_meet/.github/scripts/merge-update-manifest.sh
jq -e '.platforms["darwin-aarch64"].signature == "FAKE_SIGNATURE_AARCH64" and .version == "9.9.9"' latest-client.json
cd - && rm -rf /tmp/upd
```
Expected: the script prints the merged JSON and a `gh upload:` line; the final `jq -e` exits 0 (assertion true).

- [ ] **Step 3: Update `macos-desktop.yml` — sign, upload updater artifact, merge, drop prerelease**

Add signing env to the build step (currently lines 65-67):
```yaml
      - name: Build client installer (app + dmg)
        working-directory: apps/desktop
        env:
          TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
        run: pnpm tauri build --bundles app dmg
```

In the `Publish installer to prerelease` step: remove the `prerelease: true` line and add the updater artifact globs to `files:`:
```yaml
      - name: Publish installer to release
        uses: softprops/action-gh-release@v2
        with:
          tag_name: v${{ env.VERSION }}
          name: yeson-meet v${{ env.VERSION }}
          body: |
            ## yeson-meet v1.1.3 — 무엇이 바뀌었나
            (… keep the existing body verbatim …)
          files: |
            apps/desktop/src-tauri/target/release/bundle/dmg/*.dmg
            apps/desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz
            apps/desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz.sig
          fail_on_unmatched_files: true
```

Append a merge step immediately after that publish step:
```yaml
      - name: Merge client entry into update manifest
        shell: bash
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          VERSION: ${{ env.VERSION }}
          REPO: ${{ github.repository }}
          MANIFEST: latest-client.json
          PLATFORM_KEY: darwin-aarch64
          ARTIFACT_GLOB: apps/desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz
        run: bash .github/scripts/merge-update-manifest.sh
```

- [ ] **Step 4: Update `windows-desktop.yml` — sign, upload NSIS updater artifact, merge, drop prerelease**

Add signing env to the `Build Tauri Windows installer` step (the `pwsh` step ending in `pnpm --filter @yeson-meet/desktop tauri:build`):
```yaml
      - name: Build Tauri Windows installer
        shell: pwsh
        env:
          TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
        run: |
          $iconPath = "apps/desktop/src-tauri/icons/icon.ico"
          (… keep the rest of the existing script verbatim, ending with …)
          pnpm --filter @yeson-meet/desktop tauri:build
```

In the `Publish client installer to prerelease` step: remove `prerelease: true` and extend `files:` with the NSIS updater artifact:
```yaml
      - name: Publish client installer to release
        uses: softprops/action-gh-release@v2
        with:
          tag_name: v${{ env.VERSION }}
          name: yeson-meet v${{ env.VERSION }}
          body: |
            (… keep the existing body verbatim …)
          files: |
            apps/desktop/src-tauri/target/release/bundle/msi/*.msi
            apps/desktop/src-tauri/target/release/bundle/nsis/*-setup.exe
            apps/desktop/src-tauri/target/release/bundle/nsis/*-setup.exe.sig
          fail_on_unmatched_files: true
```

Append the merge step after publish:
```yaml
      - name: Merge client entry into update manifest
        shell: bash
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          VERSION: ${{ env.VERSION }}
          REPO: ${{ github.repository }}
          MANIFEST: latest-client.json
          PLATFORM_KEY: windows-x86_64
          ARTIFACT_GLOB: apps/desktop/src-tauri/target/release/bundle/nsis/*-setup.exe
        run: bash .github/scripts/merge-update-manifest.sh
```

- [ ] **Step 5: Update `server-desktop-macos.yml`**

Add signing env to `Build server console installer (app + dmg)` (lines 62-64):
```yaml
      - name: Build server console installer (app + dmg)
        working-directory: apps/server_desktop
        env:
          TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
        run: pnpm tauri build --bundles app dmg
```

In the publish step: remove `prerelease: true`, extend `files:`:
```yaml
          files: |
            apps/server_desktop/src-tauri/target/release/bundle/dmg/*.dmg
            apps/server_desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz
            apps/server_desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz.sig
          fail_on_unmatched_files: true
```

Append the merge step after publish:
```yaml
      - name: Merge server entry into update manifest
        shell: bash
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          VERSION: ${{ env.VERSION }}
          REPO: ${{ github.repository }}
          MANIFEST: latest-server.json
          PLATFORM_KEY: darwin-aarch64
          ARTIFACT_GLOB: apps/server_desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz
        run: bash .github/scripts/merge-update-manifest.sh
```

- [ ] **Step 6: Update `server-desktop-windows.yml`**

Add signing env to `Build server console installer (nsis + msi)` (lines 84-87):
```yaml
      - name: Build server console installer (nsis + msi)
        shell: pwsh
        working-directory: apps/server_desktop
        env:
          TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
        run: pnpm tauri build --bundles nsis msi
```

In the publish step: remove `prerelease: true`, extend `files:` (keep the existing nsis `*.exe` line — note the manifest needs specifically the `-setup.exe`, which the `*.exe` glob already covers; add the `.sig`):
```yaml
          files: |
            apps/server_desktop/src-tauri/target/release/bundle/nsis/*.exe
            apps/server_desktop/src-tauri/target/release/bundle/nsis/*-setup.exe.sig
            apps/server_desktop/src-tauri/target/release/bundle/msi/*.msi
          fail_on_unmatched_files: true
```

Append the merge step after publish:
```yaml
      - name: Merge server entry into update manifest
        shell: bash
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          VERSION: ${{ env.VERSION }}
          REPO: ${{ github.repository }}
          MANIFEST: latest-server.json
          PLATFORM_KEY: windows-x86_64
          ARTIFACT_GLOB: apps/server_desktop/src-tauri/target/release/bundle/nsis/*-setup.exe
        run: bash .github/scripts/merge-update-manifest.sh
```

- [ ] **Step 7: Lint the workflow YAML**

```bash
python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]; print('all workflows parse')"
grep -rn "prerelease: true" .github/workflows/ && echo "STILL HAS PRERELEASE — fix" || echo "no prerelease flags remain"
```
Expected: `all workflows parse`; the grep prints `no prerelease flags remain` (grep exits non-zero when nothing matches, triggering the `||` branch).

- [ ] **Step 8: Commit**

```bash
git add .github/scripts/merge-update-manifest.sh .github/workflows/macos-desktop.yml .github/workflows/windows-desktop.yml .github/workflows/server-desktop-macos.yml .github/workflows/server-desktop-windows.yml
git commit -m "$(cat <<'EOF'
ci(update): sign installers, upload updater artifacts, merge manifests, publish full releases

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Local update rehearsal + release checklist

Prove the full detect → download → banner → relaunch loop on this Mac against a local HTTP server, before the first real release ships the updater. The updater only runs in an **installed/bundled** app, never in `tauri dev`.

**Files:**
- Modify (temporary, reverted at the end): `apps/desktop/src-tauri/tauri.conf.json` (`.version`, `.plugins.updater.endpoints`, `.plugins.updater.pubkey`)
- No permanent file changes — this task is a manual verification runbook plus the checklist text below.

**Interfaces:**
- Consumes: everything from Tasks 1-7 (the client app must build with the updater wired in).
- Produces: recorded evidence that auto-update works end-to-end; no code artifact.

- [ ] **Step 1: Mint a throwaway local signing key**

```bash
pnpm --filter @yeson-meet/desktop exec tauri signer generate -w /tmp/rehearsal.key
# note the printed public key; it replaces plugins.updater.pubkey below
export TAURI_SIGNING_PRIVATE_KEY="$(cat /tmp/rehearsal.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""   # match the empty password chosen at generate time
```

- [ ] **Step 2: Point the client at localhost + build v0.0.1**

Edit `apps/desktop/src-tauri/tauri.conf.json`: set `.version` to `0.0.1`, `.plugins.updater.pubkey` to the rehearsal public key, and `.plugins.updater.endpoints` to `["http://localhost:8787/latest-client.json"]`. Then build just the `.app`:

```bash
pnpm --filter @yeson-meet/desktop tauri build --bundles app
open apps/desktop/src-tauri/target/release/bundle/macos/*.app   # launch the installed v0.0.1
```
Expected: the app opens; the sidebar shows `v0.0.1`; the update banner shows nothing new (localhost:8787 not serving yet → a caught error, app still usable).

- [ ] **Step 3: Build v0.0.2 and stage its updater artifact**

Bump `.version` to `0.0.2` in `tauri.conf.json`, rebuild, and collect the artifact + signature into a serving dir:

```bash
pnpm --filter @yeson-meet/desktop tauri build --bundles app
mkdir -p /tmp/upd-serve
cp apps/desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz /tmp/upd-serve/
SIG=$(cat apps/desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz.sig)
FN=$(basename apps/desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz)
cat > /tmp/upd-serve/latest-client.json <<EOF
{
  "version": "0.0.2",
  "pub_date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "platforms": {
    "darwin-aarch64": { "signature": "$SIG", "url": "http://localhost:8787/$FN" }
  }
}
EOF
```

- [ ] **Step 4: Serve the manifest + artifact**

```bash
cd /tmp/upd-serve && python3 -m http.server 8787
```
Leave this running in its own terminal.

- [ ] **Step 5: Observe the update on the still-running v0.0.1**

In the v0.0.1 app, click `지금 업데이트 확인` (or wait for the startup check). Expected sequence:
1. Banner shows `업데이트 내려받는 중 (NN%)`.
2. Banner switches to `v0.0.2 준비됨 — 재시작하여 적용` with the mac permission note.
3. Click `재시작하여 업데이트` → the app installs and relaunches; the sidebar now reads `v0.0.2`.

Record PASS/FAIL for each of the three transitions.

- [ ] **Step 6: Verify the macOS permission note in practice**

After the relaunch, open the client and confirm the existing screen-recording banner behavior: if macOS dropped the TCC grant (cdhash changed), the app's existing `NativeCaptureBanner` re-prompts — confirming no extra logic is needed and the note's guidance is accurate.

- [ ] **Step 7: Tear down and revert the temporary config**

```bash
# stop the python http.server (Ctrl+C in its terminal), then:
git checkout apps/desktop/src-tauri/tauri.conf.json
rm -rf /tmp/upd-serve /tmp/rehearsal.key /tmp/rehearsal.key.pub
unset TAURI_SIGNING_PRIVATE_KEY TAURI_SIGNING_PRIVATE_KEY_PASSWORD
git status   # expected: clean (rehearsal left no committed changes)
```

- [ ] **Step 8: Record the two-release checklist (add to the team's release runbook)**

The updater only proves itself across two real releases. Add these items to the release checklist used for future versions:

```
[ ] Release N (this one) ships the updater for the first time — installed by manual download.
[ ] Release N+1: on a Windows box AND an Apple-Silicon Mac already running N,
    confirm the "vN+1 준비됨 — 재시작하여 적용" banner appears within a few minutes,
    and that clicking it relaunches into N+1.
[ ] Mac only: after the N→N+1 auto-update, confirm screen-recording permission
    (re-grant via the app's existing banner if macOS dropped it).
[ ] Confirm latest-client.json and latest-server.json are attached to the release
    and each lists darwin-aarch64 + windows-x86_64 entries.
[ ] Intel Mac users (darwin-x86_64) are NOT auto-updated (out of scope) — notify
    them to reinstall the local Intel dmg manually.
```

- [ ] **Step 9: Commit (checklist only, if the runbook is a repo file)**

If your team keeps the release checklist in a repo doc, add the items there and commit; otherwise this task produces only verification evidence (no commit).

```bash
git add -A && git commit -m "$(cat <<'EOF'
docs(update): add two-release auto-update verification checklist

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)" || echo "no doc file to commit — rehearsal evidence recorded out-of-band"
```

---

## Out of Scope (from the spec — do NOT implement)

- Code signing / notarization (Apple + Windows) — parked decision stands. Updater ed25519 signing is separate and IS in scope.
- `darwin-x86_64` (Intel Mac) auto-update — no CI/signing path for the local `makehybrid` dmg; Intel users reinstall manually.
- Delta updates, forced auto-restart, nightly/beta channel split, automatic rollback (rollback fallback = manual reinstall of a prior release asset).

## Self-Review (performed against the spec before finalizing)

**1. Spec coverage** — every spec section maps to a task:
- 1회 셋업 서명 키 → Task 1 (keygen, backup, secrets, pubkey).
- `createUpdaterArtifacts` + endpoints + capabilities → Tasks 1 & 2 (including the Windows-override gotcha and the deep-merge note for macOS).
- Rust registration + Cargo/package deps → Task 2.
- 앱 내 UX (startup + 4h check, silent download, ready banner near version, manual button, failures never block, macOS note) → Tasks 3-6.
- CI (signing env, updater artifact upload, jq manifest merge, drop prerelease) → Task 7.
- 로컬 리허설 + two-release checklist + mac permission re-check → Task 8.
- 범위 밖 list → mirrored in the Out of Scope section.

**2. Placeholder scan** — the only intentional `PASTE_…` token is the operator-generated pubkey (a real runtime value, produced by the exact `tauri signer generate` command in Task 1 Step 1); all code/config/scripts are complete. No "TBD"/"similar to Task N"/"add appropriate…".

**3. Type consistency** — `UpdateStatus`/`UpdateAction`/`updateReducer`/`initialUpdateStatus`/`isMacOS` are defined identically in Tasks 3 (client) and 5 (server) and consumed with matching signatures by the hooks/banners in Tasks 4 & 6. Hook return shape `{ status, checkNow, applyNow }` matches every `<UpdateBanner status onCheckNow onApplyNow />` call site. Manifest field names (`version`, `pub_date`, `platforms.<key>.{signature,url}`) are consistent between the merge script (Task 7) and the rehearsal manifest (Task 8).
