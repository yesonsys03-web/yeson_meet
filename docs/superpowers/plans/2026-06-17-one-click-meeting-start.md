# One-Click Meeting Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the 6-step meeting-start flow into one `회의 시작` button by persisting operator credentials + Device API Key in the OS keychain.

**Architecture:** A Rust `credentials` module stores a single JSON blob in the OS keychain (`keyring` crate; Secret Service / Keychain / Credential Manager). The long-lived Device API Key is resolved inside `start_sidecar` and never transits JS. The desktop UI gets a two-mode hero card (register-once vs everyday one-click) driving a pure `runOneClickStart` orchestrator (login → create session → start sidecar). Existing detailed panels are folded into a collapsed `고급 설정` section.

**Tech Stack:** Rust + Tauri 2, `keyring` v3, React 18 + TypeScript, Vitest, `cargo test`.

**Conventions:** This repo uses VibeLign anchors. Keep edits inside existing `// === ANCHOR: NAME_START/END ===` spans, and give every new file its own top-level anchor pair (see code blocks). Run all `cargo` commands from `apps/desktop/src-tauri` and all `vitest` commands from `apps/desktop`.

**Design spec:** `docs/superpowers/specs/2026-06-17-one-click-meeting-start-design.md`

---

## File Structure

**Rust (`apps/desktop/src-tauri/`):**
- Create `src/credentials.rs` — keychain I/O + pure projection/selection logic + 4 Tauri commands.
- Modify `Cargo.toml` — add `keyring` dependency.
- Modify `src/lib.rs` — register `mod credentials;` and the new commands.
- Modify `src/sidecar.rs` — resolve Device API Key from the keychain when the request key is empty.

**TypeScript (`apps/desktop/src/`):**
- Create `setup/credentials.ts` — `invoke` wrappers + no-Tauri fallback.
- Create `setup/credentials.test.ts` — fallback behavior.
- Create `console/meetingTitle.ts` — pure auto-title formatter.
- Create `console/meetingTitle.test.ts` — formatter test.
- Create `console/oneClickStart.ts` — pure orchestrator with injected deps.
- Create `console/oneClickStart.test.ts` — order + sidecar-failure path.
- Modify `console/useMeetingLifecycle.ts` — add `startMeetingOneClick`.
- Modify `setup/sidecarRunner.ts` — drop the JS device-key hard requirement.
- Modify `setup/MeetingQuickStartPanel.tsx` — two-mode hero card.
- Modify `setup/SetupAssistant.tsx` — fold detailed panels into `<details>`; drop device-key from the manual checklist.

---

## Task 1: Rust credentials module (keychain + pure logic)

**Files:**
- Modify: `apps/desktop/src-tauri/Cargo.toml`
- Create: `apps/desktop/src-tauri/src/credentials.rs`

- [ ] **Step 1: Add the `keyring` dependency**

In `apps/desktop/src-tauri/Cargo.toml`, under `[dependencies]` (after the `serde_json = "1"` line), add:

```toml
keyring = { version = "3", features = ["apple-native", "windows-native", "sync-secret-service", "crypto-rust"] }
```

(`apple-native`/`windows-native` are selected per-target; `sync-secret-service` + `crypto-rust` give a pure-Rust Linux backend with no OpenSSL/C build dependency.)

- [ ] **Step 2: Create `src/credentials.rs` with the module + failing-by-absence tests**

