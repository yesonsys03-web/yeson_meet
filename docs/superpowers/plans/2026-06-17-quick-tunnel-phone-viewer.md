# Quick Tunnel Phone Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing viewer over the public internet via a Cloudflare Quick Tunnel so participant phones open a public HTTPS URL over cellular and see live subtitles.

**Architecture:** Deploy config + a runbook script + docs — no app code. An opt-in `cloudflared` compose service (Quick Tunnel) points at a new plain-HTTP Caddy `:8080` origin that mirrors the existing site (web SPA + `/api` + `/ws`). A helper script captures the random `*.trycloudflare.com` URL, writes it to `VIEWER_BASE` in `deploy/.env`, and recreates the server so the operator's QR encodes the public URL.

**Tech Stack:** Docker Compose, Caddy 2, cloudflared (Cloudflare Quick Tunnel), bash. No unit tests — verification is `docker compose config`, `bash -n`/`shellcheck`, `caddy validate` (when the tool is present), and structural review; the live tunnel E2E is a manual operator step.

**Spec:** `docs/superpowers/specs/2026-06-17-quick-tunnel-phone-viewer-design.md`

---

## File Structure

- `deploy/Caddyfile` — add a plain-HTTP `:8080` tunnel-origin site block (mirrors the `:443` routing without TLS).
- `deploy/docker-compose.yml` — add the opt-in `cloudflared` service; pass `VIEWER_BASE` to the `server` service.
- `deploy/tunnel-quick.sh` — **new** runbook helper: start tunnel, capture URL, set `VIEWER_BASE`, recreate server.
- `deploy/env.example` — document `VIEWER_BASE`.
- `docs/DEPLOY.md` — Quick Tunnel runbook + teardown + security note.
- `docs/ROADMAP.md` — mark the Tunnel-mode item.

Note on environment availability: `docker`, `caddy`, and `shellcheck` may not be installed in the implementation environment. For each verification step, run the named tool if present; if it is absent, fall back to the structural review described in that step. Do NOT treat a missing tool as a failure.

---

## Task 1: Caddy `:8080` tunnel origin

**Files:**
- Modify: `deploy/Caddyfile`

The current Caddyfile has a global block, the `{$SERVER_HOST}:443` site (with `tls internal`, `/api/*`, `/ws/*`, static web), and a `:80` → HTTPS redirect. Add a plain-HTTP `:8080` site that mirrors the `:443` routing without TLS (Cloudflare provides public TLS). It is internal-only (not published to the host).

- [ ] **Step 1: Add the `:8080` block**

In `deploy/Caddyfile`, insert this block immediately after the closing `}` of the `{$SERVER_HOST}:443 { … }` site and before the `:80 { … }` redirect block:

