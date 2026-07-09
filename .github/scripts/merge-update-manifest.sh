#!/usr/bin/env bash
# Merge THIS platform's updater entry into the per-app update manifest attached
# to the GitHub release, then re-upload it. Called by each installer workflow
# AFTER the release is published. Runs on both macOS and Windows runners
# (Windows uses git-bash via `shell: bash`; gh + jq are preinstalled on both).
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

# Start from the manifest already on the release (the other platform's workflow
# may have run first); fall back to an empty object for the first platform.
if ! gh release download "v$VERSION" --repo "$REPO" --pattern "$MANIFEST" --dir . --clobber 2>/dev/null; then
  echo "{}" > "$MANIFEST"
fi

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

echo "merged $PLATFORM_KEY into $MANIFEST:"
cat "$MANIFEST"

gh release upload "v$VERSION" "$MANIFEST" --repo "$REPO" --clobber
