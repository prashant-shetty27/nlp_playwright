# setup.ps1 - one-time setup on Windows (run in PowerShell from the project folder)
#   1. creates the .venv
#   2. installs the Python packages
#   3. downloads the Chromium browser Playwright drives
#   4. creates .env from .env.example if you do not have one yet
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Host "Python is not installed. Install Python 3.11 from python.org (tick 'Add python.exe to PATH') and run this again."; exit 1 }
Write-Host "Using $((python --version) 2>&1)"

if (-not (Test-Path "$root\.venv")) {
  Write-Host "Creating the virtual environment (.venv) ..."
  python -m venv "$root\.venv"
}
$venvPy = "$root\.venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip
Write-Host "Installing packages (a few minutes the first time) ..."
& $venvPy -m pip install -r "$root\requirements.txt"
Write-Host "Downloading Chromium for Playwright ..."
& $venvPy -m playwright install chromium

if (-not (Test-Path "$root\.env")) {
  Copy-Item "$root\.env.example" "$root\.env"
  Write-Host ".env created from .env.example - open it in Notepad and fill in the values you were given."
}
foreach ($d in @("data\logs", "data\screenshots\runs", "data\plan_runs", "data\plan_reports", "data\testsigma_exports")) {
  New-Item -ItemType Directory -Force -Path "$root\$d" | Out-Null
}
if (-not (Test-Path "$root\config\environments.json") -and (Test-Path "$root\config\environments.example.json")) {
  Copy-Item "$root\config\environments.example.json" "$root\config\environments.json"
}
if (-not (Test-Path "$root\data\common\variables.json") -and (Test-Path "$root\data\common\variables.example.json")) {
  Copy-Item "$root\data\common\variables.example.json" "$root\data\common\variables.json"
}
Write-Host ""
Write-Host "Setup done. Start the portal by double-clicking start_portal.cmd"
