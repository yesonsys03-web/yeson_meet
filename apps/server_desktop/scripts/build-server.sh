#!/usr/bin/env bash
set -euo pipefail

# Build the packaged yeson-server console into a standalone PyInstaller --onedir
# bundle and stage it where Tauri's externalBin expects it.
#
# NET-NEW (Slice 2): unlike apps/client_sidecar/scripts/build-sidecar.sh (which
# only collects `truststore`), this freezes the heavy native server deps
# proven in the Slice 0 probe: grpc + google-genai + bcrypt. Gemini-only —
# google.cloud.speech/translate are intentionally NOT collected (they are the
# heaviest, most Windows-fragile surface and the server's google.cloud imports
# are lazy, so the Gemini path never loads them). See .omc/plans/open-questions.md.
#
# Uses --onedir (NOT --onefile): the heavy grpc/genai deps + the SQLite WAL
# checkpoint on teardown are far more robust without onefile's temp-extract.

# repo root = scripts/../../.. (apps/server_desktop/scripts -> repo root)
cd "$(dirname "$0")/../../.."
[[ -f apps/server_desktop/sidecar/server_entry.py ]] || {
    echo "ERROR: repo root detection failed (cwd: $(pwd))" >&2
    exit 1
}

PY_VERSION="3.12"
BUILD_VENV="target/server-build-venv"
DIST="target/server-dist"
WORK="target/server-build"

echo "Preparing Python ${PY_VERSION} build venv (server deps + pyinstaller)…"
uv venv --clear --python "${PY_VERSION}" "${BUILD_VENV}"
# Install the server project (pulls fastapi/uvicorn/grpc/google-genai/bcrypt/
# aiosqlite/sqlalchemy/passlib/…) plus PyInstaller into the build venv.
VIRTUAL_ENV="${BUILD_VENV}" uv pip install --python "${BUILD_VENV}/bin/python" \
    ./apps/server "pyinstaller>=6.21"

# 하이브리드 B: 실리콘맥 번들에만 mlx-lm 포함 (인텔맥 회귀 방지 — 510741b 방침).
# macOS bash 3.2 + set -u에서 빈 배열 확장이 unbound variable 처리 → 아래 pyinstaller 호출에서 ${arr[@]+...} 가드 필수.
MLX_COLLECT_FLAGS=()
if [[ "$(uname -sm)" == "Darwin arm64" ]]; then
    echo "Adding mlx-lm (Apple Silicon only)…"
    VIRTUAL_ENV="${BUILD_VENV}" uv pip install --python "${BUILD_VENV}/bin/python" \
        './apps/server[mlx]'
    MLX_COLLECT_FLAGS=(--collect-all mlx --collect-all mlx_lm)
fi

# Build the viewer SPA (apps/web) so the frozen server serves it under the same
# :8000 origin as /api + /ws (replacing the old Docker-path Caddy). Staged into
# the bundle via PyInstaller --add-data below; main._web_dist_dir() reads it
# back from sys._MEIPASS/web_dist at runtime.
echo "Building viewer SPA (apps/web → dist)…"
pnpm -C apps/web install --frozen-lockfile
pnpm -C apps/web build
[[ -f apps/web/dist/index.html ]] || {
    echo "ERROR: apps/web build produced no dist/index.html" >&2
    exit 1
}

echo "Building yeson-server (PyInstaller --onedir, Gemini-only)…"
# --paths . puts the repo root on PyInstaller's analysis path so the entry's
# absolute `apps.server.*` / `apps.server_desktop.*` imports resolve.
#
# Flag set is the Slice-0-verified Gemini-only set:
#   grpc (genai needs it) + google.genai + google.api_core submodules + bcrypt.
#   NO --collect-all google.cloud.speech / google.cloud.translate.
"${BUILD_VENV}/bin/pyinstaller" \
    --noconfirm --clean --onedir \
    --name yeson-server \
    --paths . \
    --collect-submodules grpc \
    --collect-data grpc \
    --hidden-import grpc._cython.cygrpc \
    --collect-all google.genai \
    --collect-submodules google.api_core \
    --hidden-import aiosqlite \
    --collect-all docx \
    --hidden-import lxml._elementpath \
    --collect-submodules lxml \
    --collect-all faster_whisper \
    --collect-all ctranslate2 \
    --collect-all av \
    --collect-all onnxruntime \
    --collect-all yt_dlp \
    ${MLX_COLLECT_FLAGS[@]+"${MLX_COLLECT_FLAGS[@]}"} \
    --add-data "$(pwd)/apps/web/dist:web_dist" \
    --distpath "${DIST}" \
    --workpath "${WORK}" \
    --specpath "${WORK}" \
    apps/server_desktop/sidecar/server_entry.py

OUT_DIR="${DIST}/yeson-server"
OUT_BIN="${OUT_DIR}/yeson-server"
if [[ ! -x "${OUT_BIN}" ]]; then
    echo "ERROR: expected binary at ${OUT_BIN}" >&2
    exit 1
fi

# Map host arch → Tauri target-triple suffix expected by externalBin.
# (Same mapping as apps/client_sidecar/scripts/build-sidecar.sh.)
case "$(uname -m)" in
    arm64)   TRIPLE="aarch64-apple-darwin" ;;
    x86_64)  TRIPLE="x86_64-apple-darwin" ;;
    *)
        echo "ERROR: unsupported host arch: $(uname -m)" >&2
        exit 1
        ;;
esac

# Stage the whole onedir tree (binary + _internal libs) under the triple name.
DEST_DIR="apps/server_desktop/src-tauri/binaries/yeson-server-${TRIPLE}"
rm -rf "${DEST_DIR}"
mkdir -p "$(dirname "${DEST_DIR}")"
cp -R "${OUT_DIR}" "${DEST_DIR}"
echo "→ ${DEST_DIR}"
echo "  bundle size: $(du -sh "${DEST_DIR}" | cut -f1)"
echo "  entry binary: ${DEST_DIR}/yeson-server"

# S7: frozen-bundle report smoke test — fails the build if the freeze cannot
# produce a report (catches python-docx/lxml missing from the bundle).
bash apps/server_desktop/scripts/smoke-server-bundle.sh

# P4.3: vendor the host-triple cloudflared so tauri.conf's
# `binaries/cloudflared-*` resource glob is satisfied when `tauri build`
# packages the app (the ~50-70MB binary is gitignored + fetched per-host, never
# committed). Idempotent — fetch-cloudflared.sh skips the download if already
# present. Without this the packaged app would lack the public-tunnel binary.
echo "Vendoring cloudflared quick-tunnel binary (P4.3)…"
bash apps/server_desktop/scripts/fetch-cloudflared.sh

# Task 14: vendor the host-triple ffmpeg (subtitle burn-in + probing) so
# tauri.conf's `binaries/ffmpeg-*` resource glob is satisfied when `tauri
# build` packages the app. Idempotent — fetch-ffmpeg.sh skips the download if
# already present. Without this the packaged app's video-caption feature has
# no ffmpeg to fall back to (only PATH, which a plain user install may lack).
echo "Vendoring ffmpeg binary (Task 14)…"
bash apps/server_desktop/scripts/fetch-ffmpeg.sh
