# Frozen-bundle report smoke test (S7) - Windows.
#
# Runs the staged PyInstaller server binary in YESON_REPORT_SELFTEST mode, which
# exercises every report builder (md/html/docx + summary, plus pdf when
# LibreOffice is present) and exits without starting uvicorn. Catches deps that
# pass in the dev venv but are missing from the frozen bundle (python-docx /
# lxml). Called at the end of build-server.ps1; fails the build on error.
$ErrorActionPreference = "Stop"

# repo root = scripts/../../.. (apps/server_desktop/scripts -> repo root)
Set-Location (Join-Path $PSScriptRoot "..\..\..")

$Bin = Get-ChildItem -Path "apps\server_desktop\src-tauri\binaries\yeson-server-*\yeson-server.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $Bin) {
    Write-Error "smoke: staged server binary not found - run build-server.ps1 first"
    exit 1
}

Write-Host "Frozen-bundle report smoke test ($($Bin.FullName))..."
$env:YESON_REPORT_SELFTEST = "1"
$out = & $Bin.FullName 2>&1
Remove-Item Env:\YESON_REPORT_SELFTEST -ErrorAction SilentlyContinue

$out | Where-Object { $_ -match "^SELFTEST" } | ForEach-Object { Write-Host $_ }
if (-not ($out -match "SELFTEST_RESULT=PASS")) {
    $out | ForEach-Object { Write-Error $_ }
    Write-Error "bundle report smoke FAILED - a report dependency is likely missing from the frozen bundle"
    exit 1
}
Write-Host "OK bundle report smoke PASS"

# Frozen-bundle search smoke test (S4): assert FTS5 engine present in the bundled
# sqlite AND the search index seeds (utterance/summary row counts match).
Write-Host "Frozen-bundle search smoke test ($($Bin.FullName))..."
$env:YESON_SEARCH_SELFTEST = "1"
$sout = & $Bin.FullName 2>&1
Remove-Item Env:\YESON_SEARCH_SELFTEST -ErrorAction SilentlyContinue

$sout | Where-Object { $_ -match "^SEARCH_SELFTEST" } | ForEach-Object { Write-Host $_ }
if ($sout -match "SEARCH_SELFTEST_RESULT=PASS") {
    Write-Host "OK bundle search smoke PASS"
    exit 0
}

$sout | ForEach-Object { Write-Error $_ }
Write-Error "bundle search smoke FAILED - FTS5 missing from the bundle or the index did not seed"
exit 1
