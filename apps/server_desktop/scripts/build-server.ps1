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

# RapidOCR가 끌어온 opencv-python(비-headless)은 Linux에서 libGL.so.1을 요구해
# 서버 번들에서 import cv2가 즉사한다. 같은 cv2 모듈을 제공하는 headless로 교체.
uv pip install --python $VenvPython `
    opencv-python-headless
# opencv-python이 이미 없을 수도 있으니 실패해도 계속 진행(bash의 `|| true` 대응).
# $ErrorActionPreference=Stop + PowerShell 7.4의 네이티브 명령 오류 전파가 켜지면
# uninstall 비정상 종료가 스크립트를 중단시킬 수 있어 try/catch로 감싼다.
try { uv pip uninstall --python $VenvPython opencv-python } catch { }
$global:LASTEXITCODE = 0

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
    --collect-all faster_whisper `
    --collect-all ctranslate2 `
    --collect-all av `
    --collect-all onnxruntime `
    --collect-all rapidocr_onnxruntime `
    --collect-all shapely `
    --collect-all pyclipper `
    --collect-all cv2 `
    --collect-all PIL `
    --collect-all yt_dlp `
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

# Task 14: vendor the Windows ffmpeg binary so the tauri.conf binaries/ffmpeg-*
# resource glob is satisfied at `tauri build`. Without this, `tauri build` fails
# on the unmatched glob (v1.0.0 Windows CI에서 실제 발생). Fetched natively in
# PowerShell so CI (and local Windows builds) need no Git Bash step.
#
# The version/url/sha256 come from ffmpeg.lock.json — the SAME manifest
# fetch-ffmpeg.sh reads — so this path and the POSIX one cannot drift onto
# different ffmpeg builds. This used to hardcode its own URL to the rolling
# ffmpeg-master-latest (FFmpeg git trunk nightlies) with no integrity check.
# Idempotent via the .pinned stamp: a manifest bump re-pulls automatically.
$FfManifest = "apps/server_desktop/ffmpeg.lock.json"
if (-not (Test-Path $FfManifest)) { throw "manifest not found: $FfManifest" }
$FfPin = (Get-Content $FfManifest -Raw | ConvertFrom-Json).triples.$Triple
if (-not $FfPin) { throw "no ffmpeg pin for triple $Triple in $FfManifest" }

$FfDir   = "apps/server_desktop/src-tauri/binaries/ffmpeg-$Triple"
$FfBin   = Join-Path $FfDir $FfPin.bin
$FfStamp = Join-Path $FfDir ".pinned"
$FfWant  = "$($FfPin.version) $($FfPin.sha256)"

# The stamp may have been written by either fetcher, so normalise before
# comparing rather than depending on a trailing newline either way.
if ((Test-Path $FfBin) -and (Test-Path $FfStamp) -and
    ("$(Get-Content $FfStamp -Raw)".Trim() -eq $FfWant)) {
    Write-Host "ffmpeg already vendored at pinned $($FfPin.version): $FfBin"
} else {
    Write-Host "Vendoring ffmpeg $($FfPin.version) for $Triple..."
    New-Item -ItemType Directory -Force -Path $FfDir | Out-Null
    $FfTmp = Join-Path ([System.IO.Path]::GetTempPath()) "ffmpeg-dl-$PID"
    New-Item -ItemType Directory -Force -Path $FfTmp | Out-Null
    try {
        $FfZip = Join-Path $FfTmp "pkg.zip"
        # Invoke-WebRequest's progress bar makes large downloads crawl in CI.
        $OldProgress = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        try { Invoke-WebRequest -Uri $FfPin.url -OutFile $FfZip }
        finally { $ProgressPreference = $OldProgress }

        $FfActual = (Get-FileHash -Path $FfZip -Algorithm SHA256).Hash.ToLower()
        if ($FfActual -ne $FfPin.sha256.ToLower()) {
            throw ("sha256 mismatch — refusing to vendor $($FfPin.url)`n" +
                   "  expected: $($FfPin.sha256)`n  actual:   $FfActual`n" +
                   "The pinned artifact changed upstream, or the download was tampered with.`n" +
                   "If this is an intentional upgrade, bump version+url+sha256 in $FfManifest.")
        }

        Expand-Archive -Path $FfZip -DestinationPath $FfTmp -Force
        # `member` is the exact path inside the archive — the pin makes it
        # deterministic, so we no longer guess with a recursive filename search.
        $FfSrc = Join-Path $FfTmp ($FfPin.member -replace '/', '\')
        if (-not (Test-Path $FfSrc)) {
            throw "'$($FfPin.member)' not found inside the archive from $($FfPin.url)"
        }
        Copy-Item $FfSrc $FfBin -Force
        Set-Content -Path $FfStamp -Value $FfWant -NoNewline
    } finally {
        Remove-Item -Recurse -Force $FfTmp -ErrorAction SilentlyContinue
    }
    Write-Host "-> $FfBin"
    Write-Host "   pinned: $($FfPin.version) (sha256 verified)"
}
