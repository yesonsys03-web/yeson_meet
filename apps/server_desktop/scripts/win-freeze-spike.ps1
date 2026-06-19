# P0 — Windows freeze + teardown spike harness (GUI-free, measure-first).
#
# This automates ACs P0.1/P0.2/P0.3 for the frozen Windows yeson-server onedir
# WITHOUT the Tauri GUI, so the one real unknown — does closing the console
# orphan the server subtree on Windows? — is measured directly.
#
# KEY: server_process.rs::terminate_group on Windows does only `child.kill()`
# (cfg(not(unix)) branch), which TerminateProcess-es ONLY the top child handle.
# On macOS the equivalent kills the whole process GROUP. This harness reproduces
# that exact contract: it spawns the frozen server, then kills ONLY the top PID,
# then checks for an orphaned subtree. If nothing is orphaned, child.kill() is
# already sufficient on Windows (onedir uvicorn is a single process) and NO Job
# Object is needed. If an orphan survives, apply the Job Object fix in
# docs/P0-WINDOWS-FREEZE-SPIKE.md and re-run.
#
# The spike uses an ISOLATED temp DB/storage so it never touches real app data
# and the relaunch-clean check (P0.3) is deterministic.
#
# Usage:  pwsh apps/server_desktop/scripts/win-freeze-spike.ps1 [-Port 8000]
param(
    [int]$Port = 8000,
    [string]$Triple = "x86_64-pc-windows-msvc",
    # Isolated workspace for the spike's temp DB/storage/logs. CI passes
    # $RUNNER_TEMP so the log-upload step finds the files deterministically.
    [string]$WorkDir = (Join-Path $env:TEMP "yeson-p0-spike")
)
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$Bin  = Join-Path $Root "apps/server_desktop/src-tauri/binaries/yeson-server-$Triple/yeson-server.exe"
if (-not (Test-Path $Bin)) {
    throw "frozen server not found: $Bin`nRun build-server.ps1 first."
}

# Isolated spike workspace.
$Work    = $WorkDir
if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$DbPath  = Join-Path $Work "yeson-meet.db"
$Storage = Join-Path $Work "storage"
New-Item -ItemType Directory -Force -Path $Storage | Out-Null
$DbUrl   = "sqlite+aiosqlite:///" + ($DbPath -replace '\\','/')

# Env mirrors server_process.rs (JWT_SECRET is REQUIRED by the server).
function Set-ServerEnv {
    $env:DATABASE_URL    = $DbUrl
    $env:STORAGE_ROOT    = $Storage
    $env:PORT            = "$Port"
    $env:HOST            = "0.0.0.0"
    $env:JWT_SECRET      = "p0-spike-secret-not-for-production"  # vibelign: allow-secret — throwaway spike value, not a real key
    $env:YESON_AI_PROVIDER = "gemini_live"
    $env:PYTHONUTF8      = "1"
    $env:PYTHONIOENCODING = "utf-8"
}

function Wait-Health([int]$TimeoutSec = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$Port/api/v1/health"
            if ($r.StatusCode -eq 200) { return $true }
        } catch { Start-Sleep -Milliseconds 400 }
    }
    return $false
}

# Residual = any of our spike's server/python processes still alive. We match by
# image name AND command line referencing our isolated DB so we never count an
# unrelated python on the machine.
function Get-SpikeResiduals {
    Get-CimInstance Win32_Process -Filter "Name='yeson-server.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -and ($_.CommandLine -like "*$($Work -replace '\\','*')*" -or $_.Name -eq 'yeson-server.exe') }
}

$results = [ordered]@{}

# ---- P0.1: frozen onedir boots, create_schema produces tables, /health 200 ----
Write-Host "`n[P0.1] boot + schema + /health ..." -ForegroundColor Cyan
Set-ServerEnv
$proc = Start-Process -FilePath $Bin -PassThru -WindowStyle Hidden
Start-Sleep -Milliseconds 500
$healthy = Wait-Health 30
$dbHasTables = (Test-Path $DbPath) -and ((Get-Item $DbPath).Length -gt 4096)  # cold create_schema writes the ORM tables
$results["P0.1 boot+/health"] = $healthy
$results["P0.1 schema(db non-empty)"] = $dbHasTables
Write-Host "  /health 200: $healthy ; db tables: $dbHasTables"

