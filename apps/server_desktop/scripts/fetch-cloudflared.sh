#!/usr/bin/env bash
set -euo pipefail

# Vendor the official cloudflared quick-tunnel binary for the HOST triple into
# the path tunnel.rs::locate_cloudflared() expects:
#   apps/server_desktop/src-tauri/binaries/cloudflared-<triple>/cloudflared[.exe]
#
# P4.3: vendors the current host's triple (macOS arm64/amd64, Linux amd64,
# Windows amd64 — each platform's own build fetches its own binary; Tauri builds
# per-OS, not cross). build-server.sh calls this idempotently before `tauri
# build`, and tauri.conf.json's `binaries/cloudflared-*` resource glob bundles
# the result into Contents/Resources so the PACKAGED app carries cloudflared (not
# just dev). The binary is GITIGNORED (binaries/cloudflared-*/, mirroring
# yeson-server-*/) — never committed (~50-70MB). The committed cloudflared-stub.sh
# remains the offline-test fake.
#
# Idempotent: skips the download if the binary is already vendored (set FORCE=1
# to re-pull the latest release).
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
    MINGW*-x86_64|MSYS*-x86_64|CYGWIN*-x86_64)
        # Windows under Git Bash / MSYS2 / Cygwin. cloudflared ships a raw .exe;
        # locate_cloudflared() expects the `.exe` suffix on the windows triple.
        TRIPLE="x86_64-pc-windows-msvc"
        ASSET="cloudflared-windows-amd64.exe"
        PACKED="raw"
        BIN="cloudflared.exe"
        ;;
    *)
        echo "ERROR: unsupported host ${OS}-${ARCH}" >&2
        exit 1
        ;;
esac

DEST_DIR="apps/server_desktop/src-tauri/binaries/cloudflared-${TRIPLE}"
DEST_BIN="${DEST_DIR}/${BIN}"

# Idempotent: a build that already vendored cloudflared skips the ~50-70MB pull
# (build-server.sh calls this before every `tauri build`). FORCE=1 re-downloads.
if [[ -x "${DEST_BIN}" && "${FORCE:-}" != "1" ]]; then
    echo "cloudflared already vendored: ${DEST_BIN} (set FORCE=1 to re-download)"
    echo "  version: $("${DEST_BIN}" --version 2>/dev/null | head -1 || echo '(unknown)')"
    exit 0
fi

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
echo "Done. locate_cloudflared() resolves this in dev (src-tauri/binaries) and,"
echo "once packaged via tauri.conf resources, in Contents/Resources/binaries."
