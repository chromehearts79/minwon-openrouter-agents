from __future__ import annotations

import unittest

from minwon_agents.contracts import (
    AnalysisArtifact,
    Category,
    Difficulty,
    DraftArtifact,
    EvidenceBundle,
    EvidenceItem,
    GroundingReview,
    QualityCheck,
    QualityReview,
)
from minwon_agents.policy import decide, final_for, validate_citations


def _status_value(decision: object) -> str:
    status = getattr(decision, "status")
    return str(getattr(status, "value", status))


def _analysis(*, sensitive: bool = False, difficulty: str = "중") -> dict[str, object]:
    return {"sensitive": sensitive, "difficulty": difficulty}


def _evidence(*ids: str, insufficient: bool = False) -> dict[str, object]:
    return {
        "items": [{"id": evidence_id} for evidence_id in ids],
        "insufficient": insufficient,
    }


def _draft(text: str = "관련 기준을 확인했습니다 [E1].", revision: int = 0) -> dict[str, object]:
    citations = tuple(match for match in ("E1",) if f"[{match}]" in text)
    if "[E99]" in text:
        citations += ("E99",)
    return {"text": text, "citations": citations, "revision": revision}


def _grounding(passed: bool = True, *reasons: str) -> dict[str, object]:
    return {"passed": passed, "reasons": reasons}


def _quality(
    *,
    passed: bool = True,
    score: int = 90,
    checks: list[dict[str, object]] | None = None,
    reasons: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "passed": passed,
        "score": score,
        "checks": checks
        if checks is not None
        else [{"criterion": "원문 충실도", "passed": True, "comment": "통과"}],
        "reasons": reasons,
    }


class CitationValidationTests(unittest.TestCase):
    def test_accepts_known_citations_case_insensitively(self) -> None:
        review = validate_citations("근거 [e1]과 [E2]를 확인했습니다.", ("E1", "E2"))

        self.assertTrue(review.passed)
        self.assertEqual(review.reasons, ())

    def test_rejects_unknown_citation(self) -> None:
        review = validate_citations("확인했습니다 [E99].", ("E1",))

        self.assertFalse(review.passed)
        self.assertIn("INVALID_CITATION:E99", review.reasons)

    def test_rejects_missing_citation_and_evidence(self) -> None:
        no_citation = validate_citations("근거를 확인했습니다.", ("E1",))
        no_evidence = validate_citations("근거입니다 [E1].", ())

        self.assertIn("MISSING_CITATION", no_citation.reasons)
        self.assertIn("INSUFFICIENT_EVIDENCE", no_evidence.reasons)
        self.assertIn("INVALID_CITATION:E1", no_evidence.reasons)


