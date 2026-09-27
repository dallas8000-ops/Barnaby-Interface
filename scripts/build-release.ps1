<#
.SYNOPSIS
  Build a Windows installer (NSIS + MSI) with the voice engine bundled.
.DESCRIPTION
  1. Freezes the engine into a single exe with PyInstaller.
  2. Places it where Tauri's externalBin expects it (target-triple suffix).
  3. Runs `tauri build` with the release config that declares the sidecar.
  Note: the torch-based TTS makes the engine exe large (~400 MB CPU build) and
  its first start slower while it unpacks. See README "Packaging".
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = "$root\engine\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "Run .\scripts\setup.ps1 first." -ForegroundColor Yellow; exit 1 }

& $py -m pip install pyinstaller --quiet
Push-Location "$root\engine"
& $py -m PyInstaller --noconfirm --onefile --name barnaby-engine `
    --collect-all kokoro --collect-all misaki --collect-all faster_whisper `
    --hidden-import sounddevice run_engine.py
Pop-Location

$triple = (rustc -vV | Select-String "host:").ToString().Split(" ")[1]
$bin = "$root\app\src-tauri\binaries"
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item "$root\engine\dist\barnaby-engine.exe" "$bin\barnaby-engine-$triple.exe" -Force

Push-Location "$root\app"
npx tauri build --config src-tauri\tauri.release.conf.json
Pop-Location
Write-Host "Installers: app\src-tauri\target\release\bundle\" -ForegroundColor Green