# ---- P0.2: kill ONLY the top PID (mimics Rust child.kill()) -> orphan? --------
Write-Host "`n[P0.2] kill top PID only, then check for orphaned subtree ..." -ForegroundColor Cyan
$children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($proc.Id)" | Select-Object -Expand ProcessId
Write-Host "  server PID=$($proc.Id) ; direct children=$($children -join ',')"
Stop-Process -Id $proc.Id   # NOT -Force tree; exactly what child.kill() does
Start-Sleep -Seconds 3
$residual = @(Get-SpikeResiduals)
$noOrphan = ($residual.Count -eq 0)
$results["P0.2 no orphan after child.kill()"] = $noOrphan
if (-not $noOrphan) {
    Write-Host "  ORPHANS SURVIVED (Job Object needed):" -ForegroundColor Yellow
    $residual | ForEach-Object { Write-Host "    PID $($_.ProcessId)  $($_.Name)" }
    # Clean up so the relaunch check isn't poisoned by the orphan.
    $residual | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }
    Start-Sleep -Seconds 2
} else {
    Write-Host "  no residual server/python -> child.kill() already reaps the tree" -ForegroundColor Green
}

# ---- P0.3: relaunch against the SAME db -> port free, SQLite unlocked ---------
Write-Host "`n[P0.3] relaunch against same DB (port free + no 'database is locked') ..." -ForegroundColor Cyan
$portFree = -not (Test-NetConnection -ComputerName 127.0.0.1 -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue)
$logFile = Join-Path $Work "relaunch.log"
Set-ServerEnv
$proc2 = Start-Process -FilePath $Bin -PassThru -WindowStyle Hidden -RedirectStandardError $logFile -RedirectStandardOutput "$logFile.out"
$healthy2 = Wait-Health 30
$log = (Get-Content $logFile -ErrorAction SilentlyContinue) + (Get-Content "$logFile.out" -ErrorAction SilentlyContinue)
$dbLocked = ($log | Select-String -SimpleMatch "database is locked").Count -gt 0
$results["P0.3 port free before relaunch"] = $portFree
$results["P0.3 relaunch /health 200"] = $healthy2
$results["P0.3 no 'database is locked'"] = (-not $dbLocked)
Write-Host "  port free: $portFree ; relaunch /health: $healthy2 ; db-locked: $dbLocked"

# clean teardown of the relaunch (force-tree, this is cleanup not a measurement).
try { Stop-Process -Id $proc2.Id -Force } catch {}
Get-SpikeResiduals | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }

# ---- verdict -----------------------------------------------------------------
Write-Host "`n================ P0 SPIKE RESULT ================" -ForegroundColor Cyan
$allPass = $true
foreach ($k in $results.Keys) {
    $ok = [bool]$results[$k]
    if (-not $ok) { $allPass = $false }
    $tag = if ($ok) { "PASS" } else { "FAIL" }
    $color = if ($ok) { "Green" } else { "Red" }
    Write-Host ("  [{0}] {1}" -f $tag, $k) -ForegroundColor $color
}
Write-Host "================================================="
if ($allPass) {
    Write-Host "VERDICT: Windows freeze + current child.kill() teardown PASS." -ForegroundColor Green
    Write-Host "  -> No Job Object change required; P0 unknown retired. Proceed to P1."
} elseif (-not $results["P0.1 boot+/health"]) {
    Write-Host "VERDICT: FREEZE FAILED (P0.1). Trigger P0.4 uv-launcher contingency" -ForegroundColor Red
    Write-Host "  BEFORE any P1-P4 work (see docs/P0-WINDOWS-FREEZE-SPIKE.md)."
} elseif (-not $results["P0.2 no orphan after child.kill()"]) {
    Write-Host "VERDICT: ORPHAN ON CLOSE (P0.2). Apply the Job Object fix in" -ForegroundColor Yellow
    Write-Host "  docs/P0-WINDOWS-FREEZE-SPIKE.md (server_process.rs cfg(windows)), then re-run."
} else {
    Write-Host "VERDICT: relaunch-clean (P0.3) failed despite no orphan — inspect" -ForegroundColor Yellow
    Write-Host "  $logFile for the SQLite/port error."
}
exit ([int](-not $allPass))