```rust
// === ANCHOR: CREDENTIALS_START ===
//! OS-keychain credential store for one-click meeting start.
//!
//! A single JSON blob (server address + operator email/password + Device API
//! Key) is stored under one keychain entry. The pure projection/selection
//! helpers (`Credentials::to_meta`, `choose_device_key`) hold all the logic and
//! are unit-tested; the `keyring` I/O is thin glue (integration-tested by hand).
use keyring::Entry;
use serde::{Deserialize, Serialize};

const SERVICE: &str = "yeson-meet";
const ACCOUNT: &str = "operator-credentials";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Credentials {
    pub server_ws_base: String,
    pub email: String,
    pub password: String,
    pub device_api_key: String,
}

/// Non-secret projection returned to the UI. Never carries the password or the
/// Device API Key — only whether a key is present.
#[derive(Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CredentialsMeta {
    pub has_credentials: bool,
    pub server_ws_base: String,
    pub email: String,
    pub has_device_key: bool,
}

/// Transient login bundle handed to JS only to perform the operator login.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OperatorLogin {
    pub server_ws_base: String,
    pub email: String,
    pub password: String,
}

impl Credentials {
    pub fn to_meta(&self) -> CredentialsMeta {
        CredentialsMeta {
            has_credentials: true,
            server_ws_base: self.server_ws_base.clone(),
            email: self.email.clone(),
            has_device_key: !self.device_api_key.trim().is_empty(),
        }
    }
}

pub fn empty_meta() -> CredentialsMeta {
    CredentialsMeta {
        has_credentials: false,
        server_ws_base: String::new(),
        email: String::new(),
        has_device_key: false,
    }
}

/// Pick the Device API Key for a sidecar start: prefer an explicit request key,
/// otherwise fall back to the stored key. Pure so it can be unit-tested without
/// touching the keychain.
pub fn choose_device_key(request_key: &str, stored: Option<&str>) -> Result<String, String> {
    let trimmed = request_key.trim();
    if !trimmed.is_empty() {
        if trimmed.contains('<') {
            return Err("YESON_DEVICE_API_KEY is required before starting the sidecar".to_string());
        }
        return Ok(trimmed.to_string());
    }
    match stored.map(str::trim).filter(|key| !key.is_empty()) {
        Some(key) => Ok(key.to_string()),
        None => Err("no Device API Key found — register credentials or paste a key".to_string()),
    }
}

fn entry() -> Result<Entry, String> {
    Entry::new(SERVICE, ACCOUNT).map_err(|error| format!("keychain unavailable: {error}"))
}

fn load() -> Result<Option<Credentials>, String> {
    match entry()?.get_password() {
        Ok(json) => {
            let creds: Credentials =
                serde_json::from_str(&json).map_err(|error| format!("corrupt credentials: {error}"))?;
            Ok(Some(creds))
        }
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(format!("keychain read failed: {error}")),
    }
}

pub fn save(creds: &Credentials) -> Result<(), String> {
    let json = serde_json::to_string(creds).map_err(|error| error.to_string())?;
    entry()?
        .set_password(&json)
        .map_err(|error| format!("keychain write failed: {error}"))
}

pub fn clear() -> Result<(), String> {
    match entry()?.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(error) => Err(format!("keychain delete failed: {error}")),
    }
}

pub fn meta() -> Result<CredentialsMeta, String> {
    Ok(load()?.map(|creds| creds.to_meta()).unwrap_or_else(empty_meta))
}

pub fn operator_login() -> Result<OperatorLogin, String> {
    let creds = load()?.ok_or_else(|| "no stored credentials".to_string())?;
    Ok(OperatorLogin {
        server_ws_base: creds.server_ws_base,
        email: creds.email,
        password: creds.password,
    })
}

/// Resolve the Device API Key for `start_sidecar`: explicit request key wins,
/// else the stored key. Reads the keychain only when the request key is empty.
pub fn resolve_device_key(request_key: &str) -> Result<String, String> {
    if !request_key.trim().is_empty() {
        return choose_device_key(request_key, None);
    }
    let stored = load()?.map(|creds| creds.device_api_key);
    choose_device_key(request_key, stored.as_deref())
}

#[tauri::command]
pub fn save_credentials(request: Credentials) -> Result<(), String> {
    save(&request)
}

#[tauri::command]
pub fn clear_credentials() -> Result<(), String> {
    clear()
}

#[tauri::command]
pub fn credentials_meta() -> Result<CredentialsMeta, String> {
    meta()
}

#[tauri::command]
pub fn load_operator_login() -> Result<OperatorLogin, String> {
    operator_login()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn to_meta_hides_password_and_device_key() {
        let creds = Credentials {
            server_ws_base: "wss://host".to_string(),
            email: "op@yeson.local".to_string(),
            password: "s3cret".to_string(),
            device_api_key: "dev-key".to_string(),
        };
        let meta = creds.to_meta();
        assert_eq!(meta.has_credentials, true);
        assert_eq!(meta.server_ws_base, "wss://host");
        assert_eq!(meta.email, "op@yeson.local");
        assert_eq!(meta.has_device_key, true);
    }

    #[test]
    fn to_meta_reports_missing_device_key() {
        let creds = Credentials {
            server_ws_base: "wss://host".to_string(),
            email: "op@yeson.local".to_string(),
            password: "s3cret".to_string(),
            device_api_key: "   ".to_string(),
        };
        assert_eq!(creds.to_meta().has_device_key, false);
    }

    #[test]
    fn choose_device_key_prefers_request_key() {
        assert_eq!(choose_device_key("req-key", Some("stored")), Ok("req-key".to_string()));
    }

    #[test]
    fn choose_device_key_falls_back_to_stored() {
        assert_eq!(choose_device_key("  ", Some("stored")), Ok("stored".to_string()));
    }

    #[test]
    fn choose_device_key_rejects_placeholder() {
        assert!(choose_device_key("<plaintext-device-key>", Some("stored")).is_err());
    }

    #[test]
    fn choose_device_key_errors_when_nothing_available() {
        assert!(choose_device_key("", None).is_err());
    }
}
// === ANCHOR: CREDENTIALS_END ===
```

