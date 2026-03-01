"""
reporting/testplan_selector.py
Moved from root testplan_selector.py.
"""
import os
try:
    import questionary
except ImportError:  # pragma: no cover - optional dependency fallback
    questionary = None


def get_folder_autosuggestion(base_path):
    folders = [
        f for f in os.listdir(base_path)
        if os.path.isdir(os.path.join(base_path, f)) and not f.startswith('.')
    ]
    if questionary:
        testplan = questionary.autocomplete(
            "Type the Testplan (folder) name:",
            choices=folders,
            validate=lambda val: val in folders or "Select a valid folder from suggestions.",
        ).ask()
    else:
        testplan = input("Type the Testplan (folder) name: ").strip()
    return testplan


def select_files_in_folder(folder_path):
    files = [
        f for f in os.listdir(folder_path)
        if os.path.isfile(os.path.join(folder_path, f)) and not f.startswith('.')
    ]
    if questionary:
        selected_files = questionary.checkbox(
            "Select test scripts to include in the report:",
            choices=files,
        ).ask()
    else:
        raw = input("Enter comma-separated test files: ").strip()
        selected_files = [f.strip() for f in raw.split(",") if f.strip()]
    return selected_files


def main():
    base_path = os.getcwd()
    testplan_file = os.path.join(base_path, ".testplan")
    if os.path.exists(testplan_file):
        with open(testplan_file, "r") as f:
            testplan = f.read().strip()
    else:
        testplan = get_folder_autosuggestion(base_path)
        with open(testplan_file, "w") as f:
            f.write(testplan)

    print(f"Selected Testplan: {testplan}")
    folder_path = os.path.join(base_path, testplan)
    selected_files = select_files_in_folder(folder_path)
    print(f"Selected files: {selected_files}")

    with open(os.path.join(base_path, ".selected_files"), "w") as f:
        for file in selected_files:
            f.write(file + "\n")

    print("Selections saved.")


if __name__ == "__main__":
    main()
