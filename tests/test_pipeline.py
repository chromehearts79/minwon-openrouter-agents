from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from minwon_agents.agents import (
    AgentContext,
    AnalyzeAgent,
    IntakeAgent,
    build_agents,
)
from minwon_agents.contracts import RunStatus
from minwon_agents.events import AgentEvent, Usage
from minwon_agents.models import ModelConfig
from minwon_agents.openrouter import LlmResult, OpenRouterClient
from minwon_agents.pipeline import AgentPipeline
from minwon_agents.run import run_minwon
from minwon_agents.xlsx_reader import Minwon, load_minwons


ROOT = Path(__file__).resolve().parents[1]
MODELS = ModelConfig(classify="test/classify", draft="test/draft", review="test/review")


class PipelineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.samples = load_minwons(ROOT / "data" / "minwon_sample.xlsx")

    def run_row(self, row: int) -> tuple[AgentContext, list[AgentEvent]]:
        events: list[AgentEvent] = []
        context = run_minwon(
            self.samples[row - 1],
            dry_run=True,
            emit=events.append,
            models=MODELS,
        )
        return context, events

    def test_normal_multi_issue_case_completes_with_grounded_final(self) -> None:
        context, _ = self.run_row(17)

        self.assertIs(context.status, RunStatus.COMPLETED)
        self.assertIsNotNone(context.final)
        self.assertEqual(context.draft.text, context.final)
        self.assertTrue(context.grounding_review.passed)
        self.assertTrue(context.quality_review.passed)
        self.assertTrue({"E5", "E6"}.issubset({item.id for item in context.evidence.items}))
        categories = {
            context.analysis.primary_category.value,
            *(category.value for category in context.analysis.secondary_categories),
        }
        self.assertTrue({"보수·수당", "여비"}.issubset(categories))

    def test_sensitive_cases_are_routed_to_human_without_final(self) -> None:
        for row in (20, 25):
            with self.subTest(row=row):
                context, _ = self.run_row(row)
                self.assertIs(context.status, RunStatus.HUMAN_REVIEW_REQUIRED)
                self.assertTrue(context.analysis.sensitive)
                self.assertIsNone(context.final)
                self.assertIn("SENSITIVE_CASE", context.decision.reasons)

    def test_final_is_exposed_only_for_completed_runs(self) -> None:
        for row in (3, 17, 20, 25, 27):
            with self.subTest(row=row):
                context, _ = self.run_row(row)
                self.assertEqual(
                    context.status is RunStatus.COMPLETED,
                    context.final is not None,
                )
                result = context.to_result()
                self.assertEqual(
                    result.status is RunStatus.COMPLETED,
                    result.final is not None,
                )

    def test_event_order_shows_fan_out_join_before_gate(self) -> None:
        context, events = self.run_row(17)

        def index(stage: str, status: str) -> int:
            return next(
                position
                for position, event in enumerate(events)
                if event.stage == stage and event.status == status
            )

        start_positions = [index(stage, "running") for stage in (
            "intake",
            "analyze",
            "retrieve",
            "draft",
        )]
        self.assertEqual(start_positions, sorted(start_positions))

        grounding_start = index("grounding", "running")
        quality_start = index("quality", "running")
        grounding_done = index("grounding", "done")
        quality_done = index("quality", "done")
        gate_start = index("gate", "running")
        self.assertLess(grounding_start, grounding_done)
        self.assertLess(grounding_start, quality_done)
        self.assertLess(quality_start, grounding_done)
        self.assertLess(quality_start, quality_done)
        self.assertGreater(gate_start, max(grounding_done, quality_done))

        self.assertEqual("done", events[-1].type)
        self.assertEqual("completed", events[-1].status)
        self.assertTrue(all(event.run_id == context.run_id for event in events))

    def test_pipeline_failures_are_fail_closed(self) -> None:
        class ExplodingQualityAgent:
            name = "ExplodingQualityAgent"

            def run(self, context: AgentContext):
                raise ValueError("synthetic reviewer failure")

        suite = build_agents(OpenRouterClient(dry_run=True))
        pipeline = AgentPipeline(replace(suite, quality=ExplodingQualityAgent()))
        events: list[AgentEvent] = []
        context = AgentContext(minwon=self.samples[16], models=MODELS)

        result_context = pipeline.run(context, events.append)

        self.assertIs(result_context.status, RunStatus.FAILED)
        self.assertIsNone(result_context.final)
        self.assertFalse(result_context.decision.passed)
        self.assertFalse(result_context.decision.allow_revision)
        self.assertTrue(result_context.errors)
        self.assertTrue(
            any(
                event.stage == "quality" and event.status == "error"
                for event in events
            )
        )
        self.assertEqual("failed", events[-1].status)

    def test_only_masked_intake_is_sent_to_analysis_model(self) -> None:
        class RecordingLlm:
            dry_run = False

            def __init__(self) -> None:
                self.user = ""

            def chat_json(self, *, model: str, system: str, user: str, max_tokens: int):
                self.user = user
                return LlmResult(
                    text=json.dumps(
                        {
                            "primary_category": "기타",
                            "secondary_categories": [],
                            "department": "민원 담당 부서",
                            "difficulty": "하",
                            "sensitive": False,
                            "issues": ["문의 내용 확인"],
                            "law_queries": [],
                            "keywords": [],
                        },
                        ensure_ascii=False,
                    ),
                    usage=Usage(),
                )

        raw_email = "hong@example.com"
        raw_phone = "010-1234-5678"
        raw_id = "900101-1234567"
        minwon = Minwon(
            request_id="pii-001",
            title=f"연락처 {raw_email}",
            body=f"전화 {raw_phone}, 식별번호 {raw_id} 관련 문의입니다.",
        )
        llm = RecordingLlm()
        context = AgentContext(minwon=minwon, models=MODELS)
        context.intake = IntakeAgent().run(context)

        artifact = AnalyzeAgent(llm).run(context)

        self.assertEqual("기타", artifact.primary_category.value)
        self.assertTrue(context.intake.pii_masked)
        self.assertIn("[EMAIL]", llm.user)
        self.assertIn("[PHONE]", llm.user)
        self.assertIn("[ID_NUMBER]", llm.user)
        self.assertNotIn(raw_email, llm.user)
        self.assertNotIn(raw_phone, llm.user)
        self.assertNotIn(raw_id, llm.user)
        self.assertEqual(raw_email in context.intake.original_title, True)


if __name__ == "__main__":
    unittest.main()