- [ ] **Step 3: Register the module so it compiles**

In `apps/desktop/src-tauri/src/lib.rs`, change the module declarations at the top (currently `mod diagnostics;` / `mod sidecar;`) to also include credentials:

```rust
mod credentials;
mod diagnostics;
mod sidecar;
```

- [ ] **Step 4: Run the Rust tests**

Run: `cd apps/desktop/src-tauri && cargo test credentials`
Expected: the 6 `credentials::tests::*` tests PASS, build succeeds.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src-tauri/Cargo.toml apps/desktop/src-tauri/src/credentials.rs apps/desktop/src-tauri/src/lib.rs
git commit -m "feat(desktop): keychain credential store + pure selection logic"
```

---

## Task 2: Register commands + resolve Device Key in start_sidecar

**Files:**
- Modify: `apps/desktop/src-tauri/src/lib.rs:11-17` (invoke_handler list)
- Modify: `apps/desktop/src-tauri/src/sidecar.rs` (start_sidecar body + validate_request)

- [ ] **Step 1: Register the four new commands**

In `apps/desktop/src-tauri/src/lib.rs`, extend the `tauri::generate_handler!` list so it reads:

```rust
        .invoke_handler(tauri::generate_handler![
            diagnostics::save_app_log,
            sidecar::start_sidecar,
            sidecar::stop_sidecar,
            sidecar::sidecar_status,
            sidecar::open_screen_recording_settings,
            credentials::save_credentials,
            credentials::clear_credentials,
            credentials::credentials_meta,
            credentials::load_operator_login,
        ])
```

- [ ] **Step 2: Resolve the Device Key from the keychain in `start_sidecar`**

In `apps/desktop/src-tauri/src/sidecar.rs`, inside `start_sidecar`, immediately after `*child_slot = None;` (line ~176, before the `let (mut child, detail) = match locate_bundled_sidecar()` block), insert:

```rust
    let device_api_key = crate::credentials::resolve_device_key(&request.device_api_key)?;
```

Then in BOTH match arms, replace the env line
`.env("YESON_DEVICE_API_KEY", request.device_api_key.trim())`
with
`.env("YESON_DEVICE_API_KEY", device_api_key.trim())`
(there are two occurrences — the bundled arm ~line 192 and the dev/uv arm ~line 224).

- [ ] **Step 3: Stop hard-requiring the Device Key in `validate_request`**

In `apps/desktop/src-tauri/src/sidecar.rs`, change `validate_request` (currently lines ~373-378) to drop the device-key line, since the key is now resolved (and validated) by `resolve_device_key`:

```rust
fn validate_request(request: &SidecarStartRequest) -> Result<(), String> {
    require_value("SERVER_WS_BASE", &request.server_ws_base)?;
    require_value("YESON_SESSION_ID", &request.session_id)?;
    Ok(())
}
```

- [ ] **Step 4: Build to verify it compiles**

Run: `cd apps/desktop/src-tauri && cargo build`
Expected: build succeeds (no warnings about unused `credentials`).

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src-tauri/src/lib.rs apps/desktop/src-tauri/src/sidecar.rs
git commit -m "feat(desktop): register credential commands, resolve Device Key from keychain"
```

---

## Task 3: TypeScript credentials client

**Files:**
- Create: `apps/desktop/src/setup/credentials.ts`
- Test: `apps/desktop/src/setup/credentials.test.ts`

- [ ] **Step 1: Write the failing test**

Create `apps/desktop/src/setup/credentials.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { EMPTY_META, loadCredentialsMeta } from "./credentials";

describe("loadCredentialsMeta", () => {
  it("returns EMPTY_META when there is no Tauri runtime", async () => {
    // jsdom has no __TAURI_INTERNALS__, so invoke must not be reached.
    await expect(loadCredentialsMeta()).resolves.toEqual(EMPTY_META);
  });

  it("EMPTY_META carries no credentials", () => {
    expect(EMPTY_META.hasCredentials).toBe(false);
    expect(EMPTY_META.hasDeviceKey).toBe(false);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/desktop && npx vitest run src/setup/credentials.test.ts`
Expected: FAIL — cannot resolve `./credentials`.

- [ ] **Step 3: Create the client**

Create `apps/desktop/src/setup/credentials.ts`:

