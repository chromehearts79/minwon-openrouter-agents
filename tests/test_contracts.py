from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest
from uuid import UUID

from minwon_agents.contracts import (
    AnalysisArtifact,
    Category,
    ContractValidationError,
    Difficulty,
    DraftArtifact,
    EvidenceBundle,
    EvidenceItem,
    GateDecision,
    GroundingReview,
    IntakeArtifact,
    QualityCheck,
    QualityReview,
    RunInput,
    RunResult,
    RunStatus,
    new_run_id,
)


RUN_ID = "12345678-1234-5678-9234-567812345678"


def valid_analysis_dict() -> dict[str, object]:
    return {
        "primary_category": "보수·수당",
        "secondary_categories": ["여비"],
        "department": "인사혁신처 또는 관계 소관 부서",
        "difficulty": "중",
        "sensitive": False,
        "issues": ["시간외근무수당", "관내여비 중복 지급"],
        "law_queries": ["공무원수당 등에 관한 규정", "공무원 여비 규정"],
        "keywords": ["시간외", "여비"],
    }


def evidence_item(identifier: str = "E1") -> EvidenceItem:
    return EvidenceItem(
        id=identifier,
        title="공무원수당 등에 관한 규정",
        source="local-catalog",
        excerpt="초과근무수당의 지급 기준을 규정한다.",
        source_url="https://www.law.go.kr/example",
        checked_at="2026-07-10",
        matched_terms=("시간외", "수당"),
        score=8,
    )


class EnumAndRunIdTests(unittest.TestCase):
    def test_required_enum_values(self) -> None:
        self.assertEqual(Category.PAY.value, "보수·수당")
        self.assertEqual(Difficulty.HIGH.value, "상")
        self.assertEqual(RunStatus.HUMAN_REVIEW_REQUIRED.value, "human_review_required")

    def test_new_run_id_is_uuid_and_unique(self) -> None:
        first = new_run_id()
        second = new_run_id()
        self.assertEqual(str(UUID(first)), first)
        self.assertNotEqual(first, second)


class StrictParsingTests(unittest.TestCase):
    def test_analysis_parses_without_coercion_and_serializes(self) -> None:
        artifact = AnalysisArtifact.from_dict(valid_analysis_dict())
        self.assertIs(artifact.primary_category, Category.PAY)
        self.assertEqual(artifact.secondary_categories, (Category.TRAVEL,))
        self.assertIs(artifact.sensitive, False)
        self.assertEqual(artifact.to_dict(), valid_analysis_dict())

    def test_string_false_is_rejected(self) -> None:
        payload = valid_analysis_dict()
        payload["sensitive"] = "false"
        with self.assertRaisesRegex(ContractValidationError, "JSON boolean"):
            AnalysisArtifact.from_dict(payload)

    def test_unknown_category_is_rejected(self) -> None:
        payload = valid_analysis_dict()
        payload["primary_category"] = "임의 분류"
        with self.assertRaisesRegex(ContractValidationError, "must be one of"):
            AnalysisArtifact.from_dict(payload)

    def test_missing_and_unexpected_fields_are_rejected(self) -> None:
        missing = valid_analysis_dict()
        del missing["issues"]
        with self.assertRaisesRegex(ContractValidationError, "missing fields"):
            AnalysisArtifact.from_dict(missing)

        extra = valid_analysis_dict()
        extra["confidence"] = 0.9
        with self.assertRaisesRegex(ContractValidationError, "unexpected fields"):
            AnalysisArtifact.from_dict(extra)

    def test_json_arrays_must_really_be_lists(self) -> None:
        payload = valid_analysis_dict()
        payload["issues"] = ("쟁점",)
        with self.assertRaisesRegex(ContractValidationError, "JSON array"):
            AnalysisArtifact.from_dict(payload)

    def test_artifacts_are_frozen(self) -> None:
        artifact = AnalysisArtifact.from_dict(valid_analysis_dict())
        with self.assertRaises(FrozenInstanceError):
            artifact.sensitive = True  # type: ignore[misc]


