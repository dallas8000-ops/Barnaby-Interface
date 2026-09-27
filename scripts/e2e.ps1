<#
.SYNOPSIS
  Run Barnaby's end-to-end browser tests. Works from any folder.
.DESCRIPTION
  Starts the engine in mock mode with its own Python, and uses Playwright's own
  browser if the matching build is installed. Otherwise it uses the newest
  Chromium already in ms-playwright (e.g. one installed by the Playwright MCP),
  so no download is needed.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\app"
$env:PYTHON = ".venv\Scripts\python.exe"   # relative to the engine folder the tests start it in

$browsers = Join-Path $env:LOCALAPPDATA "ms-playwright"
$newest = Get-ChildItem $browsers -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue |
    Sort-Object { [int]($_.Name -replace '\D', '') } -Descending |
    ForEach-Object { Join-Path $_.FullName "chrome-win64\chrome.exe" } |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($newest) { $env:CHROME_PATH = $newest; Write-Host "Using browser: $newest" -ForegroundColor DarkGray }

npx playwright test
exit $LASTEXITCODE
