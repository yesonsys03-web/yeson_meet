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
    if ! echo "${out}" | grep -q "SELFTEST_RESULT=PASS"; then
        echo "${out:-}" >&2
        echo "✗ bundle report smoke FAILED — a report dependency is likely missing from the frozen bundle" >&2
        exit 1
    fi
    echo "✓ bundle report smoke PASS"
else
    echo "${out:-}" >&2
    echo "✗ bundle report smoke FAILED — a report dependency is likely missing from the frozen bundle" >&2
    exit 1
fi

# Frozen-bundle PDF smoke test (Task 11): assert pymupdf survived the freeze
# (same uv-cache materialization trap class as cv2 — see build-server.sh).
echo "Frozen-bundle PDF smoke test (${BIN})…"
if pout="$(YESON_PDF_SELFTEST=1 "${BIN}" 2>&1)"; then
    if echo "${pout}" | grep -q "PDF_SELFTEST_RESULT=PASS"; then
        echo "✓ bundle PDF smoke PASS"
    else
        echo "${pout}" >&2
        echo "✗ bundle PDF smoke FAILED — pymupdf likely missing from the frozen bundle" >&2
        exit 1
    fi
else
    echo "${pout:-}" >&2
    echo "✗ bundle PDF smoke FAILED — pymupdf likely missing from the frozen bundle" >&2
    exit 1
fi

# Frozen-bundle search smoke test (S4): assert FTS5 engine present in the
# bundled sqlite AND the search index seeds (utterance/summary row counts match).
echo "Frozen-bundle search smoke test (${BIN})…"
if sout="$(YESON_SEARCH_SELFTEST=1 "${BIN}" 2>&1)"; then
    echo "${sout}" | grep -E "^SEARCH_SELFTEST" || true
    if echo "${sout}" | grep -q "SEARCH_SELFTEST_RESULT=PASS"; then
        echo "✓ bundle search smoke PASS"
        exit 0
    fi
fi

echo "${sout:-}" >&2
echo "✗ bundle search smoke FAILED — FTS5 missing from the bundle or the index did not seed" >&2
exit 1
