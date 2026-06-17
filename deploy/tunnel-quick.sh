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
