# start_portal.ps1 - start the Codeless Automation portal on http://localhost:8100
#
# Every start also:
#   1. gets the latest test cases / elements / portal fixes the team shared (git pull)
#   2. installs any new Python packages the update needs
#   3. makes sure .env has the settings a teammate's laptop needs
# so nobody has to type git or pip commands. Each step is skipped quietly when
# it is not possible (no network, not a git checkout).
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$venvPy = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Write-Host "Run setup.cmd first (double-click it)."; exit 1 }

if (Test-Path "$root\.git") {
  Write-Host "Getting the latest from the team ..."
  $before = (git rev-parse HEAD 2>$null)
  git pull --ff-only --quiet 2>&1 | Out-Null
  $after = (git rev-parse HEAD 2>$null)
  if ($before -ne $after) {
    Write-Host "Updated. Making sure all packages are installed ..."
    & $venvPy -m pip install -q -r "$root\requirements.txt"
    & $venvPy -m playwright install chromium 2>&1 | Out-Null
  } else {
    Write-Host "Already up to date."
  }
}

if (-not (Test-Path "$root\.env")) { Copy-Item "$root\.env.example" "$root\.env" }
# Scheduled plans run only on the machine that owns them. A laptop that has never
# said "I own the schedules" (SCHEDULER_OWNER=1) keeps the scheduler off.
$envText = Get-Content "$root\.env" -Raw
if ($envText -notmatch "(?m)^\s*DISABLE_SCHEDULER\s*=" -and $envText -notmatch "(?m)^\s*SCHEDULER_OWNER\s*=\s*1") {
  Add-Content "$root\.env" "`nDISABLE_SCHEDULER=1"
}

Write-Host "Portal starting on http://localhost:8100  (keep this window open; close it to stop)"
& $venvPy -m ui.app --port 8100 --show
