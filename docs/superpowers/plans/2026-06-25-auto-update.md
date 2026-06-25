# Auto-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Both Tauri apps (server console `apps/server_desktop`, operator client `apps/desktop`) check for a new release on startup, show a banner, and install + relaunch on one click.

**Architecture:** Tauri v2 `updater` plugin reads a signed static manifest (`latest-server.json` / `latest-client.json`) hosted on a moving GitHub Release tag `updater-latest`. The manifest's artifact URLs point at the per-version prerelease (`v0.9.x`) assets. A minisign keypair (shared by both apps) signs the update bundles; the public key is committed, the private key lives in GitHub Secrets. A frontend hook drives a banner; install runs `downloadAndInstall()` then `relaunch()`.

**Tech Stack:** Tauri v2, `tauri-plugin-updater`/`tauri-plugin-process` (Rust `"2"`), `@tauri-apps/plugin-updater`/`@tauri-apps/plugin-process` (JS), React 18, Vitest 2, GitHub Actions, `gh` CLI.

## Global Constraints

- Plugin versions: Rust crates `tauri-plugin-updater = "2"`, `tauri-plugin-process = "2"`; JS `@tauri-apps/plugin-updater@^2`, `@tauri-apps/plugin-process@^2`.
- Repo: `yesonsys03-web/yeson_meet` (PUBLIC). Per-version prereleases use a hardcoded tag (currently `v0.9.6`).
- Updater manifest endpoint (committed in `tauri.conf.json`), per app:
  - server: `https://github.com/yesonsys03-web/yeson_meet/releases/download/updater-latest/latest-server.json`
  - client: `https://github.com/yesonsys03-web/yeson_meet/releases/download/updater-latest/latest-client.json`
- Manifest platform keys: `darwin-aarch64` (macOS, aarch64-only) and `windows-x86_64`.
- Updater artifacts: macOS = `*.app.tar.gz` (+ `.sig`); Windows = NSIS `*-setup.exe` (+ `.sig`). NSIS is used by the updater only (it runs the installer itself, so no SmartScreen/MOTW prompt); manual downloads keep using the `.msi` (unchanged).
- productName: server = `yeson-server-console`, client = `yeson-meet`. App version authority = each `src-tauri/tauri.conf.json` `version`.
- Behavior (approved): check once at startup → notify via banner → user clicks → download+install+relaunch. No silent/forced install, no polling.
- VibeLign: smallest patches; keep entry files thin; wrap each NEW `.ts`/`.tsx` file in `// === ANCHOR: NAME_START ===` … `// === ANCHOR: NAME_END ===` to match repo convention; only edit inside existing anchors.
- Secret material (private signing key, password) MUST NOT be committed.
- Node 22 / pnpm 9.0.0 in CI.

---

### Task 1: Generate the minisign signing keypair (foundation)

This is a one-time interactive step that produces secret material consumed by every later task (the public key) and by CI (the private key + password).

**Files:**
- Modify (later tasks): both `tauri.conf.json` `plugins.updater.pubkey`
- No repo files created in this task (the private key is written OUTSIDE the repo)

**Interfaces:**
- Produces: `UPDATER_PUBKEY` — the base64 minisign public key string (one line). Later tasks paste it verbatim into both `tauri.conf.json` files.
- Produces: `TAURI_SIGNING_PRIVATE_KEY` (base64 private key file contents) and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` — added to GitHub Secrets in Task 8.

- [ ] **Step 1: Generate the keypair (writes private key outside the repo tree)**

Run:
```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet/apps/server_desktop
pnpm tauri signer generate -w "$HOME/.yeson/updater.key"
```
When prompted, set a password (remember it — it becomes `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`).
Expected: prints a **Public key** (base64) to stdout and writes `~/.yeson/updater.key` (private) + `~/.yeson/updater.key.pub`.

- [ ] **Step 2: Capture the values**

Run:
```bash
echo "PUBKEY:"; cat "$HOME/.yeson/updater.key.pub"
echo "PRIVATE (for GitHub secret TAURI_SIGNING_PRIVATE_KEY):"; cat "$HOME/.yeson/updater.key"
```
Record the public key as `UPDATER_PUBKEY` for Tasks 4 and 7. Keep the private key + password for Task 8 (GitHub Secrets). Do **not** write either into the repo.

- [ ] **Step 3: Verify the private key file is untracked**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet && git status --porcelain | grep -i 'updater.key' || echo "clean: key not in repo"`
Expected: `clean: key not in repo`

- [ ] **Step 4: No commit** (this task produces no committed files).

---

### Task 2: server console — updater core module + tests (TDD)

**Files:**
- Create: `apps/server_desktop/src/updater.ts`
- Test: `apps/server_desktop/src/updater.test.ts`

**Interfaces:**
- Produces:
  - `downloadPercent(downloaded: number, contentLength: number | null | undefined): number`
  - `interface AppUpdate { version: string; body?: string; downloadAndInstall(onEvent?: (e: DownloadEvent) => void): Promise<void> }`
  - `type DownloadEvent = { event: "Started"; data: { contentLength?: number } } | { event: "Progress"; data: { chunkLength: number } } | { event: "Finished" }`
  - `type CheckFn = () => Promise<AppUpdate | null>`
  - `checkForUpdate(checkFn?: CheckFn): Promise<AppUpdate | null>` (swallows errors → `null`)

- [ ] **Step 1: Write the failing test**