```ts
// === ANCHOR: CREDENTIALS_CLIENT_START ===
import { invoke } from "@tauri-apps/api/core";

export type CredentialsInput = {
  serverWsBase: string;
  email: string;
  password: string;
  deviceApiKey: string;
};

export type CredentialsMeta = {
  hasCredentials: boolean;
  serverWsBase: string;
  email: string;
  hasDeviceKey: boolean;
};

export type OperatorLogin = {
  serverWsBase: string;
  email: string;
  password: string;
};

export const EMPTY_META: CredentialsMeta = {
  hasCredentials: false,
  serverWsBase: "",
  email: "",
  hasDeviceKey: false,
};

type TauriWindow = Window & { __TAURI_INTERNALS__?: unknown };

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

export async function saveCredentials(request: CredentialsInput): Promise<void> {
  await invoke("save_credentials", { request });
}

export async function clearCredentials(): Promise<void> {
  await invoke("clear_credentials");
}

export async function loadCredentialsMeta(): Promise<CredentialsMeta> {
  if (!hasTauriRuntime()) return EMPTY_META;
  try {
    return await invoke<CredentialsMeta>("credentials_meta");
  } catch {
    return EMPTY_META;
  }
}

export async function loadOperatorLogin(): Promise<OperatorLogin> {
  return invoke<OperatorLogin>("load_operator_login");
}
// === ANCHOR: CREDENTIALS_CLIENT_END ===
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/desktop && npx vitest run src/setup/credentials.test.ts`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/setup/credentials.ts apps/desktop/src/setup/credentials.test.ts
git commit -m "feat(desktop): TS credential client with no-Tauri fallback"
```

---

## Task 4: Auto meeting-title formatter

**Files:**
- Create: `apps/desktop/src/console/meetingTitle.ts`
- Test: `apps/desktop/src/console/meetingTitle.test.ts`

- [ ] **Step 1: Write the failing test**

Create `apps/desktop/src/console/meetingTitle.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { formatMeetingTitle } from "./meetingTitle";

