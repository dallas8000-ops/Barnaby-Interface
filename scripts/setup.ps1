<#
.SYNOPSIS
  One-time setup for Barnaby on Windows. Safe to re-run.
.DESCRIPTION
  Creates engine\.venv with the voice stack, installs UI dependencies, and
  checks the external prerequisites (Python 3.11/3.12, Node, Rust, Claude Code).
  Nothing is installed globally except via the tools you already have.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "  x $msg" -ForegroundColor Red; exit 1 }
function Ok($msg)   { Write-Host "  ok $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
# $ErrorActionPreference doesn't see native exit codes; check them explicitly.
function Check($what) { if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit code $LASTEXITCODE)" } }

Step "Checking prerequisites"
# Kokoro (TTS) supports Python < 3.13, so pick 3.12, then 3.11.
$py = $null
foreach ($v in "3.12", "3.11") {
    try { & py "-$v" --version *> $null; if ($LASTEXITCODE -eq 0) { $py = "-$v"; break } } catch {}
}
if (-not $py) { Fail "Python 3.12 or 3.11 not found. Install: winget install Python.Python.3.12" }
Ok "Python $py"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Fail "Node.js not found. Install: winget install OpenJS.NodeJS.LTS" }
Ok "Node $(node --version)"

# Rust is only needed to launch/build the desktop shell (dev.ps1), not for the
# engine or UI deps, so a missing toolchain warns instead of blocking setup.
$hasRust = [bool](Get-Command cargo -ErrorAction SilentlyContinue)
if ($hasRust) { Ok "Rust $(cargo --version)" }
else { Warn "Rust not found. Needed before dev.ps1: winget install Rustlang.Rustup (plus 'Desktop development with C++' build tools), then reopen the terminal." }

if (Get-Command claude -ErrorAction SilentlyContinue) { Ok "Claude Code $(claude --version)" }
else { Warn "Claude Code not on PATH. Install it, then run 'claude' once to sign in: https://docs.claude.com/en/docs/claude-code" }

if (-not (Get-Command code -ErrorAction SilentlyContinue)) { Warn "VS Code 'code' launcher not on PATH ('Open in VS Code' buttons need it)." }
if (-not (Get-Command cursor -ErrorAction SilentlyContinue)) { Warn "Cursor 'cursor' launcher not on PATH ('Open in Cursor' buttons need it)." }

Step "Creating engine virtualenv (engine\.venv)"
if (-not (Test-Path "engine\.venv\Scripts\python.exe")) { & py $py -m venv engine\.venv; Check "Creating the virtualenv" }
& engine\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet; Check "Upgrading pip"
# CPU build of torch keeps the download ~200 MB instead of ~2.5 GB of CUDA libs.
# If you have an NVIDIA GPU and want faster speech, see README "GPU".
& engine\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu --quiet; Check "Installing torch"
& engine\.venv\Scripts\python.exe -m pip install -e "engine[voice]" --quiet; Check "Installing the voice engine"
Ok "Voice engine installed"

Step "Running engine tests"
Push-Location engine
# unittest reports on stderr; route through cmd so PowerShell 5.1 doesn't treat it as a terminating error.
cmd /c ".venv\Scripts\python.exe -m unittest discover -s tests -t . 2>&1" | Select-Object -Last 3
$testsOk = ($LASTEXITCODE -eq 0)
Pop-Location
if (-not $testsOk) { Fail "Engine tests failed. Output above." }

Step "Installing UI dependencies"
Push-Location app
npm install --no-audit --no-fund; Check "npm install"
if (-not (Test-Path "src-tauri\icons\icon.ico")) {
    # Icons are generated from the scope mark rather than committed as binaries.
    npx tauri icon public\favicon.svg -o src-tauri\icons; Check "Generating app icons"
    Remove-Item -Recurse -Force src-tauri\icons\android, src-tauri\icons\ios -ErrorAction SilentlyContinue
}
Pop-Location

Step "Done"
if (-not $hasRust) { Warn "Install Rust + C++ build tools before starting the app (see warning above)." }
Write-Host "Start Barnaby:  .\scripts\dev.ps1" -ForegroundColor Cyan
Write-Host "First launch downloads the speech models (~250 MB for small.en + Kokoro), once."
