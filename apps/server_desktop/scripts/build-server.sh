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

# Task 14: vendor the host-triple ffmpeg (subtitle burn-in + probing) so
# tauri.conf's `binaries/ffmpeg-*` resource glob is satisfied when `tauri
# build` packages the app. Idempotent — fetch-ffmpeg.sh skips the download if
# already present. Without this the packaged app's video-caption feature has
# no ffmpeg to fall back to (only PATH, which a plain user install may lack).
#
# 동결보다 먼저 받는다. 예전엔 이 블록이 스크립트 맨 끝이라, 핀이 썩었으면
# PyInstaller 동결과 스모크를 전부 통과한 **뒤에야** 죽었다(v1.8.0 Windows에서
# 실제로 그렇게 40분을 태웠다). 받는 총량은 같고 순서만 바뀐다 — 썩은 핀은 이제
# 몇 초 만에 드러난다. mac 쪽이 특히 중요한데, osxexperts URL은 메이저 버전만
# 담고 있어 같은 자리에서 재빌드되면 404가 아니라 sha256 불일치로 나타난다.
echo "Vendoring ffmpeg binary (Task 14)…"
bash apps/server_desktop/scripts/fetch-ffmpeg.sh

echo "Preparing Python ${PY_VERSION} build venv (server deps + pyinstaller)…"
uv venv --clear --python "${PY_VERSION}" "${BUILD_VENV}"
# Install the server project (pulls fastapi/uvicorn/grpc/google-genai/bcrypt/
# aiosqlite/sqlalchemy/passlib/…) plus PyInstaller into the build venv.
VIRTUAL_ENV="${BUILD_VENV}" uv pip install --python "${BUILD_VENV}/bin/python" \
    ./apps/server "pyinstaller>=6.21"

# RapidOCR는 cv2(opencv)를 import한다. 두 가지를 처리한다:
#  (1) 실체화: `uv pip install ./apps/server`가 opencv를 전이 설치할 때 cv2/ 패키지가
#      실체화되지 않고 .dist-info만 남는 uv 캐시 링크 이슈가 있다(2026-07-20 실측:
#      cv2/ 디렉터리 부재 → pyinstaller가 못 모음 → 번들에 cv2 누락 → 스캔 즉사).
#      --reinstall --no-cache로 cv2/를 강제 실체화한다.
#  (2) headless: 비-headless opencv는 Linux에서 libGL.so.1을 요구해 GUI 없는 서버
#      번들에서 import cv2가 즉사한다 → Linux만 headless로 교체. macOS/Windows는
#      libGL 문제가 없고 headless 특정 버전(5.0.0.93)이 실체화 실패 사례가 있어
#      non-headless를 유지한다.
if [[ "$(uname -s)" == "Linux" ]]; then
    VIRTUAL_ENV="${BUILD_VENV}" uv pip install --python "${BUILD_VENV}/bin/python" \
        --reinstall --no-cache opencv-python-headless
    VIRTUAL_ENV="${BUILD_VENV}" uv pip uninstall --python "${BUILD_VENV}/bin/python" \
        opencv-python || true
else
    VIRTUAL_ENV="${BUILD_VENV}" uv pip install --python "${BUILD_VENV}/bin/python" \
        --reinstall --no-cache opencv-python
fi
# cv2 실체화 검증 — 여기서 실패하면 번들에 cv2가 빠져 스캔이 즉사하므로 빌드를 멈춘다.
"${BUILD_VENV}/bin/python" -c "import cv2; print('build-venv cv2 OK', cv2.__version__)"

# PDF 번역(Task 1~11)이 쓰는 pymupdf(fitz)도 cv2와 같은 uv 캐시 미실체화 함정에
# 노출된다(2026-07-20 cv2 실측과 동일 클래스 이슈) — 실패 시에만 강제 재설치해
# 정상 케이스의 빌드 시간을 늘리지 않는다.
if ! "${BUILD_VENV}/bin/python" -c "import pymupdf, fitz" 2>/dev/null; then
    echo "pymupdf/fitz import 실패 — 강제 재설치 후 재검증…" >&2
    VIRTUAL_ENV="${BUILD_VENV}" uv pip install --python "${BUILD_VENV}/bin/python" \
        --reinstall --no-cache pymupdf
    "${BUILD_VENV}/bin/python" -c "import pymupdf, fitz"
fi
"${BUILD_VENV}/bin/python" -c "import pymupdf; print('build-venv pymupdf OK', pymupdf.__doc__)"

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
    --collect-all rapidocr_onnxruntime \
    --collect-all shapely \
    --collect-all pyclipper \
    --collect-all cv2 \
    --collect-all PIL \
    --collect-all yt_dlp \
    --collect-all pymupdf \
    --hidden-import fitz \
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

# MLX Metal 커널: libmlx는 자기 dylib과 "같은 디렉터리"에서 mlx.metallib을 찾는데,
# PyInstaller가 libmlx.dylib을 _internal 루트로 복제하므로 metallib도 루트에 있어야
# 한다 (없으면 워커가 "Failed to load the default metallib"로 즉사 — 2026-07-12 E2E 실측).
# collect-all이 넣어주는 _internal/mlx/lib/mlx.metallib을 루트로 하드링크한다.
if [[ -f "${OUT_DIR}/_internal/mlx/lib/mlx.metallib" ]]; then
    ln -f "${OUT_DIR}/_internal/mlx/lib/mlx.metallib" "${OUT_DIR}/_internal/mlx.metallib"
    echo "mlx.metallib linked to _internal root (metal kernel colocated fix)"
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
# cp -R이 하드링크를 별도 파일로 풀어 155MB metallib이 중복되므로 다시 결합.
if [[ -f "${DEST_DIR}/_internal/mlx/lib/mlx.metallib" ]]; then
    ln -f "${DEST_DIR}/_internal/mlx/lib/mlx.metallib" "${DEST_DIR}/_internal/mlx.metallib"
fi
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