```caddyfile
# Tunnel origin: plain HTTP for cloudflared (public TLS handled by Cloudflare).
# Reachable only on the internal docker network (not published to the host).
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

- [ ] **Step 2: Verify the Caddyfile is valid**

Run (if a `caddy` binary is available): `caddy validate --config deploy/Caddyfile --adapter caddyfile`
Expected: `Valid configuration`.
If `caddy` is not installed: structurally verify the new `:8080` block matches the `:443` block's three `handle` routes (`/api/*`, `/ws/*`, static), has balanced braces, and is a sibling site block (not nested inside `:443`).

- [ ] **Step 3: Commit**

```bash
git add deploy/Caddyfile
git commit -m "feat(deploy): add plain-HTTP Caddy :8080 tunnel origin"
```

---

## Task 2: cloudflared service + `VIEWER_BASE` plumbing

**Files:**
- Modify: `deploy/docker-compose.yml`

Two changes: (a) pass `VIEWER_BASE` to the `server` service so the operator QR uses it (currently absent — `_viewer_base()` falls back to a localhost default); (b) add the opt-in `cloudflared` Quick Tunnel service.

- [ ] **Step 1: Pass `VIEWER_BASE` to the server**

In `deploy/docker-compose.yml`, inside the `server:` service `environment:` map, add a `VIEWER_BASE` line next to the existing `SERVER_HOST` / `STORAGE_ROOT` entries:

```yaml
      VIEWER_BASE: ${VIEWER_BASE:-https://localhost}
```

(LAN deployments set `VIEWER_BASE=https://<SERVER_IP>` in `.env`; tunnel mode has `tunnel-quick.sh` set it to the trycloudflare URL. The `https://localhost` default is a harmless dev fallback.)

- [ ] **Step 2: Add the opt-in `cloudflared` service**

At the end of the `services:` map in `deploy/docker-compose.yml` (after the `caddy:` service), add:

```yaml
  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    profiles: ["tunnel"]
    command: tunnel --no-autoupdate --url http://caddy:8080
    depends_on:
      - caddy
```

(Quick Tunnel needs no account/token. `profiles: ["tunnel"]` keeps it out of default `docker compose up`; it starts only with `--profile tunnel`. It reaches Caddy's internal `:8080` by service name on the compose network.)

- [ ] **Step 3: Verify the compose file parses (both default and tunnel profile)**

Run (if `docker` is available):
`docker compose -f deploy/docker-compose.yml config >/dev/null && echo DEFAULT_OK`
`docker compose -f deploy/docker-compose.yml --profile tunnel config | grep -q cloudflared && echo TUNNEL_OK`
Expected: `DEFAULT_OK` then `TUNNEL_OK`; the default `config` (no profile) must NOT include `cloudflared`, the `--profile tunnel` one MUST.
If `docker` is not installed: validate the YAML is well-formed (e.g. `python3 -c "import yaml,sys; yaml.safe_load(open('deploy/docker-compose.yml'))"` → no error) and review that `cloudflared` has `profiles: ["tunnel"]`, the `--url http://caddy:8080` command, and that `server.environment` now contains `VIEWER_BASE`.

- [ ] **Step 4: Commit**

```bash
git add deploy/docker-compose.yml
git commit -m "feat(deploy): add cloudflared quick-tunnel service + VIEWER_BASE plumbing"
```

---

## Task 3: `tunnel-quick.sh` runbook helper

**Files:**
- Create: `deploy/tunnel-quick.sh`

- [ ] **Step 1: Create the script**

Create `deploy/tunnel-quick.sh` with exactly:

```bash
#!/usr/bin/env bash
# Quick Tunnel bring-up:
#   1) start cloudflared (Cloudflare Quick Tunnel, no account)
#   2) capture the random https://<...>.trycloudflare.com URL from its logs
#   3) set VIEWER_BASE to that URL in deploy/.env
#   4) recreate the server container so the operator QR uses the public URL
# Run this BEFORE starting a meeting (server recreate drops no active viewers then).
set -euo pipefail

cd "$(dirname "$0")"   # deploy/
ENV_FILE=".env"

echo "Starting cloudflared (Quick Tunnel)..."
docker compose --profile tunnel up -d cloudflared

echo "Waiting for the public tunnel URL (up to ~60s)..."
url=""
for _ in $(seq 1 30); do
  url="$(docker compose logs cloudflared 2>&1 \
    | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || true)"
  [ -n "$url" ] && break
  sleep 2
done

if [ -z "$url" ]; then
  echo "ERROR: no trycloudflare.com URL found in cloudflared logs after ~60s." >&2
  echo "Inspect manually: docker compose logs cloudflared" >&2
  exit 1
fi
echo "Tunnel URL: $url"

touch "$ENV_FILE"
if grep -q '^VIEWER_BASE=' "$ENV_FILE"; then
  tmp="$(mktemp)"
  sed "s#^VIEWER_BASE=.*#VIEWER_BASE=${url}#" "$ENV_FILE" > "$tmp"
  mv "$tmp" "$ENV_FILE"
else
  printf 'VIEWER_BASE=%s\n' "$url" >> "$ENV_FILE"
fi
echo "Set VIEWER_BASE=${url} in ${ENV_FILE}"

echo "Recreating server to pick up VIEWER_BASE..."
docker compose up -d server

echo
echo "Done. Participant viewer base: ${url}"
echo "Start a meeting in the operator app — the QR will encode ${url}/v/<token>."
```

(The `#` sed delimiter avoids escaping the `/` in the URL. `VIEWER_BASE` is the bare origin with no trailing slash, matching `viewer_url = f"{VIEWER_BASE}/v/{token}"`.)

- [ ] **Step 2: Make it executable**

```bash
chmod +x deploy/tunnel-quick.sh
```

- [ ] **Step 3: Verify the script**

Run: `bash -n deploy/tunnel-quick.sh && echo SYNTAX_OK`
Expected: `SYNTAX_OK`.
Then (if `shellcheck` is available): `shellcheck deploy/tunnel-quick.sh`
Expected: no errors (warnings about `docker` being external are acceptable). If `shellcheck` is not installed, review that `set -euo pipefail` is present, the URL-not-found path `exit 1`s, and the `.env` upsert handles both the existing-line and missing-line cases.

- [ ] **Step 4: Commit**

```bash
git add deploy/tunnel-quick.sh
git commit -m "feat(deploy): add tunnel-quick.sh runbook helper"
```

---

## Task 4: Docs — env.example, DEPLOY runbook, ROADMAP

**Files:**
- Modify: `deploy/env.example`
- Modify: `docs/DEPLOY.md`
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Document `VIEWER_BASE` in env.example**

In `deploy/env.example`, add a `VIEWER_BASE` entry immediately after the `SERVER_HOST=localhost` line:

```bash
# Public base URL used to build the participant viewer QR (viewer_url = VIEWER_BASE/v/<token>).
# LAN mode: set to your server's HTTPS address, e.g. https://192.168.0.38
# Tunnel mode: leave as-is; deploy/tunnel-quick.sh auto-sets it to the trycloudflare URL.
VIEWER_BASE=https://localhost
```

- [ ] **Step 2: Add the Quick Tunnel runbook to DEPLOY.md**

In `docs/DEPLOY.md`, in/after the existing "외부 배포 시 변경점 (Phase 5+ 메모)" section (the one listing `ACCESS_MODE=tunnel` / Cloudflare Tunnel), add a concrete runbook subsection:

```markdown
### Quick Tunnel (핸드폰 셀룰러 접속) — 계정 불필요

회의실 와이파이가 서버에 닿지 않을 때, 핸드폰이 각자 셀룰러로 공개 URL을 열어 자막을 본다.
전제: 서버가 인터넷 egress 가능(이미 Gemini 호출로 충족).

1. 회의 시작 전, deploy/ 에서: `./tunnel-quick.sh`
   - cloudflared(Quick Tunnel) 기동 → `https://<랜덤>.trycloudflare.com` 발급
   - 그 URL을 `deploy/.env`의 `VIEWER_BASE`에 기록 → server 재생성
   - 출력된 URL이 참가자 viewer base
2. 운영자 앱에서 회의 시작 → QR이 자동으로 `https://<랜덤>.trycloudflare.com/v/<token>` 을 담음
3. 참가자: 셀룰러 상태로 QR 스캔(룸 와이파이 불필요)
4. 종료: `docker compose --profile tunnel down` (또는 cloudflared만 stop)

수동 폴백(스크립트 로그 파싱 실패 시): `docker compose logs cloudflared` 에서
`https://...trycloudflare.com` 복사 → `deploy/.env`의 `VIEWER_BASE=` 에 설정 →
`docker compose up -d server`.

보안: 인터넷 노출 시 게이트는 세션별 viewer 토큰(32B, 회의 종료 시 만료)뿐이다. QR은
회의실에서만 배포하고, trycloudflare URL은 터널이 떠 있는 동안만 유효하다. URL은 재시작마다
바뀐다(Quick Tunnel). 고정 도메인/SSO·PIN은 후속(Named Tunnel / DEPLOY §12).
```

(Match the surrounding heading depth/format of DEPLOY.md when inserting.)

- [ ] **Step 3: Mark the ROADMAP item**

In `docs/ROADMAP.md`, update the `- [ ] Tunnel 모드 (Cloudflare Tunnel)` line (~L346) to reflect Quick Tunnel landing, keeping the existing list style:

```markdown
- [~] Tunnel 모드 (Cloudflare Tunnel) — Quick Tunnel(임시·계정불필) 코드/런북 완료: cloudflared compose 서비스(profile opt-in) + Caddy :8080 origin + `tunnel-quick.sh`가 `VIEWER_BASE` 자동 설정. 라이브 터널 E2E는 운영자 수동. Named Tunnel(고정 도메인)·SSO/PIN은 후속.
```

- [ ] **Step 4: Commit**

```bash
git add deploy/env.example docs/DEPLOY.md docs/ROADMAP.md
git commit -m "docs(deploy): Quick Tunnel runbook + VIEWER_BASE env + ROADMAP"
```

---

## Final verification

- [ ] **Compose still parses (default + tunnel profile)**

Run (if `docker` present): `docker compose -f deploy/docker-compose.yml config >/dev/null && docker compose -f deploy/docker-compose.yml --profile tunnel config >/dev/null && echo OK`
Expected: `OK`. If `docker` absent: `python3 -c "import yaml; yaml.safe_load(open('deploy/docker-compose.yml'))"` exits 0.

- [ ] **Script syntax is clean**

Run: `bash -n deploy/tunnel-quick.sh && echo OK`
Expected: `OK`.

- [ ] **Only intended files changed**

Run: `git status --short`
Expected: only the prior-session leftovers (`PROJECT_CONTEXT.md`, `apps/desktop/scripts/vm_dump.py`, `bun.lock`) remain unstaged — do not commit those.

- [ ] **Manual operator E2E (out of automated scope — note in handoff, do not block on it)**

With the stack running: `./deploy/tunnel-quick.sh`, then open the printed
`https://<...>.trycloudflare.com/v/<token>` (or scan the QR) on a phone over cellular,
and confirm live subtitles arrive and update. This requires a Cloudflare runtime and
the running stack, so it is verified by the operator, not in this implementation.
