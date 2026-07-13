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

# Map host arch → Tauri target-triple suffix expected by externalBin.
# Note: this produces a single-arch binary matching the host. For a universal
# (x86_64 + arm64) release, run swift build per arch and lipo them — out of
# scope until Phase 4 codesign/distribution.
case "$(uname -m)" in
    arm64)   TRIPLE="aarch64-apple-darwin" ;;
    x86_64)  TRIPLE="x86_64-apple-darwin" ;;
    *)
        echo "ERROR: unsupported host arch: $(uname -m)" >&2
        exit 1
        ;;
esac

# 1) Stable dev location consumed by Python sidecar via
#    apps/client_sidecar/config/audio.py:NATIVE_HELPER_BIN_PATH default.
DEST_DEV="../../target/native-helper-mac/yeson-mac-audio-helper"
mkdir -p "$(dirname "$DEST_DEV")"
cp "$OUT" "$DEST_DEV"
echo "→ $DEST_DEV"
echo "  size: $(stat -f%z "$DEST_DEV") bytes"

# 2) Tauri externalBin staging — packaged into the .app bundle's Contents/MacOS/
#    (suffix is stripped at bundle time). Consumed by tauri.macos.conf.json
#    and located at runtime by sidecar.rs::locate_bundled_native_helper.
DEST_BUNDLE="../desktop/src-tauri/binaries/yeson-mac-audio-helper-${TRIPLE}"
mkdir -p "$(dirname "$DEST_BUNDLE")"
cp "$OUT" "$DEST_BUNDLE"
echo "→ $DEST_BUNDLE"
echo "  size: $(stat -f%z "$DEST_BUNDLE") bytes"

# 애드혹 코드서명 — 이 바이너리가 회의 오디오 캡처(ScreenCaptureKit)를 하는
# 프로세스라 TCC(화면 기록)가 서명을 검증한다. 서명이 무효면 macOS가 헬퍼를
# 앱 소속으로 인식 못 해 사용자가 권한을 켜도 ScreenCapture를 거부한다(자막 무음;
# macOS 26 Tahoe 실측 -67056, 2026-07-13). tauri의 externalBin deep-sign에만
# 의존하지 않도록 번들 전에 여기서 확실히 서명한다.
codesign --force --sign - "$DEST_BUNDLE"
codesign --verify --strict "$DEST_BUNDLE" && echo "  codesigned (ad-hoc) ✓"