describe("formatMeetingTitle", () => {
  it("formats date and time with zero-padding", () => {
    // Month is 0-based: 5 === June.
    expect(formatMeetingTitle(new Date(2026, 5, 17, 9, 5))).toBe("2026-06-17 09:05 회의");
  });

  it("pads two-digit hours and minutes", () => {
    expect(formatMeetingTitle(new Date(2026, 11, 1, 14, 30))).toBe("2026-12-01 14:30 회의");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/desktop && npx vitest run src/console/meetingTitle.test.ts`
Expected: FAIL — cannot resolve `./meetingTitle`.

- [ ] **Step 3: Create the formatter**

Create `apps/desktop/src/console/meetingTitle.ts`:

```ts
// === ANCHOR: MEETING_TITLE_START ===
/// Auto-generated meeting title for one-click start: "YYYY-MM-DD HH:mm 회의".
/// Operators can rename it afterwards.
export function formatMeetingTitle(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  const ymd = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const hm = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return `${ymd} ${hm} 회의`;
}
// === ANCHOR: MEETING_TITLE_END ===
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/desktop && npx vitest run src/console/meetingTitle.test.ts`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/console/meetingTitle.ts apps/desktop/src/console/meetingTitle.test.ts
git commit -m "feat(desktop): auto meeting-title formatter"
```

---

## Task 5: Pure one-click orchestrator

**Files:**
- Create: `apps/desktop/src/console/oneClickStart.ts`
- Test: `apps/desktop/src/console/oneClickStart.test.ts`

This is the login → create-session → start-sidecar sequence, extracted as a pure async function with injected dependencies so it is unit-testable without React (matching the repo's pure-function test style).

- [ ] **Step 1: Write the failing test**

Create `apps/desktop/src/console/oneClickStart.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";

import { runOneClickStart, type OneClickDeps } from "./oneClickStart";
import type { CreatedSession } from "./types";

const session: CreatedSession = { session_id: "sess-1", viewer_url: "https://host/v/tok" };

function baseDeps(overrides: Partial<OneClickDeps> = {}): OneClickDeps {
  return {
    loadOperatorLogin: vi.fn().mockResolvedValue({ serverWsBase: "wss://host", email: "op@x", password: "pw" }),
    login: vi.fn().mockResolvedValue("token-abc"),
    createSession: vi.fn().mockResolvedValue(session),
    startSidecar: vi.fn().mockResolvedValue(undefined),
    now: () => new Date(2026, 5, 17, 9, 5),
    ...overrides,
  };
}

describe("runOneClickStart", () => {
  it("logs in, creates the session with an auto title, then starts the sidecar", async () => {
    const deps = baseDeps();
    const result = await runOneClickStart(deps);

    expect(deps.login).toHaveBeenCalledWith("op@x", "pw");
    expect(deps.createSession).toHaveBeenCalledWith({ title: "2026-06-17 09:05 회의", operatorToken: "token-abc" });
    expect(deps.startSidecar).toHaveBeenCalledWith({ serverWsBase: "wss://host", sessionId: "sess-1" });
    expect(result).toEqual({ session, operatorToken: "token-abc", title: "2026-06-17 09:05 회의", sidecarStarted: true });
  });

  it("keeps the created session when the sidecar fails to start", async () => {
    const deps = baseDeps({ startSidecar: vi.fn().mockRejectedValue(new Error("boom")) });
    const result = await runOneClickStart(deps);

    expect(result.session).toEqual(session);
    expect(result.sidecarStarted).toBe(false);
    expect(result.sidecarError).toBe("boom");
  });

  it("does not create a session when login fails", async () => {
    const createSession = vi.fn();
    const deps = baseDeps({ login: vi.fn().mockRejectedValue(new Error("bad creds")), createSession });
    await expect(runOneClickStart(deps)).rejects.toThrow("bad creds");
    expect(createSession).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/desktop && npx vitest run src/console/oneClickStart.test.ts`
Expected: FAIL — cannot resolve `./oneClickStart`.

- [ ] **Step 3: Create the orchestrator**

Create `apps/desktop/src/console/oneClickStart.ts`:

```ts
// === ANCHOR: ONE_CLICK_START_START ===
import { formatMeetingTitle } from "./meetingTitle";
import type { CreatedSession } from "./types";

export type OneClickDeps = {
  loadOperatorLogin: () => Promise<{ serverWsBase: string; email: string; password: string }>;
  login: (email: string, password: string) => Promise<string>;
  createSession: (input: { title: string; operatorToken: string }) => Promise<CreatedSession>;
  startSidecar: (input: { serverWsBase: string; sessionId: string }) => Promise<void>;
  now: () => Date;
};

export type OneClickResult = {
  session: CreatedSession;
  operatorToken: string;
  title: string;
  sidecarStarted: boolean;
  sidecarError?: string;
};

/// Run the everyday one-click sequence. Login/create failures propagate (no
/// session is created). A sidecar failure is captured, not thrown, so the
/// created session survives and the caller can offer "회의 종료".
export async function runOneClickStart(deps: OneClickDeps): Promise<OneClickResult> {
  const { serverWsBase, email, password } = await deps.loadOperatorLogin();
  const operatorToken = await deps.login(email, password);
  const title = formatMeetingTitle(deps.now());
  const session = await deps.createSession({ title, operatorToken });

  try {
    await deps.startSidecar({ serverWsBase, sessionId: session.session_id });
    return { session, operatorToken, title, sidecarStarted: true };
  } catch (error) {
    return {
      session,
      operatorToken,
      title,
      sidecarStarted: false,
      sidecarError: error instanceof Error ? error.message : String(error),
    };
  }
}
// === ANCHOR: ONE_CLICK_START_END ===
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/desktop && npx vitest run src/console/oneClickStart.test.ts`
Expected: PASS (all three tests).

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/console/oneClickStart.ts apps/desktop/src/console/oneClickStart.test.ts
git commit -m "feat(desktop): pure one-click meeting-start orchestrator"
```

---

## Task 6: Relax the JS device-key requirement in sidecarRunner

**Files:**
- Modify: `apps/desktop/src/setup/sidecarRunner.ts:39-49` (validateSidecarValues)

Since `start_sidecar` (Rust) now resolves the Device Key from the keychain when the request key is empty, the JS layer must stop blocking an empty key.

- [ ] **Step 1: Drop the device-key check in `validateSidecarValues`**

In `apps/desktop/src/setup/sidecarRunner.ts`, replace the `validateSidecarValues` function body so it no longer requires `deviceApiKey`:

```ts
function validateSidecarValues(values: SetupValues): void {
  if (!values.sessionId.trim() || values.sessionId.includes("<")) {
    throw new Error("Live Meeting에서 회의를 만든 뒤 생성된 Session ID가 필요합니다.");
  }
  if (!values.serverWsBase.trim() || values.serverWsBase.includes("<")) {
    throw new Error("WebSocket 서버 주소를 입력하세요. 로컬 테스트는 ws://127.0.0.1:8000, LAN 테스트는 wss://192.168.0.38 입니다.");
  }
}
```

(The Device Key is still passed through `values.deviceApiKey` when the manual form supplies one; an empty value now means "use the keychain key", validated by Rust.)

- [ ] **Step 2: Verify the build typechecks**

Run: `cd apps/desktop && npx tsc --noEmit`
Expected: PASS (no type errors).

- [ ] **Step 3: Commit**

```bash
git add apps/desktop/src/setup/sidecarRunner.ts
git commit -m "refactor(desktop): let keychain supply the Device Key on sidecar start"
```

---

## Task 7: Add `startMeetingOneClick` to useMeetingLifecycle

**Files:**
- Modify: `apps/desktop/src/console/useMeetingLifecycle.ts`

- [ ] **Step 1: Add imports**

In `apps/desktop/src/console/useMeetingLifecycle.ts`, extend the imports at the top:

```ts
import { loadValues, storeValues } from "../setup/setupValues";
import { loadOperatorLogin } from "../setup/credentials";
import { startSidecar } from "../setup/sidecarRunner";
import { createSession, endSession, fetchSessionReport, loginOperator, sessionRequestBody } from "./sessionApi";
import { runOneClickStart } from "./oneClickStart";
import type { CreatedSession, EndedSession, MeetingDraft } from "./types";
```

- [ ] **Step 2: Add the `startMeetingOneClick` action**

In the same file, add this function next to `startMeeting` (inside the hook, before the `return`):

```ts
  async function startMeetingOneClick() {
    await runAction(async () => {
      const result = await runOneClickStart({
        loadOperatorLogin,
        login: async (email, password) => (await loginOperator(email, password)).access_token,
        createSession: ({ title, operatorToken }) => createSession({ ...draft, title, operatorToken }),
        startSidecar: async ({ serverWsBase, sessionId }) => {
          await startSidecar({ ...loadValues(), serverWsBase, sessionId, deviceApiKey: "" });
        },
        now: () => new Date(),
      });
      setCreatedSession(result.session);
      setEndedSession(null);
      setReportText("");
      updateDraft("operatorToken", result.operatorToken);
      updateDraft("title", result.title);
      storeSessionHandoff(result.session);
      if (result.sidecarStarted) {
        setStatusText(`회의 시작 완료: ${result.session.session_id}`);
      } else {
        setErrorText(
          `회의는 생성됐지만 sidecar 시작에 실패했습니다: ${result.sidecarError ?? ""} — 필요하면 '회의 종료'를 누르세요.`,
        );
      }
    });
  }
```

- [ ] **Step 3: Export it from the hook**

In the `return { ... }` object of `useMeetingLifecycle`, add `startMeetingOneClick,` alongside `startMeeting,`.

- [ ] **Step 4: Verify the build typechecks**

Run: `cd apps/desktop && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/console/useMeetingLifecycle.ts
git commit -m "feat(desktop): wire startMeetingOneClick into useMeetingLifecycle"
```

---

## Task 8: Two-mode one-click hero card

**Files:**
- Modify: `apps/desktop/src/setup/MeetingQuickStartPanel.tsx` (full rewrite of the panel)

Register-once mode (no stored credentials): four fields + `기억하고 회의 시작`. Everyday mode (credentials present): big `회의 시작` button + non-secret metadata + `자격증명 변경`. While a meeting is live the button toggles to `회의 종료`. Reuses existing `styles` keys — no `styles.ts` change.

- [ ] **Step 1: Rewrite the panel**

Replace the entire contents of `apps/desktop/src/setup/MeetingQuickStartPanel.tsx` with:

```tsx
// === ANCHOR: MEETING_QUICK_START_PANEL_START ===
import { useEffect, useState } from "react";
import { LiveSubtitlePreview } from "../console/LiveSubtitlePreview";
import { useMeetingLifecycle } from "../console/useMeetingLifecycle";
import { EMPTY_META, loadCredentialsMeta, saveCredentials, type CredentialsMeta } from "./credentials";
import { loadValues } from "./setupValues";
import { styles } from "./styles";

export function MeetingQuickStartPanel() {
  const lifecycle = useMeetingLifecycle();
  const [meta, setMeta] = useState<CredentialsMeta>(EMPTY_META);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(() => ({
    serverWsBase: loadValues().serverWsBase,
    email: "admin@yeson.local",
    password: "",
    deviceApiKey: "",
  }));
  const activeSessionId = lifecycle.createdSession?.session_id ?? null;
  const registered = meta.hasCredentials && !editing;

  useEffect(() => {
    void refreshMeta();
  }, []);

  async function refreshMeta() {
    const next = await loadCredentialsMeta();
    setMeta(next);
    setForm((current) => ({
      ...current,
      serverWsBase: next.serverWsBase || current.serverWsBase,
      email: next.email || current.email,
    }));
  }

  async function registerAndStart() {
    await saveCredentials(form);
    await refreshMeta();
    setEditing(false);
    await lifecycle.startMeetingOneClick();
  }

  return (
    <section style={styles.quickStartPanel}>
      <div style={styles.quickStartHeader}>
        <div>
          <p style={styles.eyebrow}>one-click meeting start</p>
          <h2 style={styles.quickStartTitle}>버튼 하나로 회의 시작</h2>
          <p style={styles.quickStartIntro}>
            한 번 자격증명을 등록하면, 다음부터는 로그인·회의 생성·sidecar 실행이 자동으로 진행됩니다.
          </p>
        </div>
        <div style={styles.quickStartSteps}>
          <span>로그인</span>
          <span>회의 생성</span>
          <span>Sidecar</span>
        </div>
      </div>

      <div style={styles.quickStartSubtitleDock}>
        <LiveSubtitlePreview operatorToken={lifecycle.draft.operatorToken} sessionId={activeSessionId} />
      </div>

      <div style={styles.quickStartGrid}>
        <div style={styles.quickStartCard}>
          {registered ? (
            <>
              <h3 style={styles.quickStartCardTitle}>준비 완료</h3>
              {activeSessionId ? (
                <button type="button" onClick={lifecycle.finishMeeting} disabled={lifecycle.busy} style={styles.primaryButton}>
                  회의 종료
                </button>
              ) : (
                <button type="button" onClick={lifecycle.startMeetingOneClick} disabled={lifecycle.busy} style={styles.primaryButton}>
                  {lifecycle.busy ? "회의 시작 중..." : "회의 시작"}
                </button>
              )}
              <div style={styles.quickStartSessionBox}>
                <span>서버</span>
                <strong>{meta.serverWsBase || "(미설정)"}</strong>
              </div>
              <div style={styles.quickStartSessionBox}>
                <span>운영자</span>
                <strong>{meta.email || "(미설정)"}</strong>
              </div>
              <div style={styles.quickStartSessionBox}>
                <span>Device Key</span>
                <strong>{meta.hasDeviceKey ? "저장됨 ✓" : "없음"}</strong>
              </div>
              <button type="button" onClick={() => setEditing(true)} style={styles.secondaryLightButton}>
                자격증명 변경
              </button>
            </>
          ) : (
            <>
              <h3 style={styles.quickStartCardTitle}>처음 한 번만 등록</h3>
              <QuickField label="WebSocket 서버 주소" value={form.serverWsBase} onChange={(value) => setForm((c) => ({ ...c, serverWsBase: value }))} />
              <QuickField label="Operator email" value={form.email} type="email" onChange={(value) => setForm((c) => ({ ...c, email: value }))} />
              <QuickField label="Operator password" value={form.password} type="password" onChange={(value) => setForm((c) => ({ ...c, password: value }))} />
              <QuickField label="Device API Key" value={form.deviceApiKey} type="password" onChange={(value) => setForm((c) => ({ ...c, deviceApiKey: value }))} />
              <div style={styles.quickStartActions}>
                <button type="button" onClick={registerAndStart} disabled={lifecycle.busy} style={styles.primaryButton}>
                  기억하고 회의 시작
                </button>
                {meta.hasCredentials ? (
                  <button type="button" onClick={() => setEditing(false)} disabled={lifecycle.busy} style={styles.secondaryLightButton}>
                    취소
                  </button>
                ) : null}
              </div>
            </>
          )}
          <div style={lifecycle.errorText ? styles.quickStartError : styles.quickStartStatus}>
            {lifecycle.errorText || lifecycle.statusText}
          </div>
        </div>

        <div style={styles.quickStartCardDark}>
          <h3 style={styles.quickStartCardTitleDark}>생성된 회의</h3>
          {lifecycle.createdSession ? (
            <>
              <div style={styles.quickStartSessionBox}>
                <span>Session ID</span>
                <strong>{lifecycle.createdSession.session_id}</strong>
              </div>
              <div style={styles.quickStartSessionBox}>
                <span>Viewer URL</span>
                <strong>{lifecycle.createdSession.viewer_url}</strong>
              </div>
              <button type="button" onClick={lifecycle.copyViewerUrl} style={styles.secondaryButton}>
                Viewer URL 복사
              </button>
            </>
          ) : (
            <p style={styles.quickStartEmpty}>회의를 시작하면 Session ID와 Viewer URL이 여기에 표시됩니다.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function QuickField({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
}) {
  return (
    <label style={styles.field}>
      <span style={styles.label}>{label}</span>
      <input type={type} value={value} onChange={(event) => onChange(event.currentTarget.value)} style={styles.input} />
    </label>
  );
}
// === ANCHOR: MEETING_QUICK_START_PANEL_END ===
```

- [ ] **Step 2: Verify the build typechecks**

Run: `cd apps/desktop && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add apps/desktop/src/setup/MeetingQuickStartPanel.tsx
git commit -m "feat(desktop): two-mode one-click meeting hero card"
```

---

## Task 9: Fold detailed panels into 고급 설정

**Files:**
- Modify: `apps/desktop/src/setup/SetupAssistant.tsx` (wrap the detailed panels)
- Modify: `apps/desktop/src/setup/SidecarRunnerPanel.tsx:84-90` (drop device-key from the checklist)

- [ ] **Step 1: Drop the device-key item from the manual sidecar checklist**

In `apps/desktop/src/setup/SidecarRunnerPanel.tsx`, change `sidecarMissingItems` (currently lines ~84-90) so it no longer lists the Device Key (the keychain can supply it):

```ts
function sidecarMissingItems(values: SetupValues): string[] {
  const items: string[] = [];
  if (!values.sessionId.trim() || values.sessionId.includes("<")) items.push("Live Meeting에서 회의를 만들고 Session ID를 채워야 합니다.");
  if (!values.serverWsBase.trim() || values.serverWsBase.includes("<")) items.push("WebSocket 서버 주소가 필요합니다.");
  return items;
}
```

- [ ] **Step 2: Wrap the detailed panels in a collapsed `<details>`**

In `apps/desktop/src/setup/SetupAssistant.tsx`, in the returned JSX, wrap everything from `<main style={styles.grid}>` through `<SmokeChecklist .../>` (currently lines ~117-168) in a `<details>` so the one-click card stays primary and the manual tools collapse below it:

```tsx
      <MeetingQuickStartPanel />

      <details style={{ marginTop: 24 }}>
        <summary style={styles.sectionTitle}>고급 설정 (수동 실행 · 문제 해결)</summary>

        <main style={styles.grid}>
          {/* ...unchanged "실행 환경 값" section and command panel... */}
        </main>

        <PlatformRunbookPanel platform={values.platform} />

        <SidecarRunnerPanel values={values} />

        <SmokeChecklist checks={checks} onRunAll={runAllSmokeChecks} running={runningChecks} />
      </details>
```

Keep the inner content (the `<main>…</main>` block, `PlatformRunbookPanel`, `SidecarRunnerPanel`, `SmokeChecklist`) exactly as it is today — only the wrapping `<details>`/`<summary>` is added.

- [ ] **Step 3: Verify the build typechecks**

Run: `cd apps/desktop && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/desktop/src/setup/SetupAssistant.tsx apps/desktop/src/setup/SidecarRunnerPanel.tsx
git commit -m "feat(desktop): fold manual setup panels into 고급 설정"
```

---

## Task 10: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full frontend test suite**

Run: `cd apps/desktop && npm test`
Expected: all vitest suites PASS (including the new credentials / meetingTitle / oneClickStart tests).

- [ ] **Step 2: Typecheck the whole frontend**

Run: `cd apps/desktop && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 3: Run the Rust tests + build**

Run: `cd apps/desktop/src-tauri && cargo test && cargo build`
Expected: PASS (credentials tests green, build succeeds on this platform).

- [ ] **Step 4: Manual smoke (operator, by hand)**

This needs a running server + valid operator/device credentials, so it is an operator step, not an automated one:
1. Launch `npm run tauri:dev` from `apps/desktop`.
2. First run: the hero card shows the 4-field register form. Enter server/email/password/Device Key → `기억하고 회의 시작`.
3. Confirm: login + session creation + sidecar start happen with no further clicks, subtitles appear, button shows `회의 종료`.
4. Restart the app → the hero card shows everyday mode (big `회의 시작`, `Device Key 저장됨 ✓`). One click starts a meeting.
5. `자격증명 변경` reopens the form; saving updates the stored values.

- [ ] **Step 5: Final commit (if any verification fixes were needed)**

```bash
git add -A
git commit -m "test(desktop): verify one-click meeting start end-to-end"
```

---

## Notes / Deviations from spec

- **`setupValues.ts` is intentionally left unchanged.** The spec table listed it, but the keychain path is purely additive: secrets live in the keychain via `credentials.ts`, and the non-secret `serverWsBase` already lives in localStorage. No edit is required, so per YAGNI we skip it.
- **No React hook test for `useMeetingLifecycle`.** The repo has no React Testing Library; the testable logic is extracted into the pure `runOneClickStart` (Task 5) instead, matching the existing pure-function test style.
- **Operator password is exposed to JS transiently** for the login call (design-approved). The long-lived Device API Key never leaves Rust — `start_sidecar` reads it from the keychain.
