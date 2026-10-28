# Installs the global "kalshiedge" command for the current Windows user.
# No Administrator permission and no global Python packages required.
# Run once: powershell -NoProfile -ExecutionPolicy Bypass -File .\install-kalshiedge.ps1

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "KalshiEdge virtual environment missing: $python"
}

& $python -c "from kalshi_edge.launcher import main; print('KalshiEdge launcher module: verified')"
if ($LASTEXITCODE -ne 0) {
    throw "The package could not be imported. In the virtual environment, run: python -m pip install -e ."
}

$bin = Join-Path $env:LOCALAPPDATA "KalshiEdge\bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
$commandFile = Join-Path $bin "kalshiedge.cmd"

# CMD wrapper deliberately uses this repo's .venv Python, even from an unrelated
# current directory. Its argument forwarding works from PowerShell and cmd.exe.
$wrapper = @"
@echo off
cd /d "$repo"
"$python" -m kalshi_edge.launcher %*
exit /b %ERRORLEVEL%
"@
Set-Content -LiteralPath $commandFile -Value $wrapper -Encoding ascii

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$entries = @($userPath -split ";" | Where-Object { $_ -and $_.Trim() })
if (-not ($entries | Where-Object { $_.TrimEnd("\") -ieq $bin.TrimEnd("\") })) {
    $newPath = (@($entries) + @($bin)) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
}

if (-not (($env:Path -split ";") | Where-Object { $_.TrimEnd("\") -ieq $bin.TrimEnd("\") })) {
    $env:Path += ";$bin"
}

Write-Host ""
Write-Host "=== KALSHIEDGE COMMAND INSTALLED ===" -ForegroundColor Green
Write-Host "Command: kalshiedge start"
Write-Host "Wrapper: $commandFile"
Write-Host "Project: $repo"
Write-Host ""
Write-Host "Open a NEW PowerShell or Command Prompt window, then type:"
Write-Host "  kalshiedge start" -ForegroundColor Cyan
Write-Host ""
Write-Host "The launcher starts live predictions, official settlement tracking,"
Write-Host "and the dashboard at http://127.0.0.1:8899/, then opens your browser."
Write-Host "Keep the terminal running; use Ctrl+C to stop the program."