class PolicyDecisionTests(unittest.TestCase):
    def _decide(self, **overrides: object) -> object:
        values: dict[str, object] = {
            "analysis": _analysis(),
            "evidence": _evidence("E1"),
            "draft": _draft(),
            "grounding": _grounding(),
            "quality": _quality(),
            "revision_count": 0,
        }
        values.update(overrides)
        return decide(**values)  # type: ignore[arg-type]

    def test_all_hard_gates_pass_to_completed(self) -> None:
        decision = self._decide()

        self.assertEqual(_status_value(decision), "completed")
        self.assertTrue(decision.passed)
        self.assertFalse(decision.allow_revision)
        self.assertEqual(final_for(decision, _draft()), "관련 기준을 확인했습니다 [E1].")

    def test_accepts_strict_contract_artifacts(self) -> None:
        analysis = AnalysisArtifact(
            primary_category=Category.PAY,
            secondary_categories=(),
            department="보수 담당 부서",
            difficulty=Difficulty.MEDIUM,
            sensitive=False,
            issues=("수당 지급 기준",),
            law_queries=("공무원수당 등에 관한 규정",),
            keywords=("수당",),
        )
        item = EvidenceItem(
            id="E1",
            title="공무원수당 등에 관한 규정",
            source="교육용 로컬 카탈로그",
            excerpt="수당 지급 기준은 관계 규정과 사실관계를 확인해야 한다.",
            source_url="https://www.law.go.kr",
            checked_at="2026-07-10",
            matched_terms=("수당",),
            score=5,
        )
        evidence = EvidenceBundle(items=(item,), insufficient=False, query_terms=("수당",))
        draft = DraftArtifact(text="관계 규정을 확인했습니다 [E1].", citations=("E1",))
        grounding = GroundingReview(passed=True, reasons=())
        quality = QualityReview(
            passed=True,
            score=90,
            checks=(QualityCheck("원문 충실도", True, "통과"),),
            reasons=(),
        )

        decision = decide(analysis, evidence, draft, grounding, quality)

        self.assertEqual(_status_value(decision), "completed")
        self.assertEqual(final_for(decision, draft), draft.text)

    def test_sensitive_case_requires_human_review(self) -> None:
        decision = self._decide(analysis=_analysis(sensitive=True))

        self.assertEqual(_status_value(decision), "human_review_required")
        self.assertIn("SENSITIVE_CASE", decision.reasons)
        self.assertIsNone(final_for(decision, _draft()))

    def test_high_difficulty_requires_human_review(self) -> None:
        decision = self._decide(analysis=_analysis(difficulty="상"))

        self.assertEqual(_status_value(decision), "human_review_required")
        self.assertIn("HIGH_DIFFICULTY", decision.reasons)

    def test_insufficient_evidence_requires_human_review(self) -> None:
        decision = self._decide(evidence=_evidence(insufficient=True))

        self.assertEqual(_status_value(decision), "human_review_required")
        self.assertIn("INSUFFICIENT_EVIDENCE", decision.reasons)
        self.assertIsNone(final_for(decision, _draft()))

    def test_first_quality_failure_requests_exactly_one_revision(self) -> None:
        decision = self._decide(
            quality=_quality(passed=False, score=72, reasons=("핵심 쟁점 누락",))
        )

        self.assertEqual(_status_value(decision), "running")
        self.assertTrue(decision.allow_revision)
        self.assertIn("REVISION_REQUIRED", decision.reasons)
        self.assertIn("QUALITY_REVIEW_FAILED", decision.reasons)
        self.assertIsNone(final_for(decision, _draft()))

    def test_failed_review_after_revision_routes_to_human(self) -> None:
        decision = self._decide(
            draft=_draft(revision=1),
            quality=_quality(passed=False, score=72),
        )

        self.assertEqual(_status_value(decision), "human_review_required")
        self.assertFalse(decision.allow_revision)
        self.assertIn("MAX_REVISIONS_REACHED", decision.reasons)

    def test_unknown_citation_is_rewritten_then_stopped(self) -> None:
        bad_draft = _draft("존재하지 않는 근거입니다 [E99].")
        first = self._decide(draft=bad_draft)
        second = self._decide(draft={**bad_draft, "revision": 1}, revision_count=1)

        self.assertEqual(_status_value(first), "running")
        self.assertTrue(first.allow_revision)
        self.assertIn("INVALID_CITATION:E99", first.reasons)
        self.assertEqual(_status_value(second), "human_review_required")
        self.assertFalse(second.allow_revision)
        self.assertIn("MAX_REVISIONS_REACHED", second.reasons)

    def test_failed_quality_check_is_not_hidden_by_passed_summary(self) -> None:
        decision = self._decide(
            quality=_quality(
                passed=True,
                score=95,
                checks=[{"criterion": "누락", "passed": False, "comment": "쟁점 누락"}],
            )
        )

        self.assertEqual(_status_value(decision), "running")
        self.assertIn("QUALITY_CHECK_FAILED:누락", decision.reasons)

    def test_malformed_review_fails_closed(self) -> None:
        decision = self._decide(
            grounding={"passed": "false", "reasons": []},
            quality={
                "passed": True,
                "score": "90",
                "checks": [{"criterion": "원문 충실도", "passed": True}],
            },
        )

        self.assertEqual(_status_value(decision), "failed")
        self.assertIn("INVALID_GROUNDING_REVIEW", decision.reasons)
        self.assertIn("INVALID_QUALITY_SCORE", decision.reasons)
        self.assertFalse(decision.allow_revision)

    def test_empty_required_quality_checks_fail_closed(self) -> None:
        decision = self._decide(quality=_quality(checks=[]))

        self.assertEqual(_status_value(decision), "failed")
        self.assertIn("MISSING_QUALITY_CHECKS", decision.reasons)

    def test_revision_limit_cannot_be_bypassed(self) -> None:
        decision = self._decide(revision_count=2)

        self.assertEqual(_status_value(decision), "failed")
        self.assertIn("INVALID_REVISION_COUNT", decision.reasons)

    def test_final_is_never_returned_for_inconsistent_nonpassing_decision(self) -> None:
        decision = {
            "status": "completed",
            "passed": False,
            "reasons": ("FORCED",),
            "allow_revision": False,
        }

        self.assertIsNone(final_for(decision, _draft()))


if __name__ == "__main__":
    unittest.main()
