"""
reporting/report_manager.py
Interactive + programmatic test report generator.
"""
import json
import os
from datetime import datetime

try:
    import questionary
except ImportError:  # pragma: no cover - optional dependency fallback
    questionary = None


class TestReportManager:
    """
    Lightweight test report manager for flow executions.
    Stores step/test results and writes a JSON + text summary report.
    """

    def __init__(self, testplan_name: str, executer_name: str):
        self.testplan_name = testplan_name
        self.executer_name = executer_name
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.results: list[dict] = []

    def add_result(self, test_name: str, status: str, reason: str | None = None) -> None:
        normalized = (status or "").strip().lower()
        if normalized not in {"passed", "failed", "skipped"}:
            normalized = "failed"
        self.results.append(
            {
                "test_name": test_name,
                "status": normalized,
                "reason": reason or "",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )

    def _summary(self) -> dict:
        total = len(self.results)
        passed = sum(1 for r in self.results if r["status"] == "passed")
        failed = sum(1 for r in self.results if r["status"] == "failed")
        skipped = sum(1 for r in self.results if r["status"] == "skipped")
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        }

    def generate_report(self, output_dir: str = "data/logs") -> tuple[str, str]:
        os.makedirs(output_dir, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"report_{self.testplan_name}_{stamp}".replace(" ", "_")
        json_path = os.path.join(output_dir, f"{base_name}.json")
        txt_path = os.path.join(output_dir, f"{base_name}.txt")

        payload = {
            "testplan": self.testplan_name,
            "executer": self.executer_name,
            "started_at": self.started_at,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "summary": self._summary(),
            "results": self.results,
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        summary = payload["summary"]
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"Testplan : {self.testplan_name}\n")
            f.write(f"Executer : {self.executer_name}\n")
            f.write(f"Started  : {self.started_at}\n")
            f.write(f"Generated: {payload['generated_at']}\n\n")
            f.write(
                f"TOTAL={summary['total']} PASSED={summary['passed']} FAILED={summary['failed']} SKIPPED={summary['skipped']}\n\n"
            )
            for r in self.results:
                reason = f" | reason={r['reason']}" if r["reason"] else ""
                f.write(f"- {r['test_name']} => {r['status']}{reason}\n")

        return json_path, txt_path


def select_testplan_and_files(base_path: str) -> tuple[str, list]:
    folders = [
        f for f in os.listdir(base_path)
        if os.path.isdir(os.path.join(base_path, f)) and not f.startswith(".")
    ]
    if questionary:
        testplan = questionary.select("Select the test plan (folder):", choices=folders).ask()
    else:
        testplan = input("Select the test plan (folder): ").strip()

    folder_path = os.path.join(base_path, testplan)
    files = [
        f for f in os.listdir(folder_path)
        if os.path.isfile(os.path.join(folder_path, f)) and not f.startswith(".")
    ]
    if questionary:
        selected_files = questionary.checkbox(
            "Select test scripts to include in the report:", choices=files
        ).ask()
    else:
        raw = input("Select test scripts (comma-separated): ").strip()
        selected_files = [f.strip() for f in raw.split(",") if f.strip()]
    return testplan, selected_files


def main() -> None:
    base_path = os.getcwd()
    testplan, selected_files = select_testplan_and_files(base_path)
    executer = (
        questionary.text("Enter your name (executer):").ask()
        if questionary else input("Enter your name (executer): ").strip()
    )

    report = TestReportManager(testplan_name=testplan, executer_name=executer)
    for file in selected_files:
        report.add_result(file, "passed")
    report.generate_report()


if __name__ == "__main__":
    main()
