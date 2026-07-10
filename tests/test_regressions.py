from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from minwon_agents.contracts import RunStatus
from minwon_agents.run import run_minwon
from minwon_agents.xlsx_reader import load_minwons


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "eval" / "minwon_core_cases.json"
XLSX = ROOT / "data" / "minwon_sample.xlsx"
EVALUATE = ROOT / "scripts" / "evaluate.py"


class RegressionEvaluationTests(unittest.TestCase):
    def test_core_case_file_uses_bounded_expectations(self) -> None:
        payload = json.loads(CASES.read_text(encoding="utf-8"))
        cases = payload["cases"]

        self.assertEqual(10, len(cases))
        self.assertEqual(
            {3, 4, 12, 17, 18, 20, 25, 27, 31, 39},
            {case["row"] for case in cases},
        )
        for case in cases:
            expected = case["expected"]
            self.assertIn("allowed_statuses", expected)
            self.assertNotIn("exact_evidence_ids", expected)
            if "evidence_any_of" in expected:
                self.assertGreater(len(expected["evidence_any_of"]), 0)

    def test_core_evaluation_passes_with_zero_release_violations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "core-report.json"
            completed = self.run_evaluator("--output", str(output))
            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(10, report["summary"]["case_count"])
        self.assertEqual(0, report["summary"]["expectation_failure_count"])
        self.assertEqual(0, report["summary"]["invariant_violation_count"])
        self.assertEqual(0, report["summary"]["final_exposure_violation_count"])
        for metric in ("category", "secondary", "status", "evidence", "sensitive"):
            value = report["summary"]["metrics"][metric]
            self.assertGreater(value["checked"], 0)
            self.assertEqual(1.0, value["accuracy"])

    def test_all_mode_runs_every_sample_without_failed_or_leaked_final(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "all-report.json"
            completed = self.run_evaluator("--all", "--output", str(output))
            report = json.loads(output.read_text(encoding="utf-8"))

        samples = load_minwons(XLSX)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(len(samples), report["summary"]["case_count"])
        self.assertEqual(0, report["summary"]["invariant_violation_count"])
        self.assertEqual(0, report["summary"]["final_exposure_violation_count"])
        self.assertFalse(
            any(result["actual"]["status"] == RunStatus.FAILED.value for result in report["results"])
        )

    def test_expectation_failure_returns_exit_code_one(self) -> None:
        bad_cases = {
            "schema_version": "1.0",
            "cases": [
                {
                    "id": "intentional-mismatch",
                    "row": 3,
                    "expected": {
                        "allowed_primary_categories": ["여비"],
                        "allowed_statuses": ["completed"]
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            cases_path = Path(directory) / "bad-cases.json"
            cases_path.write_text(
                json.dumps(bad_cases, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_evaluator("--cases", str(cases_path))
            report = json.loads(completed.stdout)

        self.assertEqual(1, completed.returncode)
        self.assertFalse(report["summary"]["passed"])
        self.assertEqual(1, report["summary"]["expectation_failure_count"])

    def test_input_error_returns_exit_code_two(self) -> None:
        completed = self.run_evaluator("--xlsx", str(ROOT / "data" / "missing.xlsx"))
        self.assertEqual(2, completed.returncode)
        error = json.loads(completed.stderr)
        self.assertEqual("evaluation_error", error["type"])

    def run_evaluator(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(EVALUATE),
                "--xlsx",
                str(XLSX),
                "--cases",
                str(CASES),
                *args,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
