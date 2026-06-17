# Quick Tunnel — Phone Viewer Over the Public Internet

- Date: 2026-06-17
- Status: Approved (design)
- Branch: `topyeson`
- Related: ARCHITECTURE.md §7 (접근 모드 추상화 LAN/Tunnel), DEPLOY.md §12
  (외부 배포 변경점 Phase 5+), ROADMAP `[ ] Tunnel 모드 (Cloudflare Tunnel)` (L346),
  PRD §355 (Web 접근 모드: MVP LAN-only / 확장 Tunnel)

## Problem

Participant phones cannot reach the in-house server over the meeting-room Wi-Fi
(that Wi-Fi has no route to the server's LAN). The current viewer flow assumes
phones are on the same LAN as the server (`VIEWER_BASE=https://<SERVER_IP>`),
so the QR points at a LAN address the phones can't open. The phones do, however,
each have their own cellular internet.

This slice exposes the existing viewer over the public internet via a **Cloudflare
Quick Tunnel** so phones open a public HTTPS URL over cellular and receive live
subtitles. It implements the long-planned "Tunnel 모드" (ARCHITECTURE §7 / ROADMAP
L346) at its lightest, zero-account tier.

The user has chosen the **Quick Tunnel** variant (ephemeral `*.trycloudflare.com`,
no Cloudflare account/domain) and accepted that the per-session viewer token is a
sufficient access gate for internet exposure.

## Why not GitHub (rejected)

GitHub Pages serves only static content and `raw.githubusercontent.com` is
CDN-cached for minutes — it cannot push or low-latency-poll the live subtitle
stream (our target is ~2s). GitHub can host the static UI but not carry live data,
so it does not solve the reachability problem. A network tunnel does.

## Goals

- A participant phone, on cellular internet, opens a public HTTPS URL and sees the
  same live subtitles as a LAN viewer.
- The operator's QR (built from `VIEWER_BASE`) encodes that public URL.
- Public TLS is provided by Cloudflare (real cert) — phones get trusted HTTPS, side-
  stepping the LAN viewer's `tls internal` private-CA trust problem.
- Tunnel is **opt-in**; default LAN deployments are unchanged.
- Near-zero app code change; the work is deploy config + a runbook helper + docs.

## Non-Goals

- Named Tunnel / stable custom domain (requires a Cloudflare account + a domain on
  Cloudflare DNS) — a documented follow-up, not this slice.
- ngrok / Tailscale Funnel alternatives.
- SSO / magic-link / PIN auth in front of the viewer (DEPLOY §12 follow-up; token
  gate is accepted for now).
- The `URLProvider`/`TunnelProvider` Protocol abstraction from ARCHITECTURE §7 — the
  existing `VIEWER_BASE` env already provides the LAN-vs-tunnel switch (YAGNI).
- No app code changes (server / desktop / web). `_viewer_base()` already reads
  `VIEWER_BASE`; `isSecureViewerUrl` already accepts any `https:` URL.

## Architecture / data path

```
phone (cellular) ──HTTPS──▶ Cloudflare edge (public TLS, *.trycloudflare.com)
                              │
                              ▼  cloudflared (egress-only, in compose)
                       Caddy :8080 (plain HTTP, internal docker net)
                       ├─ /            → web SPA (/srv/web, apps/web/dist)
                       ├─ /api/*       → reverse_proxy server:8000
                       └─ /ws/*        → reverse_proxy server:8000  (WebSocket)
```

The web SPA uses relative URLs (`VITE_WS_BASE` empty → `window.location.host`,
see `apps/web/src/lib/api.ts`), so when it is served from the trycloudflare host its
`/api/v1/viewer/utterances` fetch and `/ws/viewer` WebSocket target that same public
origin — same-origin, so no CORS involvement; WebSockets traverse Quick Tunnels
natively. The only thing that must carry the public hostname is the QR/`viewer_url`,
which is built from `VIEWER_BASE` in `apps/server/api/v1/sessions.py:_viewer_base()`.

## Design

### 1. Caddy tunnel origin — `deploy/Caddyfile`

Keep the existing `{$SERVER_HOST}:443` site (LAN, `tls internal`) unchanged. Add a
**plain-HTTP `:8080` site block** that mirrors its routing without TLS (Cloudflare
terminates public TLS):

```caddyfile
# Tunnel origin: plain HTTP for cloudflared (public TLS handled by Cloudflare).
# Only reachable on the internal docker network.
:8080 {
    handle /api/* {
        reverse_proxy server:8000
    }
    handle /ws/* {
        reverse_proxy server:8000
    }
    handle {
        root * /srv/web
        try_files {path} /index.html
        file_server
    }
    encode zstd gzip
}
```

This listener is harmless when the tunnel is not in use (internal-only). Caddy
already mounts `../apps/web/dist:/srv/web:ro` and reaches `server:8000` on the compose
network, so no other Caddy/compose change is needed for it to serve.

### 2. cloudflared service — `deploy/docker-compose.yml`

Add an opt-in service so default LAN deployments never start it:

```yaml
  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    profiles: ["tunnel"]
    command: tunnel --no-autoupdate --url http://caddy:8080
    depends_on:
      - caddy
```

Quick Tunnel needs no token/account. It is reached only via
`docker compose --profile tunnel up -d cloudflared`. On start, cloudflared logs a
line containing `https://<random>.trycloudflare.com`.

### 3. `VIEWER_BASE` wiring — `deploy/tunnel-quick.sh`

A runbook helper (the Quick Tunnel URL is random and only in cloudflared's logs):

1. `docker compose --profile tunnel up -d cloudflared`
2. Poll `docker compose logs cloudflared` until a `https://<...>.trycloudflare.com`
   URL appears (with a timeout + clear error if none); extract it.
3. Upsert `VIEWER_BASE=<url>` into `deploy/.env` (replace any existing `VIEWER_BASE`
   line, else append).
4. `docker compose up -d server` to recreate the server container so `_viewer_base()`
   reads the new `VIEWER_BASE`. This runs at tunnel-up time, before any meeting, so
   no active viewer WS connections are dropped.
5. Print the public URL for the operator.

Script conventions: `#!/usr/bin/env bash`, `set -euo pipefail`, run from `deploy/`,
operate on `deploy/.env`. Manual fallback (read URL from `docker compose logs
cloudflared`, set `VIEWER_BASE`, `docker compose up -d server`) is documented in
DEPLOY.md for when the script's log-parse times out.

Rejected alternative: a `VIEWER_BASE_FILE` indirection so the server reads the URL
live without a restart. It removes the restart but adds server code; the restart is
fast and pre-meeting, so YAGNI — `VIEWER_BASE` env + server recreate is used.

### 4. Env + docs

- `deploy/env.example`: note that `ACCESS_MODE=tunnel` (informational; not read by
  app code) and `VIEWER_BASE` is auto-set by `tunnel-quick.sh` in tunnel mode.
- `docs/DEPLOY.md`: add a "Quick Tunnel (핸드폰 셀룰러 접속)" runbook — prerequisites
  (server has internet egress, which it already needs for Gemini), the one-command
  `tunnel-quick.sh` flow, the manual fallback, teardown
  (`docker compose --profile tunnel down` or stop `cloudflared`), and the security
  note (token-only gate, ephemeral URL valid only while the tunnel runs, in-room QR).
- `docs/ROADMAP.md`: mark `Tunnel 모드 (Cloudflare Tunnel)` (L346) as Quick-Tunnel
  done / Named-Tunnel follow-up, per the docs-after-slice rule.

### 5. App code

None. Verified surfaces: `_viewer_base()` reads `VIEWER_BASE`
(`apps/server/api/v1/sessions.py`); `isSecureViewerUrl` accepts `https:`
(`apps/desktop/src/setup/setupValues.ts`); web SPA uses relative API/WS URLs
(`apps/web/src/lib/api.ts`). Same-origin through the tunnel ⇒ the server CORS
allowlist (`apps/server/main.py`) needs no new entry for the phone viewer.

## Testing / verification

This slice is deploy config + a shell script + docs; there is no app unit test to
add. A live tunnel cannot be exercised here (needs Cloudflare runtime + the running
stack). Verification:

- `docker compose -f deploy/docker-compose.yml --profile tunnel config` parses
  cleanly (the new `cloudflared` service and existing services validate).
- Caddyfile: `caddy validate --config deploy/Caddyfile` if a `caddy` binary is
  available; otherwise structural review of the `:8080` block against the existing
  `:443` block.
- `tunnel-quick.sh`: `bash -n` syntax check and `shellcheck` if available; review of
  the log-parse + `.env` upsert + timeout/error handling.
- **Manual operator E2E (out of automated scope, documented in DEPLOY.md):** run
  `tunnel-quick.sh`, open the printed `https://<...>.trycloudflare.com/v/<token>` (or
  scan the QR) on a phone over cellular, confirm live subtitles arrive and update.

## Security

Internet exposure makes the per-session viewer token the only gate. The token is a
32-byte URL-safe secret (`secrets.token_urlsafe(32)`) that expires at session end;
the QR is shown only in the meeting room; the trycloudflare URL is valid only while
the tunnel process runs. Stronger gating (SSO / magic-link / PIN) is a documented
DEPLOY §12 follow-up, deliberately out of scope here per the user's decision.

## Open questions

None.
