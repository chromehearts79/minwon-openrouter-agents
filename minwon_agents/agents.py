from __future__ import annotations

"""Agent roles for the evidence-grounded civil-petition harness.

Each role has one narrow responsibility and returns an immutable artifact from
``contracts.py``.  The orchestration order and release decision deliberately
live outside the agents so no model can approve its own answer.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from .analysis import analyze
from .contracts import (
    AnalysisArtifact,
    DraftArtifact,
    EvidenceBundle,
    GateDecision,
    GroundingReview,
    IntakeArtifact,
    QualityCheck,
    QualityReview,
    RunResult,
    RunStatus,
    new_run_id,
)
from .events import AgentEvent, Usage
from .guardrails import InputGuard, contains_pii
from .models import ModelConfig
from .openrouter import OpenRouterClient, parse_json_object
from .policy import PolicyGate, validate_citations
from .retrieval import retrieve_evidence
from .xlsx_reader import Minwon


EventSink = Callable[[AgentEvent], None]
_CITATION_RE = re.compile(r"\[(E[1-9][0-9]*)\]", re.IGNORECASE)


@dataclass
class AgentContext:
    """Mutable run state; stage values themselves are immutable contracts."""

    minwon: Minwon
    models: ModelConfig
    run_id: str = field(default_factory=new_run_id)
    status: RunStatus = RunStatus.RUNNING
    intake: IntakeArtifact | None = None
    analysis: AnalysisArtifact | None = None
    evidence: EvidenceBundle | None = None
    draft: DraftArtifact | None = None
    grounding_review: GroundingReview | None = None
    quality_review: QualityReview | None = None
    decision: GateDecision | None = None
    final: str | None = None
    revision_count: int = 0
    usage: dict[str, Usage] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_result(self) -> RunResult:
        decision = self.decision
        if decision is None:
            decision = GateDecision(
                status=RunStatus.FAILED,
                passed=False,
                reasons=("MISSING_GATE_DECISION",),
                allow_revision=False,
            )
        return RunResult(
            run_id=self.run_id,
            status=decision.status,
            intake=self.intake,
            analysis=self.analysis,
            evidence=self.evidence,
            draft=self.draft,
            grounding_review=self.grounding_review,
            quality_review=self.quality_review,
            decision=decision,
            final=self.final if decision.status is RunStatus.COMPLETED else None,
        )


class IntakeAgent:
    name = "IntakeAgent"

    def __init__(self, guard: InputGuard | None = None) -> None:
        self.guard = guard or InputGuard()

    def run(self, context: AgentContext) -> IntakeArtifact:
        return self.guard.prepare(
            request_id=context.minwon.request_id,
            title=context.minwon.title,
            body=context.minwon.body,
            run_id=context.run_id,
        )


class AnalyzeAgent:
    name = "AnalyzeAgent"

    SYSTEM = """너는 중앙행정기관 민원 분석 담당자다.
