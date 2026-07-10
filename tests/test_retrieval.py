from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from minwon_agents.analysis import analyze
from minwon_agents.retrieval import EvidenceCatalog, retrieve_evidence
from minwon_agents.xlsx_reader import Minwon, load_minwons


ROOT = Path(__file__).resolve().parents[1]


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.samples = load_minwons(ROOT / "data" / "minwon_sample.xlsx")
        cls.catalog = EvidenceCatalog.load(ROOT / "data" / "evidence_catalog.json")

    def row(self, data_row: int) -> Minwon:
        return self.samples[data_row - 1]

    def search_row(self, data_row: int):
        item = self.row(data_row)
        analysis = analyze(item.title, item.body)
        return self.catalog.search(f"{item.title}\n{item.body}", analysis)

    def test_catalog_has_unique_ids_and_required_source_metadata(self) -> None:
        ids = [entry.id for entry in self.catalog.entries]
        self.assertEqual(len(ids), len(set(ids)))
        for entry in self.catalog.entries:
            self.assertRegex(entry.id, r"^E[1-9][0-9]*$")
            self.assertIn(
                urlparse(entry.source_url).netloc,
                {"www.law.go.kr", "gongmuwon.gosi.kr"},
            )
            date.fromisoformat(entry.checked_at)
            self.assertTrue(entry.excerpt)
            self.assertIn("확인", entry.excerpt)

    def test_row_4_excludes_unrelated_exam_regulation(self) -> None:
        results = self.search_row(4)
        titles = {item.title for item in results}
        self.assertIn("공무원임용령", titles)
        self.assertIn("공무원 임용규칙", titles)
        self.assertNotIn("공무원임용시험령", titles)

    def test_row_17_returns_pay_and_travel_evidence(self) -> None:
        results = self.search_row(17)
        titles = {item.title for item in results}
        self.assertIn("공무원수당 등에 관한 규정", titles)
        self.assertIn("공무원 여비 규정", titles)
        self.assertLessEqual(len(results), 3)

    def test_generic_words_do_not_select_evidence(self) -> None:
        misleading_analysis = {
            "primary_category": "채용·시험",
            "secondary_categories": [],
            "law_queries": ["공무원임용시험령"],
            "keywords": ["공무원", "규정", "문의"],
            "issues": ["기준 확인"],
        }
        results = self.catalog.search(
            "공무원 관련 규정의 처리 기준을 문의합니다.", misleading_analysis
        )
        self.assertEqual([], results)

    def test_original_text_overrides_wrong_classifier_metadata(self) -> None:
        item = self.row(3)
        wrong_analysis = {
            "primary_category": "채용·시험",
            "secondary_categories": [],
            "law_queries": ["공무원임용시험령"],
            "keywords": ["시험"],
            "issues": ["시험"],
        }
        results = self.catalog.search(f"{item.title}\n{item.body}", wrong_analysis)
        self.assertEqual(["E8"], [result.id for result in results])

    def test_evidence_item_contains_traceable_fields(self) -> None:
        result = self.search_row(3)[0]
        self.assertEqual("E8", result.id)
        self.assertEqual("local-curated-catalog", result.source)
        self.assertTrue(result.matched_terms)
        self.assertGreater(result.score, 0)
        self.assertTrue(result.source_url.startswith("https://"))
        date.fromisoformat(result.checked_at)

    def test_bundle_marks_no_policy_evidence_as_insufficient(self) -> None:
        item = self.row(20)
        analysis = analyze(item.title, item.body)
        bundle = retrieve_evidence(f"{item.title}\n{item.body}", analysis)
        self.assertTrue(bundle.insufficient)
        self.assertEqual((), bundle.items)
        self.assertTrue(bundle.query_terms)

    def test_bundle_is_limited_to_three_items(self) -> None:
        item = self.row(12)
        analysis = analyze(item.title, item.body)
        bundle = retrieve_evidence(f"{item.title}\n{item.body}", analysis)
        self.assertLessEqual(len(bundle.items), 3)
        self.assertFalse(bundle.insufficient)

    def test_invalid_limit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.catalog.search("시간외근무수당", None, limit=4)


if __name__ == "__main__":
    unittest.main()
