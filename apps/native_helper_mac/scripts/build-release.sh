#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Building YesonMacAudioHelper (release)…"
swift build -c release

OUT="$(swift build -c release --show-bin-path)/YesonMacAudioHelper"
if [[ ! -f "$OUT" ]]; then
    echo "ERROR: expected binary at $OUT" >&2
    exit 1
fi

# Copy to a stable location consumed by Python sidecar (apps/client_sidecar/config/audio.py)
DEST="../../target/native-helper-mac/yeson-mac-audio-helper"
mkdir -p "$(dirname "$DEST")"
cp "$OUT" "$DEST"
echo "→ $DEST"
echo "size: $(stat -f%z "$DEST") bytes"