개인정보가 마스킹된 민원을 다중 분류하고, 다음 단계가 사용할 구조만 만든다.
반드시 설명이나 코드블록 없이 아래 키를 모두 가진 JSON 객체만 출력한다.
primary_category는 인사|복무|보수·수당|여비|채용·시험|시스템|정책의견|기타 중 하나,
secondary_categories는 위 목록 중 최대 3개(주 분류 제외), difficulty는 상|중|하,
sensitive는 JSON boolean이어야 한다.
{
  "primary_category": "인사",
  "secondary_categories": [],
  "department": "추정 소관",
  "difficulty": "중",
  "sensitive": false,
  "issues": ["핵심 쟁점"],
  "law_queries": ["검색할 법령명"],
  "keywords": ["검색어"]
}"""

    def __init__(self, llm: OpenRouterClient) -> None:
        self.llm = llm

    def run(self, context: AgentContext) -> AnalysisArtifact:
        intake = _require(context.intake, "intake")
        if self.llm.dry_run:
            return analyze(intake.masked_title, intake.masked_body)
        result = self.llm.chat_json(
            model=context.models.classify,
            system=self.SYSTEM,
            user=f"[제목]\n{intake.masked_title}\n\n[본문]\n{intake.masked_body}",
            max_tokens=1_000,
        )
        _reject_truncated(result.finish_reason, "analysis")
        context.usage["analyze"] = result.usage
        data = parse_json_object(result.text)
        if data is None:
            raise ValueError("analysis model did not return a JSON object")
        return AnalysisArtifact.from_dict(data)


class EvidenceAgent:
    name = "EvidenceAgent"

    def run(self, context: AgentContext) -> EvidenceBundle:
        intake = _require(context.intake, "intake")
        analysis_artifact = _require(context.analysis, "analysis")
        return retrieve_evidence(
            f"{intake.masked_title}\n{intake.masked_body}",
            analysis_artifact,
            limit=3,
        )


class DraftAgent:
    name = "DraftAgent"

    SYSTEM = """너는 중앙행정기관의 민원 답변 '초안' 작성자다.
제공된 근거 후보 밖의 법령, 조문 번호, 사실을 만들지 않는다.
근거를 사용한 문장 끝에는 반드시 [E1] 같은 제공된 근거 ID를 붙인다.
근거가 부족하면 단정하지 않고 사실관계 및 최신 공식 근거 확인 절차를 안내한다.
답변은 정중한 한국어 공직 답변체로 작성하고, 자동 생성 초안임을 명시한다.
JSON이나 코드블록이 아닌 답변 본문만 출력한다."""

    def __init__(self, llm: OpenRouterClient) -> None:
        self.llm = llm

    def run(
        self,
        context: AgentContext,
        *,
        revision: int = 0,
        feedback: tuple[str, ...] = (),
    ) -> DraftArtifact:
        if revision not in (0, 1):
            raise ValueError("revision must be 0 or 1")
        if self.llm.dry_run:
            text = _dry_draft(context, revision=revision, feedback=feedback)
        else:
            result = self.llm.chat_text(
                model=context.models.draft,
                system=self.SYSTEM,
                user=_draft_prompt(context, revision=revision, feedback=feedback),
                max_tokens=1_800,
                temperature=0.25,
            )
            _reject_truncated(result.finish_reason, "draft")
            context.usage[f"draft_{revision}"] = result.usage
            text = result.text.strip()
        if not text:
            raise ValueError("draft model returned empty text")
        citations = tuple(
            dict.fromkeys(match.upper() for match in _CITATION_RE.findall(text))
        )
        return DraftArtifact(text=text, citations=citations, revision=revision)


class GroundingReviewAgent:
    name = "GroundingReviewAgent"

    def run(self, context: AgentContext) -> GroundingReview:
        evidence = _require(context.evidence, "evidence")
        draft = _require(context.draft, "draft")
        return validate_citations(draft.text, (item.id for item in evidence.items))


class QualityReviewAgent:
    name = "QualityReviewAgent"

    SYSTEM = """너는 민원 답변 초안의 독립 검수자다.
