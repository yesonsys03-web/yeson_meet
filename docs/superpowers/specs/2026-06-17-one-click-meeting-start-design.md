# One-Click Meeting Start — Design

- Date: 2026-06-17
- Status: Approved (brainstorm) → ready for implementation plan
- Area: `apps/desktop` (setup/console), `apps/desktop/src-tauri`

## Problem

Starting a meeting today takes six manual steps every time:

1. Operator email
2. Operator password
3. Login operator
4. Create meeting
5. Paste Device API Key
6. Start sidecar

The root cause is not the UI — it is two deliberate "do not persist secrets"
decisions:

- `setupValues.ts` strips `deviceApiKey` on store and forces it back to `""` on
  load, so the Device API Key must be pasted before every sidecar start.
- The operator password is never stored, and the JWT is re-issued per session,
  so the host re-logs-in every meeting.

This app runs on **trusted in-house LAN meeting-room PCs** (code signing is also
parked for the same reason). That trust boundary lets us persist credentials
securely instead of re-entering them.

## Goal

Everyday flow is **one button** (`회의 시작`) that runs login → create session →
start sidecar automatically. First run registers credentials once.

## Decisions (locked)

- **One-click target**: collapse the three button clicks into one.
- **Credential storage**: OS keychain, encrypted at rest, working on
  **Linux, macOS, and Windows** (Secret Service/libsecret, Keychain, Credential
  Manager). Graceful fallback when no keychain is available.
- **Meeting title**: auto-generated from date/time, editable later.
- **Token strategy**: store operator email + password in the keychain and
  silently re-login on each click. No new server endpoint. (Refresh-token flow
  is a deliberately deferred alternative.)
- **Advanced panels**: keep the existing detailed panels but fold them into a
  collapsed `고급 설정` (`<details>`) section — not removed (still needed for
  debugging / non-keychain environments).

## Architecture

The one-click hero card lives at the top of `SetupAssistant` and has two modes,
chosen by whether credentials exist in the keychain.

```
┌─ First run (not registered) ───────────┐   ┌─ Everyday (registered) ────────────┐
│ Server      [wss://192.168.0.38     ]  │   │   ┌──────────────────────┐         │
│ Op. email   [admin@yeson.local      ]  │   │   │     ▶  회의 시작       │  (big)  │
│ Op. password[••••••••              ]   │   │   └──────────────────────┘         │
│ Device Key  [••••••••••••           ]  │   │ server wss://… · op admin@… ·       │
│   [ 기억하고 회의 시작 ]                │   │ key saved ✓     [자격증명 변경]     │
└─────────────────────────────────────────┘   └─────────────────────────────────────┘
```

- Not registered → 4 fields + `기억하고 회의 시작`: saves to keychain, then runs
  the full flow immediately.
- Registered → one large button + non-secret metadata only (server, email,
  key-saved indicator) + `자격증명 변경` link.
- While a meeting is live, the same button toggles to `회의 종료`.

## Components

| File | Change |
| --- | --- |
| `apps/desktop/src-tauri` (new commands) | `save_credentials`, `clear_credentials`, `credentials_meta` (returns non-secret metadata only) using the `keyring` crate. |
| `apps/desktop/src-tauri/src/sidecar.rs` `start_sidecar` | When `deviceApiKey` arrives empty, read it directly from the keychain. The long-lived Device Key never transits JS. |
| `apps/desktop/src/setup/MeetingQuickStartPanel.tsx` | Rewrite as the two-mode one-click hero card. |
| `apps/desktop/src/console/useMeetingLifecycle.ts` | Add `startMeetingOneClick()` orchestrating login → create → start-sidecar in one handler, with auto-generated title. |
| `apps/desktop/src/setup/setupValues.ts` | Move credentials to keychain access; keep non-secret values (server address, etc.) in localStorage. |
| `apps/desktop/src/setup/SetupAssistant.tsx` | Wrap the detailed panels in a collapsed `고급 설정` `<details>`. |

## Data Flow (everyday one-click)

```
[회의 시작 click]
  → JS: credentials_meta + load operator email/password (keychain, transient)
  → POST /auth/login            → operator token (memory)
  → POST /sessions (auto title) → session_id, viewer_url
  → invoke start_sidecar        → Rust reads Device Key from keychain, starts sidecar
  → live subtitle preview + viewer QR shown, button → "회의 종료"
```

- Long-lived secret (Device Key) is never exposed to JS — Rust keychain →
  sidecar directly.
- Operator password is stored in the keychain and used only transiently for
  login; JS never writes it to disk.

## Error Handling

- Keychain access failure (e.g. headless Linux) → automatically show the
  registration form with an explanatory note; operate for the current session
  only.
- If login / create / start fails, show **which step failed** and offer partial
  rollback (e.g. if the session was created but the sidecar failed to start,
  offer to end the session).
- Invalid credentials → route directly to `자격증명 변경`.

## Testing

- Unit tests for the `useMeetingLifecycle` one-click orchestration: correct
  order (login → create → start), and mid-flow failure handling.
- Rust unit tests for the keychain commands plus the no-keychain fallback path.
- Keep existing pytest / vitest suites green.

## Out of Scope

- Refresh-token / SSO auth (deferred alternative to stored password).
- Server-side device auto-provisioning (Device Key is still issued by an admin
  out of band and registered once).
- Code signing / notarization (parked — in-house only).
