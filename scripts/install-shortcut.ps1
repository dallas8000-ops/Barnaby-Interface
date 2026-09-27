<#
.SYNOPSIS
  Build Barnaby as a standalone desktop app and add Desktop + Start menu shortcuts.
.DESCRIPTION
  Produces app\src-tauri\target\release\barnaby.exe (no console window). It runs the
  voice engine from engine\.venv in this folder, so keep this folder where it is.
  Re-run after pulling changes to rebuild. Use -NoBuild to only (re)create shortcuts.
#>
param([switch]$NoBuild)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "app\src-tauri\target\release\barnaby.exe"

if (-not (Test-Path "$root\engine\.venv\Scripts\python.exe")) {
    Write-Host "Run .\scripts\setup.ps1 first." -ForegroundColor Yellow; exit 1
}
if (-not $NoBuild) {
    $cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
    if ((Test-Path $cargoBin) -and ($env:Path -notlike "*$cargoBin*")) { $env:Path += ";$cargoBin" }
    Push-Location "$root\app"
    npx tauri build --no-bundle
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Write-Host "Build failed (exit $code)." -ForegroundColor Red; exit 1 }
}
if (-not (Test-Path $exe)) { Write-Host "Not built yet: $exe" -ForegroundColor Red; exit 1 }

$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Barnaby.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Barnaby.lnk")
)
foreach ($lnk in $targets) {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = $exe
    $s.WorkingDirectory = Split-Path $exe
    $s.IconLocation = "$exe,0"
    $s.Description = "Barnaby: voice interface for Claude Code"
    $s.Save()
    Write-Host "  ok $lnk" -ForegroundColor Green
}
Write-Host "Barnaby is on your Desktop and in the Start menu." -ForegroundColor Cyan
