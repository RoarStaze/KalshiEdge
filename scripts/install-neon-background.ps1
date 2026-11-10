# Installs the locally supplied KalshiEdge background image without editing files.
# Usage: pwsh -NoProfile -File .\scripts\install-neon-background.ps1
[CmdletBinding()]
param(
    [string]$Source = (Join-Path $HOME 'Downloads\kalshiedge-neon-city.webp')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
    throw "Background file not found: $Source. Save the supplied WebP image there or pass -Source."
}
$file = Get-Item -LiteralPath $Source
if ($file.Length -lt 12 -or $file.Length -gt 3000000) {
    throw 'Background must be a valid WebP image smaller than 3 MB.'
}
$bytes = [System.IO.File]::ReadAllBytes($file.FullName)
if ([System.Text.Encoding]::ASCII.GetString($bytes, 0, 4) -ne 'RIFF' -or
    [System.Text.Encoding]::ASCII.GetString($bytes, 8, 4) -ne 'WEBP') {
    throw 'Background asset must be WebP (not a renamed PNG/JPEG).'
}

$venvPython = Join-Path $repo '.venv\Scripts\python.exe'
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { 'python' }

Push-Location $repo
try {
    $dataRoot = (& $python -c 'from kalshi_edge.bootstrap.config import BootstrapSettings; print(BootstrapSettings().bootstrap_dir.resolve())') -join ''
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($dataRoot)) {
        throw 'Could not resolve KalshiEdge bootstrap data directory. Activate its environment first.'
    }
    $assetDir = Join-Path $dataRoot.Trim() 'assets'
    [void](New-Item -ItemType Directory -Path $assetDir -Force)
    $destination = Join-Path $assetDir 'kalshiedge-neon-city.webp'
    Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    Write-Host "Installed KalshiEdge cinematic background:" -ForegroundColor Green
    Write-Host $destination
    Write-Host 'Restart KalshiEdge after updating its code, then refresh the website.'
}
finally {
    Pop-Location
}
