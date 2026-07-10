from __future__ import annotations

import unittest
from pathlib import Path

from minwon_agents.analysis import analyze, heuristic_analyze
from minwon_agents.contracts import Category, Difficulty
from minwon_agents.xlsx_reader import Minwon, load_minwons


ROOT = Path(__file__).resolve().parents[1]


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.samples = load_minwons(ROOT / "data" / "minwon_sample.xlsx")

    def row(self, data_row: int) -> Minwon:
        return self.samples[data_row - 1]

    def analyze_row(self, data_row: int) -> dict[str, object]:
        item = self.row(data_row)
        return heuristic_analyze(item.title, item.body)

    def test_system_issue_wins_for_row_3(self) -> None:
        result = self.analyze_row(3)
        self.assertEqual("시스템", result["primary_category"])
        self.assertNotIn("채용·시험", result["secondary_categories"])

    def test_pay_issues_win_for_rows_12_and_39(self) -> None:
        self.assertEqual("보수·수당", self.analyze_row(12)["primary_category"])
        self.assertEqual("보수·수당", self.analyze_row(39)["primary_category"])

    def test_row_17_preserves_both_pay_and_travel(self) -> None:
        result = self.analyze_row(17)
        categories = {result["primary_category"], *result["secondary_categories"]}
        self.assertEqual("보수·수당", result["primary_category"])
        self.assertTrue({"보수·수당", "여비"}.issubset(categories))
        self.assertTrue(any("시간외" in issue for issue in result["issues"]))
        self.assertTrue(any("여비" in issue for issue in result["issues"]))

    def test_policy_opinion_is_not_incidental_recruitment(self) -> None:
        result = self.analyze_row(20)
        self.assertEqual("정책의견", result["primary_category"])
        self.assertNotIn("채용·시험", result["secondary_categories"])
        self.assertTrue(result["sensitive"])

    def test_crime_discipline_and_medical_signals_are_sensitive(self) -> None:
        self.assertTrue(self.analyze_row(25)["sensitive"])
        medical = heuristic_analyze(
            "병가 문의",
            "임신 중 조기진통 진단을 받아 병가 사용 가능 여부를 문의합니다.",
        )
        disciplinary = heuristic_analyze(
            "징계 절차 문의",
            "수사 개시 통보를 받은 공무원의 징계 절차를 문의합니다.",
        )
        self.assertTrue(medical["sensitive"])
        self.assertTrue(disciplinary["sensitive"])

    def test_negated_discipline_and_polite_thanks_do_not_trigger_sensitivity(self) -> None:
        result = heuristic_analyze(
            "재임용 문의",
            "비위나 징계는 전혀 없고 재임용 절차가 궁금합니다. 감사합니다.",
        )
        self.assertFalse(result["sensitive"])

    def test_organization_and_compound_words_do_not_trigger_sensitivity(self) -> None:
        self.assertFalse(self.analyze_row(16)["sensitive"])  # 충남경찰청
        self.assertFalse(self.analyze_row(24)["sensitive"])  # 법령명의 '복무징계'
        self.assertFalse(self.analyze_row(36)["sensitive"])  # 근속승진기간 단축

    def test_strict_artifact_is_returned(self) -> None:
        item = self.row(17)
        artifact = analyze(item.title, item.body)
        self.assertIs(artifact.primary_category, Category.PAY)
        self.assertIn(Category.TRAVEL, artifact.secondary_categories)
        self.assertIs(artifact.difficulty, Difficulty.MEDIUM)
        self.assertIs(type(artifact.sensitive), bool)
        self.assertIsInstance(artifact.issues, tuple)

    def test_analysis_is_deterministic(self) -> None:
        item = self.row(4)
        first = heuristic_analyze(item.title, item.body)
        second = heuristic_analyze(item.title, item.body)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
