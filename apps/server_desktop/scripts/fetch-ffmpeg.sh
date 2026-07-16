#!/usr/bin/env bash
set -euo pipefail

# Vendor a PINNED static ffmpeg binary for the HOST triple into the path
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
# The version, URL and sha256 for every triple live in ../ffmpeg.lock.json —
# NOT here — because build-server.ps1 vendors the Windows binary natively and
# must stay on the exact same pin. Read that file before changing anything.
#
# Idempotent: skips the download when the vendored binary already matches the
# manifest pin (tracked via the .pinned stamp, so bumping the manifest re-pulls
# automatically instead of leaving a stale binary behind). FORCE=1 re-downloads.

# repo root = scripts/../../.. (apps/server_desktop/scripts -> repo root)
cd "$(dirname "$0")/../../.."
[[ -f apps/server_desktop/sidecar/server_entry.py ]] || {
    echo "ERROR: repo root detection failed (cwd: $(pwd))" >&2
    exit 1
}

MANIFEST="apps/server_desktop/ffmpeg.lock.json"
[[ -f "${MANIFEST}" ]] || {
    echo "ERROR: manifest not found: ${MANIFEST}" >&2
    exit 1
}
command -v jq >/dev/null 2>&1 || {
    echo "ERROR: jq is required to read ${MANIFEST} (brew install jq / apt install jq)" >&2
    exit 1
}

ARCH="$(uname -m)"
OS="$(uname -s)"

case "${OS}-${ARCH}" in
    Darwin-arm64)                              TRIPLE="aarch64-apple-darwin" ;;
    Darwin-x86_64)                             TRIPLE="x86_64-apple-darwin" ;;
    Linux-x86_64)                              TRIPLE="x86_64-unknown-linux-gnu" ;;
    MINGW*-x86_64|MSYS*-x86_64|CYGWIN*-x86_64) TRIPLE="x86_64-pc-windows-msvc" ;;
    *)
        echo "ERROR: unsupported host ${OS}-${ARCH}" >&2; exit 1 ;;
esac

pin() { jq -er --arg t "${TRIPLE}" --arg f "$1" '.triples[$t][$f]' "${MANIFEST}"; }

jq -e --arg t "${TRIPLE}" '.triples[$t]' "${MANIFEST}" >/dev/null 2>&1 || {
    echo "ERROR: no ffmpeg pin for triple ${TRIPLE} in ${MANIFEST}" >&2
    exit 1
}

VERSION="$(pin version)"
URL="$(pin url)"
SHA256="$(pin sha256)"
ARCHIVE="$(pin archive)"
MEMBER="$(pin member)"
BIN="$(pin bin)"

DEST_DIR="apps/server_desktop/src-tauri/binaries/ffmpeg-${TRIPLE}"
DEST_BIN="${DEST_DIR}/${BIN}"
STAMP="${DEST_DIR}/.pinned"
WANT="${VERSION} ${SHA256}"

if [[ -x "${DEST_BIN}" && -f "${STAMP}" && "$(cat "${STAMP}")" == "${WANT}" && "${FORCE:-}" != "1" ]]; then
    echo "ffmpeg already vendored at pinned ${VERSION}: ${DEST_BIN} (set FORCE=1 to re-download)"
    exit 0
fi

# sha256 tooling differs by host: coreutils on Linux/Git Bash, shasum on macOS.
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

mkdir -p "${DEST_DIR}"
TMP="$(mktemp -d)"; trap 'rm -rf "${TMP}"' EXIT

echo "Downloading ffmpeg ${VERSION} for ${TRIPLE}…"
curl -fsSL "${URL}" -o "${TMP}/pkg"

ACTUAL="$(sha256_of "${TMP}/pkg")"
if [[ "${ACTUAL}" != "${SHA256}" ]]; then
    echo "ERROR: sha256 mismatch — refusing to vendor ${URL}" >&2
    echo "  expected: ${SHA256}" >&2
    echo "  actual:   ${ACTUAL}" >&2
    echo "The pinned artifact changed upstream, or the download was tampered with." >&2
    echo "If this is an intentional upgrade, bump version+url+sha256 in ${MANIFEST}." >&2
    exit 1
fi

# `member` is the exact path inside the archive (the pin makes it deterministic,
# so there is no globbing over an unknown version directory).
case "${ARCHIVE}" in
    zip)
        # `|| true`: unzip exits 11 on an unmatched member, which set -e would
        # turn into a bare "filename not matched" + exit 11. Let the -f check
        # below report which member was missing, and from which URL.
        unzip -q -o -j "${TMP}/pkg" "${MEMBER}" -d "${TMP}/out" || true ;;
    tar.xz)
        mkdir -p "${TMP}/out"
        tar -xJf "${TMP}/pkg" -C "${TMP}/out" --strip-components="$(( $(tr -cd '/' <<<"${MEMBER}" | wc -c) ))" "${MEMBER}" ;;
    *)
        echo "ERROR: unsupported archive type '${ARCHIVE}' for ${TRIPLE}" >&2; exit 1 ;;
esac

EXTRACTED="${TMP}/out/$(basename "${MEMBER}")"
[[ -f "${EXTRACTED}" ]] || {
    echo "ERROR: '${MEMBER}' not found inside the archive from ${URL}" >&2
    exit 1
}

cp "${EXTRACTED}" "${DEST_BIN}"
chmod +x "${DEST_BIN}"
printf '%s' "${WANT}" > "${STAMP}"

echo "→ ${DEST_BIN}"
echo "  pinned:  ${VERSION} (sha256 verified)"
echo "  size:    $(du -sh "${DEST_BIN}" | cut -f1)"
