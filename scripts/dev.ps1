<#
.SYNOPSIS
  Run Barnaby as a desktop app in development mode (hot-reloading UI).
  The Rust shell starts the voice engine from engine\.venv automatically.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path "$root\engine\.venv\Scripts\python.exe")) {
    Write-Host "Run .\scripts\setup.ps1 first." -ForegroundColor Yellow
    exit 1
}
Set-Location "$root\app"
npm run tauri dev
