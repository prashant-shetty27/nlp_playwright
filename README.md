# NLP Playwright Automation Framework

## Overview
This project provides a modular, NLP-driven automation framework using Playwright for browser automation and Python for backend logic. It supports:
- Natural language test steps
- Dynamic locator management
- Visual regression (image comparison)
- Robust error handling and logging
- Extensible snippet and keyword system

## Common Commands
```sh
.venv/bin/python runner.py flows/full_demo.flow 2>&1
.venv/bin/python plan_runner.py plans/android_plan.json
```

## Setup on Windows (PowerShell)
```powershell
git clone https://github.com/prashant-shetty27/nlp_playwright.git
cd nlp_playwright
.\setup.ps1          # one time: .venv, packages, Chromium, .env from .env.example
.\start_portal.ps1   # every day: portal on http://localhost:8100
```
Fill in `.env` with the values your team lead gives you (it is never committed).

## Setup
1. **Clone the repository**
2. **Install dependencies (recommended):**
   ```sh
   ./setup.sh
   ```
   Manual alternative:
   ```sh
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   python -m playwright install
   ```
3. **Start recorder (optional):**
   ```sh
   ./start_recorder.sh web
   ./start_recorder.sh web --port 8081 --force
   ./start_recorder.sh app recorder --force
   ./start_recorder.sh app android --caps suites/android_suite.json --with-appium
   ./start_recorder.sh app ios --caps suites/ios_suite.json --port 8091
   ```
   iOS recorder runs a signing preflight before connecting. To check manually:
   ```sh
   .venv/bin/python execution/ios_readiness.py --caps suites/ios_suite.json
   ```
<!-- ./run_ios.sh --skip-tunnel          # if tunnel is already running
<!-- ./run_ios.sh --keep-running         # keep Appium/tunnel alive after run -->
<!-- ./run_ios.sh --port 4725            # custom Appium port --> -->

# https://ps-ai-testcase-generator-production.up.railway.app/runs/latest
# https://ps-ai-testcase-generator-production.up.railway.app/admin/runs

## Usage
- **Run a test flow:**
  ```sh
  python runner.py steps.flow
  # or
  ./run.sh
  ```
- **Run Android plan:**
  ```sh
  python plan_runner.py plans/android_plan.json
  ```
- **Add/Update locators:**
  Edit `locators_manual.json` and re-run your flows.
- **Sync VS Code snippets:**
  ```sh
  python -m reporting.snippet_sync
  ```
- **Visual regression:**
  Use `core/image_utils.py` for template matching and SSIM-based checks.

## Centralized Controllers
Global feature switches now live in `config/controllers.json`.

Examples:
- runtime target (`runtime.execution_target`: `local` / `cloud`)
- browser mode (`browser.headless`)
- capture controls (`capture.screenshots_enabled`, `capture.video_enabled`)
- notifications (`notifications.slack_enabled`, `notifications.email_enabled`)
- reporting (`reporting.enabled`)
- rerun behavior (`execution_defaults.rerun_on_failure`)
- Appium URL (`mobile.appium_server_url`)
- Android lifecycle defaults (`android_lifecycle_defaults.*`)

Current defaults are intentionally **off** for execution toggles:
- report generation
- screenshots
- video recording
- Slack notifications
- Email notifications

Priority order:
1. Environment variable / `.env` value (highest)
2. `config/controllers.json` default
3. Built-in fallback in code

## Runtime Profiles (Save / Reuse / Edit / Delete)
You can manage reusable execution preferences across sessions:

- List profiles:
  - `python plan_runner.py --list-profiles`
  - `python runner.py --list-profiles`
- Ask runtime config interactively for this run:
  - `python plan_runner.py plans/android_plan.json --ask-config`
  - `python runner.py flows/steps.flow --ask-config`
- Save selected config as named profile:
  - `--save-profile prashant_config_web`
- Use an existing profile:
  - `--profile prashant_config_web`
- Edit profile:
  - run with `--profile <name> --ask-config --save-profile <same_name>`
- Delete profile:
  - `--delete-profile <name>`

Runtime profile options include:
- execution target: `local` or `cloud`
- rerun suite on failure: on/off
- report on/off
- screenshot on/off
- video on/off
- Slack on/off
- Email on/off
- headless on/off

## Project Structure
- `runner.py` - Unified NLP/JSON flow runner
- `execution/action_service.py` - Core Playwright actions and snippet bindings
- `locators/manager.py` - Locator storage and retrieval
- `nlp/parser.py` - NLP command parser
- `reporting/snippet_sync.py` - VS Code snippet generator
- `core/image_utils.py` - Image comparison utilities
- `requirements.txt` - Python dependencies
- `steps.flow` - Example test flow

## Adding New Steps
- Add parser syntax in `nlp/parser.py` and implement action handlers in `execution/action_service.py`.
- Update `reporting/snippet_sync.py` to expose new actions as snippets.

## Contributing
- Please ensure all new code is documented and tested.
- Run `pytest` for backend tests (add tests in a `tests/` folder).

## Troubleshooting
- If locators are not found, check `locators_manual.json` and use `locators/cleaner.py`.
- For Playwright errors, ensure browsers are installed with `playwright install`.
- For iOS `xcodebuild code 65`, run:
  ```sh
  .venv/bin/python execution/ios_readiness.py --caps suites/ios_suite.json
  ```
  and fix team/certificate/profile errors before rerunning Appium.

## License
MIT

<!-- 
M = modified
U = untracked (new file)
A = added (staged new file)
D = deleted
R = renamed -->
