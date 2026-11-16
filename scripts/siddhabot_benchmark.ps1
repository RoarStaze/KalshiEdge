<#
.SYNOPSIS
Install, run, or report a strictly separate SiddhaBot comparison.
No KalshiEdge training, predictor, model promotion, or trading setting is changed.
#>
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("Install","Serve","Capture","Report")]
    [string]$Mode
)
$ErrorActionPreference = "Stop"
$Project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Bench = Join-Path $Project "data\bootstrap\benchmarks\siddhabot"
$Checkout = Join-Path $Bench "repo"
$Venv = Join-Path $Bench "venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Source = "https://github.com/SiddhaBasu/kalshi-bot.git"
$Pinned = "00f6c52b74aa17dcb69971b4c9480db7d173bfcc"

if ($Mode -eq "Install") {
    New-Item -ItemType Directory -Force -Path $Bench | Out-Null
    if (-not (Test-Path (Join-Path $Checkout ".git"))) {
        & git clone $Source $Checkout
        if ($LASTEXITCODE -ne 0) { throw "Unable to clone original SiddhaBot repository." }
    }
    $remote = (& git -C $Checkout remote get-url origin).Trim()
    if ($LASTEXITCODE -ne 0 -or $remote -notin @($Source, "https://github.com/SiddhaBasu/kalshi-bot")) {
        throw "Existing checkout has an unexpected origin: $remote"
    }
    $dirty = & git -C $Checkout status --porcelain
    if ($LASTEXITCODE -ne 0 -or $dirty) { throw "SiddhaBot checkout has local edits. Refusing to discard them." }
    & git -C $Checkout fetch origin $Pinned
    if ($LASTEXITCODE -ne 0) { throw "Unable to fetch pinned SiddhaBot revision." }
    & git -C $Checkout checkout --detach $Pinned
    if ($LASTEXITCODE -ne 0) { throw "Unable to check out pinned SiddhaBot revision." }
    $actual = (& git -C $Checkout rev-parse HEAD).Trim()
    if ($actual -ne $Pinned) { throw "Revision verification failed." }
    $BasePython = Join-Path $Project ".venv\Scripts\python.exe"
    if (-not (Test-Path $BasePython)) {
        throw "KalshiEdge Python virtual environment missing: $BasePython"
    }
    if (-not (Test-Path $Python)) {
        & $BasePython -m venv $Venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create separate SiddhaBot Python environment." }
    }
    & $Python -m pip install --disable-pip-version-check -r (Join-Path $Checkout "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "SiddhaBot dependencies failed to install." }
    Write-Host "Pinned original SiddhaBot installed in isolation: $Checkout"
    Write-Host "Use separate terminals: -Mode Serve, -Mode Capture, then -Mode Report."
    return
}

if ($Mode -eq "Report") {
    $EdgePython = Join-Path $Project ".venv\Scripts\python.exe"
    if (-not (Test-Path $EdgePython)) { throw "Missing KalshiEdge .venv. Install KalshiEdge first." }
    Push-Location $Project
    try {
        & $EdgePython -m kalshi_edge.bootstrap.siddhabot_comparison
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Benchmark has no settled matched results yet, or capture has not started."
        }
    } finally { Pop-Location }
    return
}

if (-not (Test-Path $Python) -or -not (Test-Path (Join-Path $Checkout "backend\core\btc_paper_trading.py"))) {
    throw "SiddhaBot is not installed. Run -Mode Install first."
}

# Forced non-trading flags apply even if the user's shell carries old values.
$env:SIMULATION_MODE = "true"
$env:BTC_LIVE_TRADING_ENABLED = "false"
$env:BTC_PAPER_TRADING_ENABLED = "false"
$env:WEATHER_BOT_ENABLED = "false"
$env:WHALE_SCANNER_ENABLED = "false"
$env:DATABASE_URL = "sqlite:///" + ((Join-Path $Checkout "siddhabot_benchmark.sqlite3").Replace("\","/"))
Push-Location $Checkout
try {
    if ($Mode -eq "Serve") {
        Write-Host "Starting SiddhaBot's original data service (trade execution disabled)."
        & $Python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8001
        if ($LASTEXITCODE -ne 0) { throw "SiddhaBot service ended with code $LASTEXITCODE" }
    } elseif ($Mode -eq "Capture") {
        Write-Host "Capturing original SiddhaBot model at 480s. Keep KalshiEdge running separately."
        & $Python (Join-Path $Project "scripts\siddhabot_capture.py") --root (Join-Path $Project "data\bootstrap") --repo $Checkout
        if ($LASTEXITCODE -ne 0) { throw "SiddhaBot capture ended with code $LASTEXITCODE" }
    }
} finally { Pop-Location }
