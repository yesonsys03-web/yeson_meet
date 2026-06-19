#!/usr/bin/env bash
set -euo pipefail

# Vendor the official cloudflared quick-tunnel binary for the DEV HOST triple
# into the path tunnel.rs::locate_cloudflared() expects:
#   apps/server_desktop/src-tauri/binaries/cloudflared-<triple>/cloudflared[.exe]
#
# P4.1b: this fetches ONLY the current host's triple so a real (non-stub) tunnel
# is runnable for E2E. Full cross-OS vendoring + tauri.conf resources bundling is
# P4.3. The downloaded binary is GITIGNORED (binaries/cloudflared-*/, mirroring
# yeson-server-*/) — never committed (~50-70MB). The committed cloudflared-stub.sh
# remains the offline-test fake.
#
# cloudflared is published on GitHub releases. macOS ships as a .tgz; Linux and
# Windows ship as a raw binary. We pull the "latest" release.

# repo root = scripts/../../.. (apps/server_desktop/scripts -> repo root)
cd "$(dirname "$0")/../../.."
[[ -f apps/server_desktop/sidecar/server_entry.py ]] || {
    echo "ERROR: repo root detection failed (cwd: $(pwd))" >&2
    exit 1
}

BASE="https://github.com/cloudflare/cloudflared/releases/latest/download"
ARCH="$(uname -m)"
OS="$(uname -s)"

case "${OS}-${ARCH}" in
    Darwin-arm64)
        TRIPLE="aarch64-apple-darwin"
        ASSET="cloudflared-darwin-arm64.tgz"
        PACKED="tgz"
        BIN="cloudflared"
        ;;
    Darwin-x86_64)
        TRIPLE="x86_64-apple-darwin"
        ASSET="cloudflared-darwin-amd64.tgz"
        PACKED="tgz"
        BIN="cloudflared"
        ;;
    Linux-x86_64)
        TRIPLE="x86_64-unknown-linux-gnu"
        ASSET="cloudflared-linux-amd64"
        PACKED="raw"
        BIN="cloudflared"
        ;;
    *)
        echo "ERROR: unsupported host ${OS}-${ARCH} (Windows: run the .exe variant in P4.3)" >&2
        exit 1
        ;;
esac

DEST_DIR="apps/server_desktop/src-tauri/binaries/cloudflared-${TRIPLE}"
DEST_BIN="${DEST_DIR}/${BIN}"
mkdir -p "${DEST_DIR}"

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

echo "Downloading ${ASSET} (latest cloudflared)…"
curl -fsSL "${BASE}/${ASSET}" -o "${TMP}/${ASSET}"

if [[ "${PACKED}" == "tgz" ]]; then
    tar -xzf "${TMP}/${ASSET}" -C "${TMP}"
    # macOS tgz extracts a `cloudflared` binary at the archive root.
    [[ -f "${TMP}/cloudflared" ]] || {
        echo "ERROR: cloudflared not found inside ${ASSET}" >&2
        exit 1
    }
    cp "${TMP}/cloudflared" "${DEST_BIN}"
else
    cp "${TMP}/${ASSET}" "${DEST_BIN}"
fi

chmod +x "${DEST_BIN}"

echo "→ ${DEST_BIN}"
echo "  version: $("${DEST_BIN}" --version 2>/dev/null | head -1 || echo '(unknown)')"
echo "  size:    $(du -sh "${DEST_BIN}" | cut -f1)"
echo "Done. tunnel.rs::locate_cloudflared() will resolve this in dev (src-tauri/binaries)."
