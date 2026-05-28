#!/usr/bin/env bash
set -euo pipefail

# Build the Python client sidecar into a standalone single-file executable and
# stage it where Tauri's externalBin expects it. Mirrors
# native_helper_mac/scripts/build-release.sh. Lean native-only: excludes
# numpy/sounddevice/samplerate (sounddevice fallback is dev-only).

# repo root = scripts/../../.. (apps/client_sidecar/scripts -> repo root)
cd "$(dirname "$0")/../../.."
[[ -f apps/client_sidecar/main.py ]] || { echo "ERROR: repo root detection failed (cwd: $(pwd))" >&2; exit 1; }

echo "Building yeson-sidecar (PyInstaller, lean native-only)…"
# --paths . puts the repo root on PyInstaller's analysis path so the entry's
# absolute `apps.client_sidecar.*` imports resolve (apps/ is a namespace dir).
uv run --project apps/client_sidecar pyinstaller \
    --noconfirm --clean --onefile \
    --name yeson-sidecar \
    --paths . \
    --collect-submodules truststore \
    --exclude-module sounddevice \
    --exclude-module samplerate \
    --exclude-module numpy \
    --distpath target/sidecar-dist \
    --workpath target/sidecar-build \
    --specpath target/sidecar-build \
    apps/client_sidecar/main.py

OUT="target/sidecar-dist/yeson-sidecar"
if [[ ! -f "$OUT" ]]; then
    echo "ERROR: expected binary at $OUT" >&2
    exit 1
fi

# Map host arch → Tauri target-triple suffix expected by externalBin.
# Single-arch (host) binary; universal (lipo) deferred to β-5 codesign.
case "$(uname -m)" in
    arm64)   TRIPLE="aarch64-apple-darwin" ;;
    x86_64)  TRIPLE="x86_64-apple-darwin" ;;
    *)
        echo "ERROR: unsupported host arch: $(uname -m)" >&2
        exit 1
        ;;
esac

DEST_BUNDLE="apps/desktop/src-tauri/binaries/yeson-sidecar-${TRIPLE}"
mkdir -p "$(dirname "$DEST_BUNDLE")"
cp "$OUT" "$DEST_BUNDLE"
echo "→ $DEST_BUNDLE"
echo "  size: $(stat -f%z "$DEST_BUNDLE") bytes"
