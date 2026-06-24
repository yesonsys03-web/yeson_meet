# Windows freeze of the packaged yeson-server console into a PyInstaller --onedir
# bundle, staged where Tauri's bundle.resources expects it. PowerShell mirror of
# build-server.sh (P0 spike, step 1 — freeze must run NATIVELY on Windows; there
# is no cross-compile from macOS/Linux).
#
# Differences from build-server.sh that matter on Windows:
#   - venv interpreter is <venv>\Scripts\python.exe (not bin/).
#   - PyInstaller --add-data uses ';' as the src;dest separator (':' on POSIX).
#   - entry binary is yeson-server.exe; triple is x86_64-pc-windows-msvc.
#
# Prereqs on the Windows host: uv (https://docs.astral.sh/uv), Python 3.12
# available to uv, pnpm + Node (for the apps/web build), Visual C++ runtime.
#
# Usage (from anywhere):  pwsh apps/server_desktop/scripts/build-server.ps1
#   -SkipCloudflared : freeze the server only, skip vendoring cloudflared (used
#                      by the P0 spike CI, which exercises the freeze + teardown
#                      and does not need the tunnel binary).
param(
    [switch]$SkipCloudflared
)
$ErrorActionPreference = "Stop"

# repo root = scripts\..\..\.. (apps/server_desktop/scripts -> repo root)
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
Set-Location $Root
if (-not (Test-Path "apps/server_desktop/sidecar/server_entry.py")) {
    throw "repo root detection failed (cwd: $Root)"
}

$PyVersion  = "3.12"
$BuildVenv  = "target/server-build-venv"
$Dist       = "target/server-dist"
$Work       = "target/server-build"
$VenvPython = Join-Path $BuildVenv "Scripts/python.exe"

Write-Host "Preparing Python $PyVersion build venv (server deps + pyinstaller)..."
uv venv --clear --python $PyVersion $BuildVenv
# Install the server project + PyInstaller into the build venv.
$env:VIRTUAL_ENV = $BuildVenv
uv pip install --python $VenvPython ./apps/server "pyinstaller>=6.21"

# Build the viewer SPA so the frozen server serves it under the same :8000 origin
# as /api + /ws (replacing the old Docker-path Caddy). Staged via --add-data.
Write-Host "Building viewer SPA (apps/web -> dist)..."
pnpm -C apps/web install --frozen-lockfile
pnpm -C apps/web build
if (-not (Test-Path "apps/web/dist/index.html")) {
    throw "apps/web build produced no dist/index.html"
}

Write-Host "Building yeson-server (PyInstaller --onedir, Gemini-only)..."
# Same Gemini-only flag set as build-server.sh. NOTE the ';' add-data separator.
$WebDist = (Resolve-Path "apps/web/dist").Path
& $VenvPython -m PyInstaller `
    --noconfirm --clean --onedir `
    --name yeson-server `
    --paths . `
    --collect-submodules grpc `
    --collect-data grpc `
    --hidden-import grpc._cython.cygrpc `
    --collect-all google.genai `
    --collect-submodules google.api_core `
    --hidden-import aiosqlite `
    --collect-all docx `
    --hidden-import lxml._elementpath `
    --collect-submodules lxml `
    --add-data "$WebDist;web_dist" `
    --distpath $Dist `
    --workpath $Work `
    --specpath $Work `
    apps/server_desktop/sidecar/server_entry.py

$OutDir = Join-Path $Dist "yeson-server"
$OutBin = Join-Path $OutDir "yeson-server.exe"
if (-not (Test-Path $OutBin)) {
    throw "expected binary at $OutBin"
}

$Triple  = "x86_64-pc-windows-msvc"
$DestDir = "apps/server_desktop/src-tauri/binaries/yeson-server-$Triple"
if (Test-Path $DestDir) { Remove-Item -Recurse -Force $DestDir }
New-Item -ItemType Directory -Force -Path (Split-Path $DestDir) | Out-Null
Copy-Item -Recurse $OutDir $DestDir
$SizeMB = [math]::Round((Get-ChildItem -Recurse $DestDir | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "-> $DestDir"
Write-Host "  bundle size: ${SizeMB} MB"
Write-Host "  entry binary: $DestDir/yeson-server.exe"

# S7: frozen-bundle report smoke test - fails the build if the freeze cannot
# produce a report (catches python-docx/lxml missing from the bundle). Runs
# before the (optionally skipped) cloudflared step so it always executes. Invoked
# as a child process so its exit code is captured without ending this script.
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "smoke-server-bundle.ps1")
if ($LASTEXITCODE -ne 0) { throw "frozen-bundle report smoke test failed" }

# P4.3: vendor the Windows cloudflared so the tauri.conf binaries/cloudflared-*
# resource glob is satisfied at `tauri build`. Idempotent (skips if present).
# The P0 spike CI passes -SkipCloudflared (it tests the freeze, not the tunnel).
if ($SkipCloudflared) {
    Write-Host "Skipping cloudflared vendoring (-SkipCloudflared)."
    return
}
$CfDir = "apps/server_desktop/src-tauri/binaries/cloudflared-$Triple"
$CfBin = Join-Path $CfDir "cloudflared.exe"
if (Test-Path $CfBin) {
    Write-Host "cloudflared already vendored: $CfBin"
} else {
    Write-Host "Vendoring cloudflared (windows-amd64)..."
    New-Item -ItemType Directory -Force -Path $CfDir | Out-Null
    Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $CfBin
    Write-Host "-> $CfBin"
}
