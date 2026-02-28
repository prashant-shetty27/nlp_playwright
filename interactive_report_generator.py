import os
import questionary
from report_manager import TestReportManager

def select_testplan_and_files(base_path):
    # List only folders
    folders = [f for f in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, f)) and not f.startswith('.')]
    testplan = questionary.select(
        "Select the test plan (folder):",
        choices=folders
    ).ask()

    # List files in selected folder
    folder_path = os.path.join(base_path, testplan)
    files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f)) and not f.startswith('.')]
    selected_files = questionary.checkbox(
        "Select test scripts to include in the report:",
        choices=files
    ).ask()

    return testplan, selected_files

def main():
    base_path = os.getcwd()
    testplan, selected_files = select_testplan_and_files(base_path)
    executer = questionary.text("Enter your name (executer):").ask()

    report = TestReportManager(testplan_name=testplan, executer_name=executer)
    for file in selected_files:
        # Placeholder: In real use, parse the .flow file and add real results
        report.add_result(file, "passed")
    report.generate_report()

if __name__ == "__main__":
    main()