class EvidenceAndReviewTests(unittest.TestCase):
    def test_evidence_rejects_invalid_metadata(self) -> None:
        payload = evidence_item().to_dict()
        payload["source_url"] = "local-file"
        with self.assertRaisesRegex(ContractValidationError, "HTTP"):
            EvidenceItem.from_dict(payload)

        payload = evidence_item().to_dict()
        payload["checked_at"] = "2026-02-30"
        with self.assertRaisesRegex(ContractValidationError, "ISO date"):
            EvidenceItem.from_dict(payload)

    def test_bundle_rejects_duplicate_ids_and_empty_success(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "duplicates"):
            EvidenceBundle(
                items=(evidence_item(), evidence_item()),
                insufficient=False,
                query_terms=("수당",),
            )
        with self.assertRaisesRegex(ContractValidationError, "must be insufficient"):
            EvidenceBundle(items=(), insufficient=False, query_terms=())

    def test_quality_review_requires_actual_boolean_and_checks(self) -> None:
        payload = {
            "passed": "false",
            "score": 0,
            "checks": [
                {"criterion": "누락", "passed": False, "comment": "필수 답변 누락"}
            ],
            "reasons": ["답변 누락"],
            "suggested_final": None,
        }
        with self.assertRaisesRegex(ContractValidationError, "JSON boolean"):
            QualityReview.from_dict(payload)

        payload["passed"] = False
        payload["checks"] = []
        with self.assertRaisesRegex(ContractValidationError, "at least 1"):
            QualityReview.from_dict(payload)

    def test_failed_reviews_and_gate_require_reasons(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "requires reasons"):
            GroundingReview(passed=False, reasons=())
        with self.assertRaisesRegex(ContractValidationError, "requires reasons"):
            GateDecision(
                status=RunStatus.FAILED,
                passed=False,
                reasons=(),
            )


class RunResultInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.intake = IntakeArtifact(
            run_id=RUN_ID,
            request_id="REQ-1",
            original_title="수당 문의",
            original_body="본문",
            masked_title="수당 문의",
            masked_body="본문",
            pii_masked=False,
        )
        self.analysis = AnalysisArtifact.from_dict(valid_analysis_dict())
        self.evidence = EvidenceBundle(
            items=(evidence_item(),), insufficient=False, query_terms=("수당",)
        )
        self.draft = DraftArtifact(text="답변 초안 [E1]", citations=("E1",))
        self.grounding = GroundingReview(passed=True, reasons=())
        self.quality = QualityReview(
            passed=True,
            score=90,
            checks=(QualityCheck("근거", True, "일치"),),
            reasons=(),
            suggested_final="답변 초안 [E1]",
        )
        self.decision = GateDecision(
            status=RunStatus.COMPLETED,
            passed=True,
            reasons=(),
        )

    def test_completed_result_round_trip(self) -> None:
        result = RunResult(
            run_id=RUN_ID,
            status=RunStatus.COMPLETED,
            intake=self.intake,
            analysis=self.analysis,
            evidence=self.evidence,
            draft=self.draft,
            grounding_review=self.grounding,
            quality_review=self.quality,
            decision=self.decision,
            final="답변 초안 [E1]",
        )
        self.assertEqual(RunResult.from_dict(result.to_dict()), result)

    def test_non_completed_result_cannot_expose_final(self) -> None:
        decision = GateDecision(
            status=RunStatus.HUMAN_REVIEW_REQUIRED,
            passed=False,
            reasons=("SENSITIVE_CASE",),
        )
        with self.assertRaisesRegex(ContractValidationError, "final must be null"):
            RunResult(
                run_id=RUN_ID,
                status=RunStatus.HUMAN_REVIEW_REQUIRED,
                intake=self.intake,
                analysis=self.analysis,
                evidence=self.evidence,
                draft=self.draft,
                grounding_review=self.grounding,
                quality_review=self.quality,
                decision=decision,
                final="노출되면 안 됨",
            )

    def test_run_input_rejects_noncanonical_run_id(self) -> None:
        payload = {
            "run_id": RUN_ID.upper(),
            "request_id": "REQ-1",
            "title": "제목",
            "body": "본문",
        }
        # RUN_ID contains no a-f characters, so use an explicitly upper-case UUID.
        payload["run_id"] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
        with self.assertRaisesRegex(ContractValidationError, "canonical lowercase"):
            RunInput.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