말투, 쟁점 대응, 근거 범위, 과도한 단정, 개인정보 노출을 보수적으로 검토한다.
초안을 최종 승인하거나 직접 배포하지 말고 검수 결과만 낸다.
반드시 설명이나 코드블록 없이 다음 키를 모두 가진 JSON 객체만 출력한다.
checks의 passed와 최상위 passed는 JSON boolean이어야 한다.
{
  "passed": true,
  "score": 0,
  "checks": [{"criterion": "말투", "passed": true, "comment": "검토 의견"}],
  "reasons": [],
  "suggested_final": null
}"""

    def __init__(self, llm: OpenRouterClient) -> None:
        self.llm = llm

    def run(self, context: AgentContext) -> QualityReview:
        if self.llm.dry_run:
            return _dry_quality_review(context)
        result = self.llm.chat_json(
            model=context.models.review,
            system=self.SYSTEM,
            user=_quality_prompt(context),
            max_tokens=1_500,
        )
        _reject_truncated(result.finish_reason, "quality")
        context.usage[f"quality_{context.revision_count}"] = result.usage
        data = parse_json_object(result.text)
        if data is None:
            raise ValueError("quality model did not return a JSON object")
        return QualityReview.from_dict(data)


class PolicyGateAgent:
    name = "PolicyGateAgent"

    def __init__(self, gate: PolicyGate | None = None) -> None:
        self.gate = gate or PolicyGate()

    def run(self, context: AgentContext) -> GateDecision:
        return self.gate.decide(
            _require(context.analysis, "analysis"),
            _require(context.evidence, "evidence"),
            _require(context.draft, "draft"),
            _require(context.grounding_review, "grounding_review"),
            _require(context.quality_review, "quality_review"),
            revision_count=context.revision_count,
        )

    def final_for(self, context: AgentContext, decision: GateDecision) -> str | None:
        return self.gate.final_for(decision, _require(context.draft, "draft"))


@dataclass(frozen=True)
class AgentSuite:
    intake: IntakeAgent
    analyze: AnalyzeAgent
    retrieve: EvidenceAgent
    draft: DraftAgent
    grounding: GroundingReviewAgent
    quality: QualityReviewAgent
    gate: PolicyGateAgent


def build_agents(llm: OpenRouterClient) -> AgentSuite:
    return AgentSuite(
        intake=IntakeAgent(),
        analyze=AnalyzeAgent(llm),
        retrieve=EvidenceAgent(),
        draft=DraftAgent(llm),
        grounding=GroundingReviewAgent(),
        quality=QualityReviewAgent(llm),
        gate=PolicyGateAgent(),
    )


def _require(value: object | None, name: str):
    if value is None:
        raise RuntimeError(f"{name} artifact is required")
    return value


def _reject_truncated(finish_reason: str | None, stage: str) -> None:
    if finish_reason == "length":
        raise ValueError(f"{stage} model response was truncated")


def _draft_prompt(
    context: AgentContext,
    *,
    revision: int,
    feedback: tuple[str, ...],
) -> str:
    intake = _require(context.intake, "intake")
    analysis_artifact = _require(context.analysis, "analysis")
    evidence = _require(context.evidence, "evidence")
    evidence_payload = [item.to_dict() for item in evidence.items]
    feedback_text = "\n".join(f"- {reason}" for reason in feedback) or "- 없음"
    return f"""[민원 - 개인정보 마스킹본]
제목: {intake.masked_title}
본문:
{intake.masked_body}

[분석]
{json.dumps(analysis_artifact.to_dict(), ensure_ascii=False, indent=2)}

[사용 가능한 근거 후보]
{json.dumps(evidence_payload, ensure_ascii=False, indent=2)}

[작성 회차]
{revision}

[이전 검수 피드백]
{feedback_text}

근거 후보가 0개이면 법적 결론을 내리지 말고 담당 부서의 사실관계 및 최신 공식 자료 확인을 안내하라."""


def _quality_prompt(context: AgentContext) -> str:
    intake = _require(context.intake, "intake")
    analysis_artifact = _require(context.analysis, "analysis")
    evidence = _require(context.evidence, "evidence")
    draft = _require(context.draft, "draft")
    return f"""[민원 - 개인정보 마스킹본]
{intake.masked_title}
{intake.masked_body}

[분석]
{json.dumps(analysis_artifact.to_dict(), ensure_ascii=False)}

[허용 근거]
{json.dumps(evidence.to_dict(), ensure_ascii=False)}

[초안]
{draft.text}

