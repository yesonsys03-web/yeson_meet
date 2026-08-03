#!/usr/bin/env bash
set -uo pipefail

# Verify EVERY ffmpeg pin in ffmpeg.lock.json, independently of the host triple.
#
# Why this exists: the pins point at third-party artifacts that can disappear or
# change underneath us, and until now the only thing that noticed was a release
# build failing. On v1.8.0 a pruned BtbN autobuild tag 404'd and took the
# Windows server build down *after* the PyInstaller freeze had already
# succeeded. This script is the early-warning channel — run weekly by
# .github/workflows/ffmpeg-pin-freshness.yml, and in --quick form as the manual
# pre-release check in .claude/skills/release/SKILL.md step 1.
#
# The two failure modes need DIFFERENT remediation, so they are reported
# separately rather than as one "broken pin":
#
#   GONE / UNREACHABLE  the artifact is no longer served. Re-pin (and prefer
#                       mirroring it — see mirror-ffmpeg.sh).
#   SHA256 MISMATCH     the URL still serves something, but not the bytes we
#                       pinned. Either upstream rebuilt in place (osxexperts
#                       does this — its URL carries only the major version) or
#                       the artifact was tampered with. Do NOT just re-pin:
#                       re-verify the binary still meets the requirements in
#                       ffmpeg.lock.json's _readme first.
#   MEMBER MISSING      the archive no longer contains the path the fetchers
#                       extract, so vendoring would fail even though the
#                       download succeeded.
#
# Usage:
#   scripts/verify-ffmpeg-pins.sh                 # full check, all triples
#   scripts/verify-ffmpeg-pins.sh --quick         # reachability only (fast)
#   scripts/verify-ffmpeg-pins.sh <triple> [...]  # limit to specific triples
#
# --quick answers "is every pin still served?" in about a second per triple
# instead of downloading ~300MB, which is what makes it usable as a manual
# pre-release check. It deliberately does NOT re-check sha256: the vendoring
# steps in fetch-ffmpeg.sh and build-server.ps1 already verify the hash and fail
# closed, and those now run BEFORE the freeze, so the build itself is the
# hash-checking gate. Use the full mode when you want to catch an in-place
# upstream rebuild rather than a disappearance.
#
# Env:
#   FFMPEG_MANIFEST  path to the lock file (default apps/server_desktop/ffmpeg.lock.json)

cd "$(dirname "$0")/../../.."
[[ -f apps/server_desktop/sidecar/server_entry.py ]] || {
    echo "ERROR: repo root detection failed (cwd: $(pwd))" >&2
    exit 1
}

MANIFEST="${FFMPEG_MANIFEST:-apps/server_desktop/ffmpeg.lock.json}"
QUICK=0
TRIPLES=()

for arg in "$@"; do
    case "${arg}" in
        --quick) QUICK=1 ;;
        -*) echo "ERROR: unknown option ${arg}" >&2; exit 2 ;;
        *) TRIPLES+=("${arg}") ;;
    esac
done

