# start_portal.ps1 — start the Codeless Automation portal on http://localhost:8100
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$venvPy = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Write-Host "Run .\setup.ps1 first."; exit 1 }
Write-Host "Portal starting on http://localhost:8100  (keep this window open; Ctrl+C stops it)"
& $venvPy -m ui.app --port 8100 --show