점수 80점 이상이고 모든 check가 통과한 경우에만 passed=true로 표시하라."""


def _dry_draft(
    context: AgentContext,
    *,
    revision: int,
    feedback: tuple[str, ...],
) -> str:
    intake = _require(context.intake, "intake")
    analysis_artifact = _require(context.analysis, "analysis")
    evidence = _require(context.evidence, "evidence")
    issues = ", ".join(analysis_artifact.issues)

    lines = [
        "안녕하십니까. 귀하께서 문의하신 사항에 대해 다음과 같이 안내드립니다.",
        "",
        f"귀하의 민원은 ‘{intake.masked_title}’에 관한 것으로, 주요 쟁점은 {issues}로 파악됩니다.",
        "",
    ]
    if evidence.items:
        lines.append("현재 확인한 공식 근거 후보는 다음과 같습니다.")
        for item in evidence.items:
            lines.append(
                f"- {item.title}: {item.excerpt} [{item.id}]"
            )
        lines.extend(
            [
                "",
                "위 근거는 답변 작성을 위한 후보 자료입니다. 실제 적용 여부는 민원인의 소속, 적용 시점, 구체적 사실관계와 최신 법령·행정규칙을 담당 부서가 다시 확인해야 합니다.",
            ]
        )
    else:
        lines.extend(
            [
                "현재 로컬 근거 목록에서는 민원에 직접 대응하는 공식 근거 후보를 특정하지 못했습니다.",
                "따라서 현 단계에서 법적 결론을 단정하지 않으며, 소관 부서가 사실관계와 최신 공식 자료를 확인한 뒤 회신해야 합니다.",
            ]
        )
    lines.extend(
        [
            "",
            "이 문서는 업무 지원을 위해 자동 생성된 초안이며, 담당자의 검토와 결재 전에는 대외 답변으로 사용할 수 없습니다.",
            "감사합니다.",
        ]
    )
    if revision == 1 and feedback:
        # The deterministic rewrite keeps the public-facing body clean while
        # ensuring any prior metadata mismatch is rebuilt from current evidence.
        lines.insert(-2, "검수 의견을 반영하여 근거 표시와 안내 문구를 다시 확인했습니다.")
    return "\n".join(lines)


def _dry_quality_review(context: AgentContext) -> QualityReview:
    analysis_artifact = _require(context.analysis, "analysis")
    evidence = _require(context.evidence, "evidence")
    draft = _require(context.draft, "draft")
    text = draft.text
    known_ids = {item.id for item in evidence.items}
    embedded_ids = {match.upper() for match in _CITATION_RE.findall(text)}

    checks_data = [
        (
            "말투",
            "안녕하십니까" in text and "감사합니다" in text,
            "정중한 공직 답변체와 인사말을 확인했습니다.",
        ),
        (
            "쟁점 대응",
            any(issue in text for issue in analysis_artifact.issues),
            "분석 단계의 핵심 쟁점이 초안에 반영되었습니다.",
        ),
        (
            "근거 범위",
            bool(known_ids) and bool(embedded_ids) and embedded_ids <= known_ids,
            "초안의 근거 ID가 검색 결과 범위 안에 있습니다.",
        ),
        (
            "과도한 단정",
            not any(
                phrase in text
                for phrase in ("반드시 지급됩니다", "위법입니다", "확정됩니다", "무조건 가능합니다")
            ),
            "확인되지 않은 법적 결론을 단정하지 않았습니다.",
        ),
        (
            "개인정보",
            not contains_pii(text),
            "지원하는 주민번호·이메일·전화번호 패턴의 노출이 없습니다.",
        ),
    ]
    checks = tuple(
        QualityCheck(
            criterion=criterion,
            passed=passed,
            comment=comment if passed else f"재검토 필요: {comment}",
        )
        for criterion, passed, comment in checks_data
    )
    failed = tuple(f"QUALITY_CHECK_FAILED:{check.criterion}" for check in checks if not check.passed)
    score = round(100 * sum(check.passed for check in checks) / len(checks))
    passed = score >= 80 and not failed
    return QualityReview(
        passed=passed,
        score=score,
        checks=checks,
        reasons=failed,
        suggested_final=None,
    )