[[ -f "${MANIFEST}" ]] || { echo "ERROR: manifest not found: ${MANIFEST}" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required" >&2; exit 2; }

if [[ ${#TRIPLES[@]} -eq 0 ]]; then
    while IFS= read -r t; do TRIPLES+=("${t}"); done < <(jq -r '.triples | keys[]' "${MANIFEST}")
fi

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

TMP="$(mktemp -d)"; trap 'rm -rf "${TMP}"' EXIT

FAILED=()
MODE="full check (download + sha256 + member extraction)"
[[ ${QUICK} -eq 1 ]] && MODE="quick check (reachability only)"
echo "ffmpeg pin verification — ${MODE}"
echo "manifest: ${MANIFEST}"
echo

for TRIPLE in "${TRIPLES[@]}"; do
    URL="$(jq -r --arg t "${TRIPLE}" '.triples[$t].url' "${MANIFEST}")"
    SHA256="$(jq -r --arg t "${TRIPLE}" '.triples[$t].sha256' "${MANIFEST}")"
    ARCHIVE="$(jq -r --arg t "${TRIPLE}" '.triples[$t].archive' "${MANIFEST}")"
    MEMBER="$(jq -r --arg t "${TRIPLE}" '.triples[$t].member' "${MANIFEST}")"
    VERSION="$(jq -r --arg t "${TRIPLE}" '.triples[$t].version' "${MANIFEST}")"

    if [[ "${URL}" == "null" ]]; then
        echo "✗ ${TRIPLE}: no such triple in ${MANIFEST}"
        FAILED+=("${TRIPLE}: no such triple"); continue
    fi

    printf '%-26s %s\n' "${TRIPLE}" "${VERSION}"

    # Reachability first. A range request keeps --quick cheap and still follows
    # the redirect chain that GitHub release assets use.
    # curl already writes 000 through -w when it cannot connect at all, so do
    # NOT append another 000 on non-zero exit — that concatenated into a
    # nonsense "HTTP 000000" the first time a DNS blip hit this.
    #
    # --retry matters more than it looks: this is an ALARM, and a false alarm
    # is expensive because it teaches people to ignore the real one. The first
    # scheduled-style run went red purely because osxexperts.net timed out once
    # from a GitHub runner (60s, while every other run that hour passed it).
    # curl's plain --retry covers exactly the transient cases (timeouts, 408,
    # 429, 5xx) and deliberately does NOT retry a 404 — so a genuinely pruned
    # artifact still fails fast instead of costing three extra waits.
    CODE="$(curl -sSL -o /dev/null -w '%{http_code}' \
                 --retry 3 --retry-delay 5 --retry-connrefused \
                 --connect-timeout 20 --max-time 90 \
                 -r 0-0 "${URL}" 2>/dev/null)" || true
    [[ -n "${CODE}" ]] || CODE="000"
    if [[ "${CODE}" != "200" && "${CODE}" != "206" ]]; then
        if [[ "${CODE}" == "404" ]]; then
            echo "  ✗ GONE (404) — the pinned artifact is no longer served"
            echo "    ${URL}"
            echo "    Fix: re-pin to a live artifact, and mirror it so this cannot recur"
            echo "         (apps/server_desktop/scripts/mirror-ffmpeg.sh)"
            FAILED+=("${TRIPLE}: GONE (404)")
        elif [[ "${CODE}" == "000" ]]; then
            # Distinguished from an HTTP error on purpose: a DNS or network
            # blip is a problem with THIS machine, not evidence that the pin
            # rotted. Reporting it the same way would send someone re-pinning
            # a perfectly healthy artifact.
            echo "  ✗ UNREACHABLE — could not connect after retries"
            echo "    (DNS failure, network, or the host is down/timing out)"
            echo "    ${URL}"
            echo "    Transport problem, not proof the pin is bad — this does NOT"
            echo "    mean the artifact was pruned. Re-run before changing anything;"
            echo "    if it persists, check the URL from another network."
            FAILED+=("${TRIPLE}: UNREACHABLE (no connection)")
        else
            echo "  ✗ UNREACHABLE (HTTP ${CODE})"
            echo "    ${URL}"
            FAILED+=("${TRIPLE}: UNREACHABLE (HTTP ${CODE})")
        fi
        continue
    fi

    if [[ ${QUICK} -eq 1 ]]; then
        echo "  ✓ reachable (HTTP ${CODE})"
        continue
    fi

    if ! curl -fsSL --retry 3 --retry-delay 5 --retry-connrefused \
              --connect-timeout 20 --max-time 600 "${URL}" -o "${TMP}/pkg"; then
        echo "  ✗ UNREACHABLE — download failed after a successful range probe"
        echo "    ${URL}"
        FAILED+=("${TRIPLE}: download failed"); continue
    fi

    ACTUAL="$(sha256_of "${TMP}/pkg")"
    if [[ "${ACTUAL}" != "${SHA256}" ]]; then
        echo "  ✗ SHA256 MISMATCH — the URL serves different bytes than pinned"
        echo "    expected: ${SHA256}"
        echo "    actual:   ${ACTUAL}"
        echo "    ${URL}"
        echo "    Fix: this is NOT a routine re-pin. Upstream may have rebuilt in"
        echo "         place, or the artifact was tampered with. Re-verify the"
        echo "         binary against the requirements in the manifest _readme"
        echo "         (libass, no --enable-nonfree, static) before re-pinning."
        FAILED+=("${TRIPLE}: SHA256 MISMATCH"); continue
    fi

    # Extraction proves `member` still resolves — a download can succeed while
    # the path the fetchers pull out has moved, which would only surface as a
    # build failure later.
    rm -rf "${TMP}/out"; mkdir -p "${TMP}/out"
    case "${ARCHIVE}" in
        zip)
            unzip -q -o -j "${TMP}/pkg" "${MEMBER}" -d "${TMP}/out" >/dev/null 2>&1 || true ;;
        tar.xz)
            tar -xJf "${TMP}/pkg" -C "${TMP}/out" \
                --strip-components="$(( $(tr -cd '/' <<<"${MEMBER}" | wc -c) ))" \
                "${MEMBER}" >/dev/null 2>&1 || true ;;
        *)
            echo "  ✗ unsupported archive type '${ARCHIVE}'"
            FAILED+=("${TRIPLE}: unsupported archive '${ARCHIVE}'"); continue ;;
    esac

    if [[ ! -f "${TMP}/out/$(basename "${MEMBER}")" ]]; then
        echo "  ✗ MEMBER MISSING — '${MEMBER}' is not inside the archive"
        echo "    ${URL}"
        echo "    Fix: the archive layout changed; update 'member' to match."
        FAILED+=("${TRIPLE}: MEMBER MISSING"); continue
    fi

    echo "  ✓ reachable, sha256 matches, member extracts"
    rm -f "${TMP}/pkg"
done

echo
if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo "FAILED (${#FAILED[@]}):"
    for f in "${FAILED[@]}"; do echo "  - ${f}"; done
    exit 1
fi
echo "All ${#TRIPLES[@]} ffmpeg pins OK."
