"""
reporting/generate_report_script.py
Moved from root generate_report_script.py.
"""
import os


def generate_report_script():
    print("Let's create a test report script! Please answer the following:")
    testplan = input("Enter the testplan/folder name (e.g., Web_B2B_HomePage): ").strip()
    testcase = input("Enter the testcase or script name (e.g., B2B_Home_Sanity.flow): ").strip()
    executer = input("Enter your name (executer): ").strip()
    num_cases = int(input("How many test steps/cases? "))

    results = []
    for i in range(num_cases):
        print(f"\nTest Step {i + 1}:")
        step_name = input("Step description: ").strip()
        status = input("Status (passed/failed/skipped): ").strip().lower()
        reason = None
        if status == "failed":
            reason = input("Failure reason: ").strip()
        results.append((step_name, status, reason))

    script_lines = [
        "from reporting.report_manager import TestReportManager",
        "",
        "def main():",
        f"    report = TestReportManager(testplan_name=\"{testplan}\", executer_name=\"{executer}\")",
    ]

    for step, status, reason in results:
        if status == "failed":
            script_lines.append(
                f"    report.add_result(\"{testcase} - {step}\", \"{status}\", \"{reason}\")"
            )
        else:
            script_lines.append(
                f"    report.add_result(\"{testcase} - {step}\", \"{status}\")"
            )

    script_lines.append("    report.generate_report()")
    script_lines.append("")
    script_lines.append("if __name__ == '__main__':")
    script_lines.append("    main()")

    out_path = os.path.join(os.getcwd(), f"{testcase.replace('.flow', '')}_report.py")
    with open(out_path, "w") as f:
        f.write("\n".join(script_lines))

    print(f"\nPython report script created: {out_path}")


if __name__ == "__main__":
    generate_report_script()
