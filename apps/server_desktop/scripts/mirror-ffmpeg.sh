#!/usr/bin/env bash
set -euo pipefail

# Mirror a PINNED upstream ffmpeg archive into THIS repo's own release assets,
# so the pinned URL stops depending on an upstream retention window.
#
# Why this exists: the Windows and Linux pins came from BtbN/FFmpeg-Builds
# `autobuild-*` tags, which upstream prunes after roughly 11-15 days. On v1.8.0
# that pruning turned the pinned URL into a 404 and killed the Windows server
# build *after* the PyInstaller freeze had already succeeded. Re-pinning fixes
# it for another ~11 days and then breaks again, forever. Mirroring ends the
# cycle: we serve the bytes ourselves.
#
# The copy is BYTE-IDENTICAL on purpose. Because the archive bytes do not
# change, `sha256`, `archive`, `member` and `bin` in ffmpeg.lock.json all stay
# exactly as they were and ONLY `url` moves. That is what lets both fetchers
# (fetch-ffmpeg.sh and build-server.ps1) keep working with no edit at all, and
# it turns the pre-existing sha256 pin into the proof that our mirror is a
# faithful copy of what upstream published rather than a re-roll of our own.
#
# Usage:
#   scripts/mirror-ffmpeg.sh                      # mirror the ephemeral triples
#   scripts/mirror-ffmpeg.sh <triple> [<triple>…] # mirror specific triples
#
# Env:
#   MIRROR_REPO  target repo (default yesonsys03-web/yeson_meet)
#
# Requires: gh (authenticated), jq, curl.
#
# NOTE on the release being a PRERELEASE — this is not cosmetic. The in-app
# updater polls https://github.com/<repo>/releases/latest/download/latest-*.json
# (apps/desktop/src-tauri/tauri.conf.json, apps/server_desktop/src-tauri/tauri.conf.json).
# GitHub resolves `releases/latest` to the newest NON-prerelease release. If a
# mirror release were ever marked as a normal release it would become "latest",
# that URL would 404 because a mirror carries no update manifest, and every
# already-installed build would lose auto-update. The script therefore creates
# the mirror as a prerelease and refuses to touch a pre-existing mirror release
# that is not one.

cd "$(dirname "$0")/../../.."
[[ -f apps/server_desktop/sidecar/server_entry.py ]] || {
    echo "ERROR: repo root detection failed (cwd: $(pwd))" >&2
    exit 1
}

# FFMPEG_MANIFEST is overridable so the refuse-on-mismatch path can be exercised
# against a doctored copy of the lock without touching the real one.
MANIFEST="${FFMPEG_MANIFEST:-apps/server_desktop/ffmpeg.lock.json}"
MIRROR_REPO="${MIRROR_REPO:-yesonsys03-web/yeson_meet}"

# Default set = the triples whose upstream is known to be ephemeral. The macOS
# triples are left on their upstream URLs: evermeet serves version-exact URLs
# and osxexperts' risk is an in-place rebuild (a hash change), which mirroring
# would not prevent — the freshness check covers that instead.
DEFAULT_TRIPLES=(x86_64-pc-windows-msvc x86_64-unknown-linux-gnu)

[[ -f "${MANIFEST}" ]] || { echo "ERROR: manifest not found: ${MANIFEST}" >&2; exit 1; }
for tool in gh jq curl; do
    command -v "${tool}" >/dev/null 2>&1 || {
        echo "ERROR: ${tool} is required" >&2; exit 1; }
done

