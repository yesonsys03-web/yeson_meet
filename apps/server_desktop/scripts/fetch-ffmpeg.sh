#!/usr/bin/env bash
set -euo pipefail

# Vendor a static ffmpeg binary for the HOST triple into the path
# server_process.rs::locate_bundled_ffmpeg() expects:
#   apps/server_desktop/src-tauri/binaries/ffmpeg-<triple>/ffmpeg[.exe]
#
# Task 14: mirrors fetch-cloudflared.sh's fetch/vendor pattern for the video
# caption studio's ffmpeg dependency (subtitle burn-in + audio/video probing).
# The binary is GITIGNORED (binaries/ffmpeg-*/, mirroring cloudflared-*/ and
# yeson-server-*/) — never committed. build-server.sh calls this idempotently
# at the end of the freeze, and tauri.conf.json's `binaries/ffmpeg-*` resource
# glob bundles the result into Contents/Resources so the PACKAGED app carries
# ffmpeg (not just dev).
#
# Idempotent: skips the download if the binary is already vendored (set
# FORCE=1 to re-pull the latest release).

# repo root = scripts/../../.. (apps/server_desktop/scripts -> repo root)
cd "$(dirname "$0")/../../.."
[[ -f apps/server_desktop/sidecar/server_entry.py ]] || {
    echo "ERROR: repo root detection failed (cwd: $(pwd))" >&2
    exit 1
}

ARCH="$(uname -m)"
OS="$(uname -s)"

# 정적 단일 바이너리 배포처: mac=evermeet(x86_64, arm은 Rosetta로 동작),
# win=BtbN GPL zip, linux=johnvansickle static
case "${OS}-${ARCH}" in
    Darwin-arm64)
        TRIPLE="aarch64-apple-darwin"
        URL="https://evermeet.cx/ffmpeg/getrelease/zip"; PACKED="zip"; BIN="ffmpeg" ;;
    Darwin-x86_64)
        TRIPLE="x86_64-apple-darwin"
        URL="https://evermeet.cx/ffmpeg/getrelease/zip"; PACKED="zip"; BIN="ffmpeg" ;;
    Linux-x86_64)
        TRIPLE="x86_64-unknown-linux-gnu"
        URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
        PACKED="txz"; BIN="ffmpeg" ;;
    MINGW*-x86_64|MSYS*-x86_64|CYGWIN*-x86_64)
        TRIPLE="x86_64-pc-windows-msvc"
        URL="https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
        PACKED="winzip"; BIN="ffmpeg.exe" ;;
    *)
        echo "ERROR: unsupported host ${OS}-${ARCH}" >&2; exit 1 ;;
esac

DEST_DIR="apps/server_desktop/src-tauri/binaries/ffmpeg-${TRIPLE}"
DEST_BIN="${DEST_DIR}/${BIN}"

if [[ -x "${DEST_BIN}" && "${FORCE:-}" != "1" ]]; then
    echo "ffmpeg already vendored: ${DEST_BIN} (set FORCE=1 to re-download)"
    exit 0
fi

mkdir -p "${DEST_DIR}"
TMP="$(mktemp -d)"; trap 'rm -rf "${TMP}"' EXIT

curl -fsSL "${URL}" -o "${TMP}/pkg"
case "${PACKED}" in
    zip)
        unzip -q "${TMP}/pkg" -d "${TMP}"
        cp "${TMP}/ffmpeg" "${DEST_BIN}" ;;
    txz)
        tar -xJf "${TMP}/pkg" -C "${TMP}"
        cp "${TMP}"/ffmpeg-*-static/ffmpeg "${DEST_BIN}" ;;
    winzip)
        unzip -q "${TMP}/pkg" -d "${TMP}"
        cp "${TMP}"/ffmpeg-*/bin/ffmpeg.exe "${DEST_BIN}" ;;
esac
chmod +x "${DEST_BIN}"
echo "vendored: ${DEST_BIN}"
