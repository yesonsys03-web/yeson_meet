#!/usr/bin/env bash
set -euo pipefail

# Frozen-bundle report smoke test (S7).
#
# Runs the *staged* PyInstaller server binary in YESON_REPORT_SELFTEST mode,
# which exercises every report builder (md/html/docx + summary, plus pdf when
# LibreOffice is present) and exits without starting uvicorn. This catches deps
# that pass in the dev venv but are missing from the frozen bundle (python-docx
# / lxml are the usual offenders).
#
# Called automatically at the end of build-server.sh; can also be run by hand.
# Exits non-zero (failing the build) if the bundle cannot produce a report.

# repo root = scripts/../../.. (apps/server_desktop/scripts -> repo root)
cd "$(dirname "$0")/../../.."

BIN="$(ls apps/server_desktop/src-tauri/binaries/yeson-server-*/yeson-server 2>/dev/null | head -1 || true)"
if [[ -z "${BIN}" || ! -x "${BIN}" ]]; then
    echo "smoke: staged server binary not found — run build-server.sh first" >&2
    exit 1
fi

echo "Frozen-bundle report smoke test (${BIN})…"
# The selftest path never starts uvicorn, so it returns in seconds; a generous
# 120s ceiling guards against a mis-built binary that falls through to the server.
if out="$(YESON_REPORT_SELFTEST=1 "${BIN}" 2>&1)"; then
    echo "${out}" | grep -E "^SELFTEST" || true
    if echo "${out}" | grep -q "SELFTEST_RESULT=PASS"; then
        echo "✓ bundle report smoke PASS"
        exit 0
    fi
fi

echo "${out:-}" >&2
echo "✗ bundle report smoke FAILED — a report dependency is likely missing from the frozen bundle" >&2
exit 1
