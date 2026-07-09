#!/usr/bin/env bash
# Merge THIS platform's updater entry into the per-app update manifest attached
# to the GitHub release, then re-upload it. Called by each installer workflow
# AFTER the release is published. Runs on both macOS and Windows runners
# (Windows uses git-bash via `shell: bash`; gh + jq are preinstalled on both).
#
# Concurrency: two platform workflows can race on the same manifest asset
# (read-modify-write). Defenses:
#   1. Asset existence is checked deterministically via the release API —
#      a download failure is never silently treated as "manifest missing".
#   2. After upload, the manifest is re-downloaded and verified (our entry
#      present + no previously-seen platform key lost). On mismatch (another
#      run clobbered in between) the merge retries from the latest manifest,
#      up to 3 attempts, then fails loudly.
#
# Required env:
#   VERSION       release version without the leading v, e.g. 1.1.4
#   REPO          owner/repo, e.g. yesonsys03-web/yeson_meet
#   MANIFEST      latest-client.json | latest-server.json
#   PLATFORM_KEY  darwin-aarch64 | windows-x86_64
#   ARTIFACT_GLOB glob to the updater artifact (…/*.app.tar.gz | …/*-setup.exe)
#   GH_TOKEN      token for gh (release download/upload)
set -euo pipefail

# shellcheck disable=SC2086  # ARTIFACT_GLOB must expand as a glob
ARTIFACT=$(ls $ARTIFACT_GLOB 2>/dev/null | head -n1 || true)
if [ -z "$ARTIFACT" ]; then
  echo "ERROR: no updater artifact matched: $ARTIFACT_GLOB" >&2
  exit 1
fi
SIG_FILE="$ARTIFACT.sig"
if [ ! -f "$SIG_FILE" ]; then
  echo "ERROR: missing signature $SIG_FILE — was TAURI_SIGNING_PRIVATE_KEY set on the build step?" >&2
  exit 1
fi
SIG=$(cat "$SIG_FILE")
FILENAME=$(basename "$ARTIFACT")
URL="https://github.com/$REPO/releases/download/v$VERSION/$FILENAME"
PUBDATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MANIFEST_NAME=$(basename "$MANIFEST")

# Fetch the manifest currently attached to the release into $MANIFEST.
# Seeds {} ONLY when the release verifiably has no such asset yet; any
# API or download failure is fatal (never silently start from scratch,
# which would drop the other platform's entry).
fetch_manifest() {
  local exists
  exists=$(gh api "repos/$REPO/releases/tags/v$VERSION" \
    --jq "[.assets[].name] | index(\"$MANIFEST_NAME\") != null" 2>/dev/null || echo "unknown")
  case "$exists" in
    true)
      if ! gh release download "v$VERSION" --repo "$REPO" --pattern "$MANIFEST_NAME" --dir . --clobber; then
        echo "ERROR: $MANIFEST_NAME exists on release v$VERSION but download failed" >&2
        exit 1
      fi
      ;;
    false)
      echo "{}" > "$MANIFEST"
      ;;
    *)
      echo "ERROR: could not list assets of release v$VERSION (gh api failed)" >&2
      exit 1
      ;;
  esac
}

MAX_ATTEMPTS=3
ATTEMPT=1
while :; do
  fetch_manifest

  # Platform keys present BEFORE our merge — none of these may be lost.
  BASE_KEYS=$(jq -r '(.platforms // {}) | keys[]' "$MANIFEST")

  jq \
    --arg version "$VERSION" \
    --arg pubdate "$PUBDATE" \
    --arg key "$PLATFORM_KEY" \
    --arg sig "$SIG" \
    --arg url "$URL" \
    '. + {
       version: $version,
       pub_date: (.pub_date // $pubdate),
       platforms: ((.platforms // {}) + { ($key): { signature: $sig, url: $url } })
     }' "$MANIFEST" > "$MANIFEST.tmp"
  mv "$MANIFEST.tmp" "$MANIFEST"

  echo "merged $PLATFORM_KEY into $MANIFEST (attempt $ATTEMPT/$MAX_ATTEMPTS):"
  cat "$MANIFEST"

  gh release upload "v$VERSION" "$MANIFEST" --repo "$REPO" --clobber

  # Verify after upload: re-download and assert (a) our entry survived with the
  # exact signature+url we wrote, and (b) every pre-merge platform key is still
  # present. A mismatch means a concurrent run clobbered the asset in between.
  VERIFY_DIR=$(mktemp -d)
  VERIFIED_OK=true
  if ! gh release download "v$VERSION" --repo "$REPO" --pattern "$MANIFEST_NAME" --dir "$VERIFY_DIR" --clobber; then
    VERIFIED_OK=false
  else
    VERIFIED="$VERIFY_DIR/$MANIFEST_NAME"
    if ! jq -e --arg key "$PLATFORM_KEY" --arg sig "$SIG" --arg url "$URL" \
      '.platforms[$key].signature == $sig and .platforms[$key].url == $url' \
      "$VERIFIED" >/dev/null; then
      VERIFIED_OK=false
    fi
    for k in $BASE_KEYS; do
      if ! jq -e --arg k "$k" '.platforms | has($k)' "$VERIFIED" >/dev/null; then
        VERIFIED_OK=false
      fi
    done
  fi
  rm -rf "$VERIFY_DIR"

  if [ "$VERIFIED_OK" = "true" ]; then
    echo "verified $PLATFORM_KEY entry on release v$VERSION"
    break
  fi

  if [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
    echo "ERROR: manifest verification failed after $MAX_ATTEMPTS attempts — concurrent clobber on $MANIFEST_NAME?" >&2
    exit 1
  fi
  ATTEMPT=$((ATTEMPT + 1))
  echo "verification failed (concurrent run clobbered $MANIFEST_NAME?) — retrying from latest manifest" >&2
  sleep $((RANDOM % 10 + 5))
done
