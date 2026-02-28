"""
reporting/report_manager.py
Interactive test report generator.
Moved from interactive_report_generator.py.
"""
import os

import questionary
from report_manager import TestReportManager  # existing root-level module


def select_testplan_and_files(base_path: str) -> tuple[str, list]:
    folders = [
        f for f in os.listdir(base_path)
        if os.path.isdir(os.path.join(base_path, f)) and not f.startswith(".")
    ]
    testplan = questionary.select("Select the test plan (folder):", choices=folders).ask()

    folder_path = os.path.join(base_path, testplan)
    files = [
        f for f in os.listdir(folder_path)
        if os.path.isfile(os.path.join(folder_path, f)) and not f.startswith(".")
    ]
    selected_files = questionary.checkbox(
        "Select test scripts to include in the report:", choices=files
    ).ask()
    return testplan, selected_files


def main() -> None:
    base_path = os.getcwd()
    testplan, selected_files = select_testplan_and_files(base_path)
    executer = questionary.text("Enter your name (executer):").ask()

    report = TestReportManager(testplan_name=testplan, executer_name=executer)
    for file in selected_files:
        report.add_result(file, "passed")
    report.generate_report()


if __name__ == "__main__":
    main()
