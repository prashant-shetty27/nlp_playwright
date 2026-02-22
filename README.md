# NLP Playwright Automation Framework

## Overview
This project provides a modular, NLP-driven automation framework using Playwright for browser automation and Python for backend logic. It supports:
- Natural language test steps
- Dynamic locator management
- Visual regression (image comparison)
- Robust error handling and logging
- Extensible snippet and keyword system

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
  python run.py steps.flow
  # or
  ./run.sh
  ```
- **Add/Update locators:**
  Edit `locators_manual.json` and re-run your flows.
- **Sync VS Code snippets:**
  ```sh
  python sync_snippets.py
  ```
- **Visual regression:**
  Use image_utils.py for template matching and SSIM-based checks.

## Project Structure
- `engine.py` - NLP interpreter and test runner
- `actions.py` - Playwright actions and helpers
- `locator_manager.py` - Locator storage and retrieval
- `nlp_keywords.py` - Keyword mapping
- `sync_snippets.py` - VS Code snippet generator
- `image_utils.py` - Image comparison utilities
- `requirements.txt` - Python dependencies
- `steps.flow` - Example test flow

## Adding New Steps
- Add new keywords to `nlp_keywords.py` and implement corresponding functions in `actions.py`.
- Update `sync_snippets.py` to expose new actions as snippets.

## Contributing
- Please ensure all new code is documented and tested.
- Run `pytest` for backend tests (add tests in a `tests/` folder).

## Troubleshooting
- If locators are not found, check `locators_manual.json` and run `python clean_locators.py`.
- For Playwright errors, ensure browsers are installed with `playwright install`.

## License
MIT
