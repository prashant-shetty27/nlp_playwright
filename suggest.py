import json
import os

# Paths (Update these based on your actual file locations)
LOCATORS_FILE = "locators_manual.json"
SNIPPETS_FILE = os.path.expanduser("~/.library/Application Support/Code/User/snippets/automation.code-snippets")
# Note: On Windows, use: os.path.expandvars(r"%APPDATA%\Code\User\snippets\automation.code-snippets")

def sync_locators_to_snippets():
    # 1. Get all unique locator names from JSON
    with open(LOCATORS_FILE, "r") as f:
        locs = json.load(f)
    
    all_names = []
    for page in locs.values():
        all_names.extend(page.keys())
    
    # Sort and remove duplicates
    all_names = sorted(list(set(all_names)))
    choice_list = ",".join(all_names)

    # 2. Load existing snippets
    with open(SNIPPETS_FILE, "r") as f:
        snippets = json.load(f)

    # 3. Update the 'Click on element (Saved)' snippet
    snippets["Click on element (Saved)"]["body"] = [
        f"click on element ${{1|{choice_list}|}}"
    ]

    # 4. Save back to VS Code
    with open(SNIPPETS_FILE, "w") as f:
        json.dump(snippets, f, indent=2)
    
    print(f"✅ Synced {len(all_names)} locators to VS Code suggestions!")

if __name__ == "__main__":
    sync_locators_to_snippets()