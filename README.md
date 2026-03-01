# NLP Playwright Automation Framework

## Overview
This project provides a modular, NLP-driven automation framework using Playwright for browser automation and Python for backend logic. It supports:
- Natural language test steps
- Dynamic locator management
- Visual regression (image comparison)
- Robust error handling and logging
- Extensible snippet and keyword system
# .venv/bin/python runner.py flows/full_demo.flow 2>&1
 ## Setup
1. **Clone the repository**
2. **Install dependencies:**
   ```sh
   pip install -r requirements.txt
   playwright install
   ```
3. **Run setup script (optional):**
   ```sh
   ./setup.sh
   ```

## Usage
- **Run a test flow:**
  ```sh
  python runner.py steps.flow
  # or
  ./run.sh
  ```
- **Add/Update locators:**
  Edit `locators_manual.json` and re-run your flows.
- **Sync VS Code snippets:**
  ```sh
  python -m reporting.snippet_sync
  ```
- **Visual regression:**
  Use `core/image_utils.py` for template matching and SSIM-based checks.

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

## License
MIT

<!-- 
M = modified
U = untracked (new file)
A = added (staged new file)
D = deleted
R = renamed -->