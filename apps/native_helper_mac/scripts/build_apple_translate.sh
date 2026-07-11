#!/usr/bin/env bash
# apple-live-translate를 release 빌드해 server_desktop 번들 위치로 복사.
# 실리콘맥 + Xcode 26 SDK 전용 — 다른 플랫폼 빌드에서는 호출하지 않는다.
#
# Mirrors build-release.sh's dev+bundle staging pattern (host-arch triple
# mapping, mkdir -p + cp), but this product only ever ships as
# aarch64-apple-darwin (Apple Translation framework requires Apple Silicon),
# so the triple is not derived from `uname -m` — a non-arm64 host fails fast
# instead of silently staging a binary that can't run.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname -m)" != "arm64" ]]; then
    echo "ERROR: apple-live-translate is Apple Silicon only (host: $(uname -m))" >&2
    exit 1
fi

echo "Building apple-live-translate (release)…"
swift build -c release --product apple-live-translate

DEST="../server_desktop/src-tauri/binaries/apple-translate-aarch64-apple-darwin"
mkdir -p "$DEST"
cp ".build/release/apple-live-translate" "$DEST/"
echo "copied → $DEST/apple-live-translate"