TRIPLES=("$@")
[[ ${#TRIPLES[@]} -gt 0 ]] || TRIPLES=("${DEFAULT_TRIPLES[@]}")

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

TMP="$(mktemp -d)"; trap 'rm -rf "${TMP}"' EXIT

for TRIPLE in "${TRIPLES[@]}"; do
    pin() { jq -er --arg t "${TRIPLE}" --arg f "$1" '.triples[$t][$f]' "${MANIFEST}"; }
    jq -e --arg t "${TRIPLE}" '.triples[$t]' "${MANIFEST}" >/dev/null 2>&1 || {
        echo "ERROR: no ffmpeg pin for triple ${TRIPLE} in ${MANIFEST}" >&2; exit 1; }

    VERSION="$(pin version)"
    URL="$(pin url)"
    SHA256="$(pin sha256)"
    ASSET="$(basename "${URL}")"
    TAG="ffmpeg-vendor-${VERSION}"
    MIRROR_URL="https://github.com/${MIRROR_REPO}/releases/download/${TAG}/${ASSET}"

    echo "── ${TRIPLE}"
    echo "   upstream: ${URL}"

    if [[ "${URL}" == "https://github.com/${MIRROR_REPO}/"* ]]; then
        echo "   already mirrored — nothing to do (url points at ${MIRROR_REPO})"
        continue
    fi

    # 1. Fetch the upstream archive and prove it is exactly what is pinned.
    #    Mirroring anything else would silently launder an unreviewed binary
    #    into the supply chain, so a mismatch aborts instead of uploading.
    echo "   downloading…"
    curl -fsSL "${URL}" -o "${TMP}/${ASSET}"
    ACTUAL="$(sha256_of "${TMP}/${ASSET}")"
    if [[ "${ACTUAL}" != "${SHA256}" ]]; then
        echo "ERROR: upstream sha256 does not match the pin — refusing to mirror" >&2
        echo "  expected: ${SHA256}" >&2
        echo "  actual:   ${ACTUAL}" >&2
        echo "Mirror only what is already pinned and reviewed. If upstream" >&2
        echo "legitimately rebuilt, re-verify the binary and re-pin first." >&2
        exit 1
    fi
    echo "   sha256 matches pin ✓"

    # 2. Ensure the mirror release exists AND is a prerelease (see header).
    if gh release view "${TAG}" --repo "${MIRROR_REPO}" >/dev/null 2>&1; then
        IS_PRE="$(gh release view "${TAG}" --repo "${MIRROR_REPO}" --json isPrerelease -q .isPrerelease)"
        if [[ "${IS_PRE}" != "true" ]]; then
            echo "ERROR: mirror release ${TAG} exists but is NOT a prerelease." >&2
            echo "Leaving it that way would let it become GitHub's 'latest' release," >&2
            echo "which breaks releases/latest/download/latest-*.json — the endpoint" >&2
            echo "every installed build polls for auto-update. Mark it prerelease:" >&2
            echo "  gh release edit ${TAG} --repo ${MIRROR_REPO} --prerelease" >&2
            exit 1
        fi
        echo "   release ${TAG} exists (prerelease ✓)"
    else
        echo "   creating prerelease ${TAG}…"
        gh release create "${TAG}" \
            --repo "${MIRROR_REPO}" \
            --prerelease \
            --title "ffmpeg vendor mirror ${VERSION}" \
            --notes "$(cat <<EOF
Byte-identical mirror of the pinned upstream ffmpeg archives, so the build no
longer depends on an upstream retention window.

Upstream: https://github.com/BtbN/FFmpeg-Builds (autobuild tags are pruned
after roughly 11-15 days; that pruning broke the v1.8.0 Windows server build).

These assets are copied verbatim. The sha256 values in
\`apps/server_desktop/ffmpeg.lock.json\` are unchanged from the upstream pin,
so they double as proof that this mirror is a faithful copy.

**Do not un-mark this as a prerelease.** The in-app updater polls
\`releases/latest/download/latest-*.json\`; GitHub resolves \`latest\` to the
newest non-prerelease release. If this became "latest" that URL would 404 and
auto-update would break for every installed build.

Not a product release. Nothing here is installable.
EOF
)"
    fi

    # 3. Upload (clobber so re-runs are idempotent).
    echo "   uploading ${ASSET}…"
    gh release upload "${TAG}" "${TMP}/${ASSET}" --repo "${MIRROR_REPO}" --clobber

    # 4. Prove the mirror actually serves the same bytes before anyone repoints
    #    the lock at it. Upload succeeding is not the same as the asset being
    #    downloadable and intact.
    echo "   verifying mirrored asset…"
    curl -fsSL "${MIRROR_URL}" -o "${TMP}/verify"
    MIRRORED="$(sha256_of "${TMP}/verify")"
    if [[ "${MIRRORED}" != "${SHA256}" ]]; then
        echo "ERROR: mirrored asset sha256 mismatch at ${MIRROR_URL}" >&2
        echo "  expected: ${SHA256}" >&2
        echo "  actual:   ${MIRRORED}" >&2
        exit 1
    fi
    rm -f "${TMP}/${ASSET}" "${TMP}/verify"

    echo "   ✓ mirrored, byte-identical"
    echo "   set url in ${MANIFEST} to:"
    echo "     ${MIRROR_URL}"
done

echo
echo "Done. Update the url field(s) above in ${MANIFEST}; leave version, sha256,"
echo "archive, member and bin untouched — the bytes did not change, so the two"
echo "fetchers need no modification."