Create `apps/server_desktop/src/updater.test.ts`:
```ts
// === ANCHOR: UPDATER_TEST_START ===
import { describe, expect, it } from "vitest";

import { checkForUpdate, downloadPercent, type AppUpdate } from "./updater";

describe("downloadPercent", () => {
  it("returns 0 when the content length is unknown", () => {
    expect(downloadPercent(1000, null)).toBe(0);
    expect(downloadPercent(1000, 0)).toBe(0);
    expect(downloadPercent(1000, undefined)).toBe(0);
  });

  it("computes a clamped, rounded percent", () => {
    expect(downloadPercent(0, 200)).toBe(0);
    expect(downloadPercent(50, 200)).toBe(25);
    expect(downloadPercent(200, 200)).toBe(100);
    expect(downloadPercent(999, 200)).toBe(100); // never exceeds 100
  });
});

describe("checkForUpdate", () => {
  const fakeUpdate: AppUpdate = { version: "9.9.9", body: "notes", downloadAndInstall: async () => {} };

  it("returns the update when one is available", async () => {
    expect(await checkForUpdate(async () => fakeUpdate)).toBe(fakeUpdate);
  });

  it("returns null when up to date", async () => {
    expect(await checkForUpdate(async () => null)).toBeNull();
  });

  it("swallows errors and returns null (offline / non-Tauri runtime)", async () => {
    expect(
      await checkForUpdate(async () => {
        throw new Error("no Tauri runtime");
      }),
    ).toBeNull();
  });
});
// === ANCHOR: UPDATER_TEST_END ===
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server_desktop && pnpm test updater`
Expected: FAIL — `Failed to resolve import "./updater"` (module does not exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `apps/server_desktop/src/updater.ts`:
```ts
// === ANCHOR: UPDATER_START ===
import { check } from "@tauri-apps/plugin-updater";

// A structural view of the plugin's `Update` object — only the members we use,
// so tests can pass a plain fake without constructing the plugin class.
export interface AppUpdate {
  version: string;
  body?: string;
  downloadAndInstall(onEvent?: (e: DownloadEvent) => void): Promise<void>;
}

export type DownloadEvent =
  | { event: "Started"; data: { contentLength?: number } }
  | { event: "Progress"; data: { chunkLength: number } }
  | { event: "Finished" };

export type CheckFn = () => Promise<AppUpdate | null>;

// Cumulative-bytes → integer percent, clamped to [0, 100]. Returns 0 when the
// total size is unknown (the banner then shows an indeterminate "downloading…").
export function downloadPercent(downloaded: number, contentLength: number | null | undefined): number {
  if (!contentLength || contentLength <= 0) return 0;
  const pct = Math.round((downloaded / contentLength) * 100);
  return Math.max(0, Math.min(100, pct));
}

// Best-effort update check. Any failure (offline, no manifest, no Tauri runtime
// in the browser preview/tests) resolves to null so the caller simply shows no
// banner. Defaults to the real plugin `check`; tests inject their own.
export async function checkForUpdate(checkFn: CheckFn = check as unknown as CheckFn): Promise<AppUpdate | null> {
  try {
    return (await checkFn()) ?? null;
  } catch {
    return null;
  }
}
// === ANCHOR: UPDATER_END ===
```

- [ ] **Step 4: Add the JS plugin deps so the import resolves**

Run:
```bash
cd apps/server_desktop
pnpm add @tauri-apps/plugin-updater@^2 @tauri-apps/plugin-process@^2
```
Expected: both appear under `dependencies` in `apps/server_desktop/package.json`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/server_desktop && pnpm test updater`
Expected: PASS (3 + 2 assertions across the two describe blocks).

- [ ] **Step 6: Commit**

```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet
git add apps/server_desktop/src/updater.ts apps/server_desktop/src/updater.test.ts apps/server_desktop/package.json pnpm-lock.yaml
git commit -m "feat(server_desktop): updater core (downloadPercent + checkForUpdate) + deps"
```

---

### Task 3: server console — updater hook + banner + wiring

**Files:**
- Create: `apps/server_desktop/src/useUpdater.ts`
- Create: `apps/server_desktop/src/UpdateBanner.tsx`
- Modify: `apps/server_desktop/src/ServerConsole.tsx` (import + render banner in header)

**Interfaces:**
- Consumes (Task 2): `AppUpdate`, `DownloadEvent`, `checkForUpdate`, `downloadPercent`
- Produces:
  - `type UpdaterStatus = { kind: "idle" } | { kind: "available"; version: string } | { kind: "downloading"; version: string; percent: number } | { kind: "installing"; version: string } | { kind: "error"; version: string | null; message: string }`
  - `useUpdater(): { status: UpdaterStatus; install: () => void; dismiss: () => void }`
  - `UpdateBanner({ status, onInstall, onDismiss }): JSX.Element | null`

- [ ] **Step 1: Write the updater hook**

Create `apps/server_desktop/src/useUpdater.ts`:
```ts
// === ANCHOR: USE_UPDATER_START ===
import { useCallback, useEffect, useState } from "react";
import { relaunch } from "@tauri-apps/plugin-process";

import { type AppUpdate, checkForUpdate, downloadPercent } from "./updater";

export type UpdaterStatus =
  | { kind: "idle" }
  | { kind: "available"; version: string }
  | { kind: "downloading"; version: string; percent: number }
  | { kind: "installing"; version: string }
  | { kind: "error"; version: string | null; message: string };

// Checks once on mount. If an update is available, exposes it via `status` and
// an `install()` that downloads (reporting progress), installs, and relaunches
// into the new version. All failures are surfaced on the banner, never thrown.
export function useUpdater(): { status: UpdaterStatus; install: () => void; dismiss: () => void } {
  const [status, setStatus] = useState<UpdaterStatus>({ kind: "idle" });
  const [update, setUpdate] = useState<AppUpdate | null>(null);

  useEffect(() => {
    let cancelled = false;
    void checkForUpdate().then((u) => {
      if (cancelled || !u) return;
      setUpdate(u);
      setStatus({ kind: "available", version: u.version });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const install = useCallback(() => {
    if (!update) return;
    const version = update.version;
    let total = 0;
    let downloaded = 0;
    setStatus({ kind: "downloading", version, percent: 0 });
    update
      .downloadAndInstall((e) => {
        if (e.event === "Started") {
          total = e.data.contentLength ?? 0;
        } else if (e.event === "Progress") {
          downloaded += e.data.chunkLength;
          setStatus({ kind: "downloading", version, percent: downloadPercent(downloaded, total) });
        } else if (e.event === "Finished") {
          setStatus({ kind: "installing", version });
        }
      })
      .then(() => relaunch())
      .catch((err) => setStatus({ kind: "error", version, message: err instanceof Error ? err.message : String(err) }));
  }, [update]);

  const dismiss = useCallback(() => setStatus({ kind: "idle" }), []);

  return { status, install, dismiss };
}
// === ANCHOR: USE_UPDATER_END ===
```

- [ ] **Step 2: Write the banner component**

Create `apps/server_desktop/src/UpdateBanner.tsx`:
```tsx
// === ANCHOR: UPDATE_BANNER_START ===
import type { UpdaterStatus } from "./useUpdater";

type UpdateBannerProps = {
  status: UpdaterStatus;
  onInstall: () => void;
  onDismiss: () => void;
};

export default function UpdateBanner({ status, onInstall, onDismiss }: UpdateBannerProps) {
  if (status.kind === "idle") return null;

  return (
    <div style={styles.banner}>
      {status.kind === "available" ? (
        <>
          <span style={styles.text}>새 버전 v{status.version} 사용 가능</span>
          <button type="button" style={styles.install} onClick={onInstall}>
            지금 업데이트
          </button>
          <button type="button" style={styles.later} onClick={onDismiss}>
            나중에
          </button>
        </>
      ) : status.kind === "downloading" ? (
        <span style={styles.text}>v{status.version} 다운로드 중… {status.percent}%</span>
      ) : status.kind === "installing" ? (
        <span style={styles.text}>v{status.version} 설치 중… 곧 재시작됩니다</span>
      ) : (
        <>
          <span style={styles.text}>업데이트 실패: {status.message}</span>
          <button type="button" style={styles.later} onClick={onDismiss}>
            닫기
          </button>
        </>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  banner: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    flexWrap: "wrap",
    margin: "12px 0 0",
    padding: "8px 12px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-accent-strong)",
    background: "var(--ys-accent-soft)",
    color: "var(--ys-on-accent)",
    fontSize: 13,
  },
  text: { fontWeight: "var(--ys-weight-bold)" as React.CSSProperties["fontWeight"] },
  install: {
    padding: "6px 14px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-accent-strong)",
    background: "var(--ys-accent-strong)",
    color: "var(--ys-on-accent)",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: "var(--ys-weight-black)" as React.CSSProperties["fontWeight"],
  },
  later: {
    padding: "6px 12px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-border-strong)",
    background: "transparent",
    color: "var(--ys-text-label)",
    cursor: "pointer",
    fontSize: 13,
  },
};
// === ANCHOR: UPDATE_BANNER_END ===
```

- [ ] **Step 3: Wire the banner into `ServerConsole.tsx`**

Add the imports after the existing `DevicePanel` import (around line 7):
```tsx
import UpdateBanner from "./UpdateBanner";
import { useUpdater } from "./useUpdater";
```
Inside `ServerConsole()`, after the existing `const [appVersion, setAppVersion] = useState<string>("");` line, add:
```tsx
  const updater = useUpdater();
```
In the JSX, render the banner inside `<header style={styles.header}>` immediately after `<TunnelDegradedBanner ... />` (just before the `{error ? ...}` line):
```tsx
          <UpdateBanner status={updater.status} onInstall={updater.install} onDismiss={updater.dismiss} />
```

- [ ] **Step 4: Verify type-check + build pass**

Run: `cd apps/server_desktop && pnpm build:vite`
Expected: PASS (`tsc --noEmit` clean, `vite build` succeeds).

- [ ] **Step 5: Run the unit tests (no regressions)**

Run: `cd apps/server_desktop && pnpm test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet
git add apps/server_desktop/src/useUpdater.ts apps/server_desktop/src/UpdateBanner.tsx apps/server_desktop/src/ServerConsole.tsx
git commit -m "feat(server_desktop): update-available banner + one-click install"
```

---

### Task 4: server console — register Tauri updater/process plugin + config

**Files:**
- Modify: `apps/server_desktop/src-tauri/Cargo.toml` (add two deps)
- Modify: `apps/server_desktop/src-tauri/src/lib.rs:13` (register plugins, inside `LIB` anchor)
- Modify: `apps/server_desktop/src-tauri/capabilities/default.json` (permissions)
- Modify: `apps/server_desktop/src-tauri/tauri.conf.json` (bundle flag + updater plugin)

**Interfaces:**
- Consumes (Task 1): `UPDATER_PUBKEY`

- [ ] **Step 1: Add Rust deps**

In `apps/server_desktop/src-tauri/Cargo.toml`, under `[dependencies]` (next to the existing `tauri = { version = "2", features = [] }`), add:
```toml
tauri-plugin-updater = "2"
tauri-plugin-process = "2"
```

- [ ] **Step 2: Register the plugins**

In `apps/server_desktop/src-tauri/src/lib.rs`, change the builder start (line 13) from:
```rust
    let app = tauri::Builder::default()
        // Reap leftover cloudflared / yeson-server processes from a prior app
```
to:
```rust
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        // Reap leftover cloudflared / yeson-server processes from a prior app
```

- [ ] **Step 3: Add capability permissions**

In `apps/server_desktop/src-tauri/capabilities/default.json`, change the `permissions` array from:
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

- [ ] **Step 4: Add updater config to `tauri.conf.json`**

In `apps/server_desktop/src-tauri/tauri.conf.json`, add `"createUpdaterArtifacts": true` as the first key inside `"bundle"` (before `"active": true`):
```json
  "bundle": {
    "createUpdaterArtifacts": true,
    "active": true,
```
Then add a top-level `"plugins"` block after the closing `}` of `"bundle"` (sibling of `bundle`/`app`), replacing `<UPDATER_PUBKEY>` with the value from Task 1:
```json
  "plugins": {
    "updater": {
      "pubkey": "<UPDATER_PUBKEY>",
      "endpoints": [
        "https://github.com/yesonsys03-web/yeson_meet/releases/download/updater-latest/latest-server.json"
      ]
    }
  }
```

- [ ] **Step 5: Verify Rust compiles**

Run: `cargo check --manifest-path apps/server_desktop/src-tauri/Cargo.toml`
Expected: PASS (downloads + compiles the two new crates; no errors). Note: first run is slow.

- [ ] **Step 6: Verify config is valid JSON**

Run: `cd apps/server_desktop && node -e "JSON.parse(require('fs').readFileSync('src-tauri/tauri.conf.json','utf8')); console.log('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet
git add apps/server_desktop/src-tauri/Cargo.toml apps/server_desktop/src-tauri/Cargo.lock apps/server_desktop/src-tauri/src/lib.rs apps/server_desktop/src-tauri/capabilities/default.json apps/server_desktop/src-tauri/tauri.conf.json
git commit -m "feat(server_desktop): register updater/process plugin + manifest endpoint"
```

---

### Task 5: client — updater core module + tests (TDD)

**Files:**
- Create: `apps/desktop/src/update/updater.ts`
- Test: `apps/desktop/src/update/updater.test.ts`

**Interfaces:**
- Produces: identical surface to Task 2 (`downloadPercent`, `AppUpdate`, `DownloadEvent`, `CheckFn`, `checkForUpdate`), in the client package.

- [ ] **Step 1: Write the failing test**

Create `apps/desktop/src/update/updater.test.ts`:
```ts
// === ANCHOR: UPDATER_TEST_START ===
import { describe, expect, it } from "vitest";

import { checkForUpdate, downloadPercent, type AppUpdate } from "./updater";

describe("downloadPercent", () => {
  it("returns 0 when the content length is unknown", () => {
    expect(downloadPercent(1000, null)).toBe(0);
    expect(downloadPercent(1000, 0)).toBe(0);
    expect(downloadPercent(1000, undefined)).toBe(0);
  });

  it("computes a clamped, rounded percent", () => {
    expect(downloadPercent(0, 200)).toBe(0);
    expect(downloadPercent(50, 200)).toBe(25);
    expect(downloadPercent(200, 200)).toBe(100);
    expect(downloadPercent(999, 200)).toBe(100);
  });
});

describe("checkForUpdate", () => {
  const fakeUpdate: AppUpdate = { version: "9.9.9", body: "notes", downloadAndInstall: async () => {} };

  it("returns the update when one is available", async () => {
    expect(await checkForUpdate(async () => fakeUpdate)).toBe(fakeUpdate);
  });

  it("returns null when up to date", async () => {
    expect(await checkForUpdate(async () => null)).toBeNull();
  });

  it("swallows errors and returns null (offline / non-Tauri runtime)", async () => {
    expect(
      await checkForUpdate(async () => {
        throw new Error("no Tauri runtime");
      }),
    ).toBeNull();
  });
});
// === ANCHOR: UPDATER_TEST_END ===
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/desktop && pnpm test update/updater`
Expected: FAIL — cannot resolve `./updater`.

- [ ] **Step 3: Write minimal implementation**

Create `apps/desktop/src/update/updater.ts`:
```ts
// === ANCHOR: UPDATER_START ===
import { check } from "@tauri-apps/plugin-updater";

// A structural view of the plugin's `Update` object — only the members we use,
// so tests can pass a plain fake without constructing the plugin class.
export interface AppUpdate {
  version: string;
  body?: string;
  downloadAndInstall(onEvent?: (e: DownloadEvent) => void): Promise<void>;
}

export type DownloadEvent =
  | { event: "Started"; data: { contentLength?: number } }
  | { event: "Progress"; data: { chunkLength: number } }
  | { event: "Finished" };

export type CheckFn = () => Promise<AppUpdate | null>;

// Cumulative-bytes → integer percent, clamped to [0, 100]. Returns 0 when the
// total size is unknown (the banner then shows an indeterminate "downloading…").
export function downloadPercent(downloaded: number, contentLength: number | null | undefined): number {
  if (!contentLength || contentLength <= 0) return 0;
  const pct = Math.round((downloaded / contentLength) * 100);
  return Math.max(0, Math.min(100, pct));
}

// Best-effort update check. Any failure (offline, no manifest, no Tauri runtime
// in the browser preview/tests) resolves to null so the caller simply shows no
// banner. Defaults to the real plugin `check`; tests inject their own.
export async function checkForUpdate(checkFn: CheckFn = check as unknown as CheckFn): Promise<AppUpdate | null> {
  try {
    return (await checkFn()) ?? null;
  } catch {
    return null;
  }
}
// === ANCHOR: UPDATER_END ===
```

- [ ] **Step 4: Add the JS plugin deps**

Run:
```bash
cd apps/desktop
pnpm add @tauri-apps/plugin-updater@^2 @tauri-apps/plugin-process@^2
```
Expected: both appear under `dependencies` in `apps/desktop/package.json`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/desktop && pnpm test update/updater`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet
git add apps/desktop/src/update/updater.ts apps/desktop/src/update/updater.test.ts apps/desktop/package.json pnpm-lock.yaml
git commit -m "feat(desktop): updater core (downloadPercent + checkForUpdate) + deps"
```

---

### Task 6: client — updater hook + banner + wiring

**Files:**
- Create: `apps/desktop/src/update/useUpdater.ts`
- Create: `apps/desktop/src/update/UpdateBanner.tsx`
- Modify: `apps/desktop/src/console/DesktopConsole.tsx` (import + render banner above `NativeCaptureBanner`)

**Interfaces:**
- Consumes (Task 5): `AppUpdate`, `checkForUpdate`, `downloadPercent`
- Produces: `UpdaterStatus`, `useUpdater()`, `UpdateBanner` — same surface as Task 3.

- [ ] **Step 1: Write the updater hook**

Create `apps/desktop/src/update/useUpdater.ts`:
```ts
// === ANCHOR: USE_UPDATER_START ===
import { useCallback, useEffect, useState } from "react";
import { relaunch } from "@tauri-apps/plugin-process";

import { type AppUpdate, checkForUpdate, downloadPercent } from "./updater";

export type UpdaterStatus =
  | { kind: "idle" }
  | { kind: "available"; version: string }
  | { kind: "downloading"; version: string; percent: number }
  | { kind: "installing"; version: string }
  | { kind: "error"; version: string | null; message: string };

// Checks once on mount. If an update is available, exposes it via `status` and
// an `install()` that downloads (reporting progress), installs, and relaunches
// into the new version. All failures are surfaced on the banner, never thrown.
export function useUpdater(): { status: UpdaterStatus; install: () => void; dismiss: () => void } {
  const [status, setStatus] = useState<UpdaterStatus>({ kind: "idle" });
  const [update, setUpdate] = useState<AppUpdate | null>(null);

  useEffect(() => {
    let cancelled = false;
    void checkForUpdate().then((u) => {
      if (cancelled || !u) return;
      setUpdate(u);
      setStatus({ kind: "available", version: u.version });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const install = useCallback(() => {
    if (!update) return;
    const version = update.version;
    let total = 0;
    let downloaded = 0;
    setStatus({ kind: "downloading", version, percent: 0 });
    update
      .downloadAndInstall((e) => {
        if (e.event === "Started") {
          total = e.data.contentLength ?? 0;
        } else if (e.event === "Progress") {
          downloaded += e.data.chunkLength;
          setStatus({ kind: "downloading", version, percent: downloadPercent(downloaded, total) });
        } else if (e.event === "Finished") {
          setStatus({ kind: "installing", version });
        }
      })
      .then(() => relaunch())
      .catch((err) => setStatus({ kind: "error", version, message: err instanceof Error ? err.message : String(err) }));
  }, [update]);

  const dismiss = useCallback(() => setStatus({ kind: "idle" }), []);

  return { status, install, dismiss };
}
// === ANCHOR: USE_UPDATER_END ===
```

- [ ] **Step 2: Write the banner component**

Create `apps/desktop/src/update/UpdateBanner.tsx` (uses the same `@yeson-meet/ui` `var(--ys-*)` tokens as the rest of the client):
```tsx
// === ANCHOR: UPDATE_BANNER_START ===
import type { UpdaterStatus } from "./useUpdater";

type UpdateBannerProps = {
  status: UpdaterStatus;
  onInstall: () => void;
  onDismiss: () => void;
};

export default function UpdateBanner({ status, onInstall, onDismiss }: UpdateBannerProps) {
  if (status.kind === "idle") return null;

  return (
    <div style={styles.banner}>
      {status.kind === "available" ? (
        <>
          <span style={styles.text}>새 버전 v{status.version} 사용 가능</span>
          <button type="button" style={styles.install} onClick={onInstall}>
            지금 업데이트
          </button>
          <button type="button" style={styles.later} onClick={onDismiss}>
            나중에
          </button>
        </>
      ) : status.kind === "downloading" ? (
        <span style={styles.text}>v{status.version} 다운로드 중… {status.percent}%</span>
      ) : status.kind === "installing" ? (
        <span style={styles.text}>v{status.version} 설치 중… 곧 재시작됩니다</span>
      ) : (
        <>
          <span style={styles.text}>업데이트 실패: {status.message}</span>
          <button type="button" style={styles.later} onClick={onDismiss}>
            닫기
          </button>
        </>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  banner: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    flexWrap: "wrap",
    margin: "12px 0 0",
    padding: "8px 12px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-accent-strong)",
    background: "var(--ys-accent-soft)",
    color: "var(--ys-on-accent)",
    fontSize: 13,
  },
  text: { fontWeight: "var(--ys-weight-bold)" as React.CSSProperties["fontWeight"] },
  install: {
    padding: "6px 14px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-accent-strong)",
    background: "var(--ys-accent-strong)",
    color: "var(--ys-on-accent)",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: "var(--ys-weight-black)" as React.CSSProperties["fontWeight"],
  },
  later: {
    padding: "6px 12px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-border-strong)",
    background: "transparent",
    color: "var(--ys-text-label)",
    cursor: "pointer",
    fontSize: 13,
  },
};
// === ANCHOR: UPDATE_BANNER_END ===
```

- [ ] **Step 3: Wire the banner into `DesktopConsole.tsx`**

Add imports after the existing `import { ConsoleNav } from "./ConsoleNav";` line:
```tsx
import UpdateBanner from "../update/UpdateBanner";
import { useUpdater } from "../update/useUpdater";
```
Inside `DesktopConsole()`, after `const [appVersion, setAppVersion] = useState<string>("");`, add:
```tsx
  const updater = useUpdater();
```
In the returned JSX (the hydrated branch), render the banner inside `<main style={consoleStyles.content}>` immediately before `<NativeCaptureBanner />`:
```tsx
        <UpdateBanner status={updater.status} onInstall={updater.install} onDismiss={updater.dismiss} />
```

- [ ] **Step 4: Verify type-check + build pass**

Run: `cd apps/desktop && pnpm build:vite`
Expected: PASS.

- [ ] **Step 5: Run the unit tests (no regressions)**

Run: `cd apps/desktop && pnpm test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet
git add apps/desktop/src/update/useUpdater.ts apps/desktop/src/update/UpdateBanner.tsx apps/desktop/src/console/DesktopConsole.tsx
git commit -m "feat(desktop): update-available banner + one-click install"
```

---

### Task 7: client — register Tauri updater/process plugin + config

**Files:**
- Modify: `apps/desktop/src-tauri/Cargo.toml`
- Modify: `apps/desktop/src-tauri/src/lib.rs:10-13`
- Modify: `apps/desktop/src-tauri/capabilities/default.json`
- Modify: `apps/desktop/src-tauri/tauri.conf.json` (bundle flag + updater plugin)
- Modify: `apps/desktop/src-tauri/tauri.windows.conf.json` (bundle flag — CI replaces base bundle on Windows)

**Interfaces:**
- Consumes (Task 1): `UPDATER_PUBKEY`

- [ ] **Step 1: Add Rust deps**

In `apps/desktop/src-tauri/Cargo.toml`, under `[dependencies]`, add:
```toml
tauri-plugin-updater = "2"
tauri-plugin-process = "2"
```

- [ ] **Step 2: Register the plugins**

In `apps/desktop/src-tauri/src/lib.rs`, add the two plugins after `.plugin(tauri_plugin_opener::init())` (line 13), so the chain reads:
```rust
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
```

- [ ] **Step 3: Add capability permissions**

In `apps/desktop/src-tauri/capabilities/default.json`, add `"updater:default"` and `"process:allow-restart"` to the `permissions` array (append after the last existing string permission `"opener:allow-open-path"`, keeping the two `fs:` object entries last):
```json
    "opener:allow-open-path",
    "updater:default",
    "process:allow-restart",
    { "identifier": "fs:allow-write-file", "allow": [{ "path": "$HOME/**" }] },
    { "identifier": "fs:allow-create", "allow": [{ "path": "$HOME/**" }] }
```

- [ ] **Step 4: Add updater config + bundle flag to base `tauri.conf.json`**

In `apps/desktop/src-tauri/tauri.conf.json`, add `"createUpdaterArtifacts": true` as the first key inside `"bundle"`, and add the top-level `"plugins"` block (sibling of `bundle`), replacing `<UPDATER_PUBKEY>` with the Task 1 value and pointing at the **client** manifest:
```json
  "plugins": {
    "updater": {
      "pubkey": "<UPDATER_PUBKEY>",
      "endpoints": [
        "https://github.com/yesonsys03-web/yeson_meet/releases/download/updater-latest/latest-client.json"
      ]
    }
  }
```

- [ ] **Step 5: Add the bundle flag to the Windows override**

In `apps/desktop/src-tauri/tauri.windows.conf.json`, add `"createUpdaterArtifacts": true` as the first key inside `"bundle"`:
```json
  "bundle": {
    "createUpdaterArtifacts": true,
    "active": true,
    "targets": ["nsis", "msi"],
```
(Required because the Windows CI job replaces the base `bundle` with this file's `bundle`; without it the Windows build would not emit signed updater artifacts. macOS is unaffected — Tauri deep-merges `tauri.macos.conf.json` over the base, so the base flag survives there.)

- [ ] **Step 6: Verify Rust compiles + JSON valid**

Run: `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml`
Expected: PASS.
Run: `cd apps/desktop && node -e "['src-tauri/tauri.conf.json','src-tauri/tauri.windows.conf.json'].forEach(f=>JSON.parse(require('fs').readFileSync(f,'utf8'))); console.log('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet
git add apps/desktop/src-tauri/Cargo.toml apps/desktop/src-tauri/Cargo.lock apps/desktop/src-tauri/src/lib.rs apps/desktop/src-tauri/capabilities/default.json apps/desktop/src-tauri/tauri.conf.json apps/desktop/src-tauri/tauri.windows.conf.json
git commit -m "feat(desktop): register updater/process plugin + manifest endpoint"
```

---

### Task 8: CI — sign updater artifacts + upload them to the version release

Adds the signing secrets to the four release workflows and uploads the signed updater bundles (`*.app.tar.gz`/`*-setup.exe` + `.sig`) to the existing per-version prerelease, alongside the current dmg/msi.

**Files:**
- Modify: `.github/workflows/server-desktop-macos.yml`
- Modify: `.github/workflows/server-desktop-windows.yml`
- Modify: `.github/workflows/macos-desktop.yml`
- Modify: `.github/workflows/windows-desktop.yml`
- GitHub repo (web UI): add Secrets

**Interfaces:**
- Consumes (Task 1): `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`

- [ ] **Step 1: Add the two repo secrets**

In the GitHub web UI: repo → Settings → Secrets and variables → Actions → New repository secret. Add:
- `TAURI_SIGNING_PRIVATE_KEY` = contents of `~/.yeson/updater.key` (Task 1).
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` = the password chosen in Task 1.

Verify (CLI alternative): `gh secret list` should list both names.

- [ ] **Step 2: `server-desktop-macos.yml` — sign + upload**

Add an `env:` to the **"Build server console installer (app + dmg)"** step:
```yaml
      - name: Build server console installer (app + dmg)
        working-directory: apps/server_desktop
        env:
          TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
        run: pnpm tauri build --bundles app dmg
```
Add the updater artifacts to the **"Publish installer to prerelease"** `files:` list:
```yaml
          files: |
            apps/server_desktop/src-tauri/target/release/bundle/dmg/*.dmg
            apps/server_desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz
            apps/server_desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz.sig
```

- [ ] **Step 3: `server-desktop-windows.yml` — sign + upload**

Add `env:` to the **"Build server console installer (nsis + msi)"** step (keep `shell: pwsh` and `working-directory`):
```yaml
        env:
          TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
        run: pnpm tauri build --bundles nsis msi
```
Add to the publish `files:` list (alongside the existing nsis/msi lines):
```yaml
            apps/server_desktop/src-tauri/target/release/bundle/nsis/*-setup.exe
            apps/server_desktop/src-tauri/target/release/bundle/nsis/*-setup.exe.sig
```

- [ ] **Step 4: `macos-desktop.yml` — sign + upload**

Add `env:` to the macOS client build step (the one running `pnpm tauri build --bundles app dmg`, `working-directory: apps/desktop`):
```yaml
        env:
          TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
```
Add to the prerelease `files:` list:
```yaml
            apps/desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz
            apps/desktop/src-tauri/target/release/bundle/macos/*.app.tar.gz.sig
```

- [ ] **Step 5: `windows-desktop.yml` — sign + upload**

The Windows client build runs `pnpm --filter @yeson-meet/desktop tauri:build` inside a `run:` block (after the `$baseConfig.bundle = $windowsConfig.bundle` line). Add `env:` to **that** step:
```yaml
        env:
          TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
```
Add the NSIS updater artifacts to the **"Publish client installer to prerelease"** `files:` list (keep the existing `msi/*.msi` line — manual installs still use the MSI):
```yaml
          files: |
            apps/desktop/src-tauri/target/release/bundle/msi/*.msi
            apps/desktop/src-tauri/target/release/bundle/nsis/*-setup.exe
            apps/desktop/src-tauri/target/release/bundle/nsis/*-setup.exe.sig
```
Also update that step's `body:` note: replace the parenthetical "(The NSIS .exe is intentionally not published …)" with: "(The NSIS .exe + .sig are published for the in-app auto-updater only; for manual installs use the .msi.)"

- [ ] **Step 6: Validate workflow YAML**

Run:
```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet
for f in server-desktop-macos server-desktop-windows macos-desktop windows-desktop; do
  python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/$f.yml')); print('$f ok')"
done
```
Expected: four `… ok` lines.

- [ ] **Step 7: Commit**

```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet
git add .github/workflows/server-desktop-macos.yml .github/workflows/server-desktop-windows.yml .github/workflows/macos-desktop.yml .github/workflows/windows-desktop.yml
git commit -m "ci: sign updater artifacts + publish *.app.tar.gz/-setup.exe (+sig)"
```

---

### Task 9: Manifest publisher script + `updater-latest` channel + docs

A maintainer-run script assembles `latest-server.json` / `latest-client.json` from the signed assets on the version release and pushes them to the moving `updater-latest` release.

**Files:**
- Create: `scripts/publish-updater-manifest.mjs`
- Modify: `docs/INSTALL.md` (add an "Auto-update / 자동 업데이트" section — release runbook)

**Interfaces:**
- CLI: `node scripts/publish-updater-manifest.mjs <version>` (e.g. `0.9.7`). Requires `gh` authenticated.
- Reads release `v<version>` assets; writes/uploads `latest-server.json`, `latest-client.json` to release `updater-latest`.

- [ ] **Step 1: Create the `updater-latest` channel release (one-time)**

Run:
```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet
gh release create updater-latest --title "Updater channel (do not delete)" \
  --notes "Holds latest-server.json / latest-client.json for the in-app auto-updater. Assets are overwritten by scripts/publish-updater-manifest.mjs." \
  --prerelease || echo "already exists"
```
Expected: creates the release (or prints `already exists`).

- [ ] **Step 2: Write the publisher script**

Create `scripts/publish-updater-manifest.mjs`:
```js
#!/usr/bin/env node
// Assembles per-app Tauri updater manifests from the signed assets already
// uploaded to the per-version prerelease (vX.Y.Z), then publishes them to the
// moving `updater-latest` release. Run after all four platform builds finish:
//   node scripts/publish-updater-manifest.mjs 0.9.7
// Requires the `gh` CLI authenticated with repo write access.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const REPO = "yesonsys03-web/yeson_meet";
const CHANNEL = "updater-latest";

const version = process.argv[2];
if (!version || !/^\d+\.\d+\.\d+/.test(version)) {
  console.error("usage: node scripts/publish-updater-manifest.mjs <version>  (e.g. 0.9.7)");
  process.exit(1);
}
const tag = `v${version}`;

// Each app: which asset (by filename substring + suffix) feeds which platform.
const APPS = [
  {
    manifest: "latest-server.json",
    prefix: "yeson-server-console",
    mac: { suffix: ".app.tar.gz", target: "darwin-aarch64" },
    win: { suffix: "-setup.exe", target: "windows-x86_64" },
  },
  {
    manifest: "latest-client.json",
    prefix: "yeson-meet",
    mac: { suffix: ".app.tar.gz", target: "darwin-aarch64" },
    win: { suffix: "-setup.exe", target: "windows-x86_64" },
  },
];

function gh(args, opts = {}) {
  return execFileSync("gh", args, { encoding: "utf8", ...opts });
}

// List assets {name, url} for the version release.
const release = JSON.parse(gh(["release", "view", tag, "--repo", REPO, "--json", "assets"]));
const assets = release.assets.map((a) => ({ name: a.name, url: a.url }));

const tmp = mkdtempSync(join(tmpdir(), "yeson-updater-"));
const outFiles = [];

for (const app of APPS) {
  const platforms = {};
  for (const plat of [app.mac, app.win]) {
    const bundle = assets.find((a) => a.name.startsWith(app.prefix) && a.name.endsWith(plat.suffix));
    const sig = assets.find((a) => a.name.startsWith(app.prefix) && a.name.endsWith(`${plat.suffix}.sig`));
    if (!bundle || !sig) {
      throw new Error(`missing ${plat.target} asset for ${app.prefix} (need *${plat.suffix} + .sig) on ${tag}`);
    }
    // The .sig asset content IS the signature (base64); download it to read.
    gh(["release", "download", tag, "--repo", REPO, "--pattern", sig.name, "--dir", tmp, "--clobber"]);
    const signature = readFileSync(join(tmp, sig.name), "utf8").trim();
    platforms[plat.target] = { signature, url: bundle.url };
  }
  const manifest = {
    version,
    pub_date: new Date().toISOString(),
    notes: `yeson-meet ${version}`,
    platforms,
  };
  const path = join(tmp, app.manifest);
  writeFileSync(path, `${JSON.stringify(manifest, null, 2)}\n`);
  outFiles.push(path);
  console.log(`built ${app.manifest}:\n${JSON.stringify(manifest, null, 2)}`);
}

// Overwrite the channel assets in place.
gh(["release", "upload", CHANNEL, ...outFiles, "--repo", REPO, "--clobber"]);
console.log(`published ${outFiles.map((f) => f.split("/").pop()).join(", ")} to ${CHANNEL}`);
```

Note on `pub_date`: this script stamps the publish time at run; it is informational only (the updater compares `version`, not date).

- [ ] **Step 3: Smoke-test the script's argument guard (no network)**

Run: `node scripts/publish-updater-manifest.mjs 2>&1 | head -1`
Expected: `usage: node scripts/publish-updater-manifest.mjs <version>  (e.g. 0.9.7)`

- [ ] **Step 4: Document the release runbook**

Append to `docs/INSTALL.md` a section:
```markdown
## 자동 업데이트 (Auto-update)

각 앱은 시작 시 GitHub의 `updater-latest` 채널에서 새 버전을 확인하고, 있으면 상단
배너로 알리며 한 번의 클릭으로 다운로드·설치·재시작합니다.

### 새 버전 릴리스 절차 (메인테이너)
1. 두 앱의 `src-tauri/tauri.conf.json` `version`을 올린다 (필요 시 4개 릴리스 워크플로의
   하드코딩 `tag_name`/`name`도 동일 버전으로 갱신).
2. 4개 워크플로(mac/win × 서버/클라)를 실행해 서명된 설치본 + `*.app.tar.gz`/`-setup.exe`
   (+ `.sig`)를 `v<버전>` prerelease에 업로드한다. (서명 비밀키는 GitHub Secrets
   `TAURI_SIGNING_PRIVATE_KEY` / `..._PASSWORD`.)
3. 네 빌드가 모두 끝나면 매니페스트를 발행한다:
   `node scripts/publish-updater-manifest.mjs <버전>`
4. 구버전 앱을 실행해 배너 → [지금 업데이트] → 재시작 후 새 버전인지 확인한다.

미서명 빌드라 첫 수동 설치는 mac "우클릭 열기" / win .msi(SmartScreen 회피)를 따른다.
자동 업데이트는 앱이 직접 설치하므로 그 단계가 없을 것으로 예상되나, mac/win 실기기에서
1회 검증한다.
```

- [ ] **Step 5: Commit**

```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet
git add scripts/publish-updater-manifest.mjs docs/INSTALL.md
git commit -m "ci: updater manifest publisher + updater-latest channel + release runbook"
```

---

## Manual E2E Verification (after all tasks + a real release)

Not unit-testable; run once on real devices (matches the project's device-verification practice).

1. Bump versions, run the 4 workflows for `v<new>`, run `publish-updater-manifest.mjs <new>`.
2. Install the PREVIOUS version (mac: right-click Open; win: `.msi`).
3. Launch it → confirm the banner shows `새 버전 v<new> 사용 가능`.
4. Click **지금 업데이트** → progress → app relaunches → version line shows `v<new>`.
5. macOS: confirm no repeated Gatekeeper "우클릭 열기" prompt after the updater-installed relaunch.
6. Windows: confirm no SmartScreen block during the updater-driven NSIS install.

Record results in `docs/INSTALL.md` or the relevant memory note.

## Spec Coverage Check

- Startup check + notify + one-click install → Tasks 2/3 (server), 5/6 (client).
- Both apps → Tasks 2-4 (server), 5-7 (client).
- updater plugin + process(relaunch) + capabilities → Tasks 4, 7.
- minisign signing key, pubkey committed, private in Secrets → Tasks 1, 8.
- `createUpdaterArtifacts`, endpoints, `updater-latest` moving tag → Tasks 4, 7, 9.
- CI signs + uploads updater artifacts (4 workflows) → Task 8.
- Manifest publisher (local script) → Task 9.
- Platform keys darwin-aarch64 / windows-x86_64; NSIS-for-updater / MSI-for-manual → Tasks 8, 9.
- Unsigned-app caveats (Gatekeeper/SmartScreen) → Manual E2E + docs (Task 9).
