from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from minwon_agents.contracts import RunResult
from minwon_agents.run import run_minwon, save_result
from minwon_agents.xlsx_reader import load_minwons


ROOT = Path(__file__).resolve().parents[1]


class ResultPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.samples = load_minwons(ROOT / "data" / "minwon_sample.xlsx")

    def test_result_is_saved_atomically_under_run_id(self) -> None:
        context = run_minwon(self.samples[16], dry_run=True)
        with TemporaryDirectory() as directory:
            path = save_result(
                directory,
                context,
                [],
                row=17,
                dry_run=True,
            )
            self.assertEqual(context.run_id, path.parent.name)
            self.assertEqual("result.json", path.name)
            self.assertFalse((path.parent / ".result.json.tmp").exists())
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual("2.0", payload["schema_version"])
        self.assertEqual("dry-run", payload["mode"])
        result = RunResult.from_dict(payload["result"])
        self.assertEqual(context.run_id, result.run_id)
        self.assertIsNotNone(result.final)

    def test_human_review_result_persists_draft_but_not_final(self) -> None:
        context = run_minwon(self.samples[24], dry_run=True)
        with TemporaryDirectory() as directory:
            path = save_result(
                directory,
                context,
                [],
                row=25,
                dry_run=True,
            )
            result = json.loads(path.read_text(encoding="utf-8"))["result"]

        self.assertEqual("human_review_required", result["status"])
        self.assertTrue(result["draft"]["text"])
        self.assertIsNone(result["final"])


if __name__ == "__main__":
    unittest.main()
