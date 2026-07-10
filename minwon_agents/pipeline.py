from __future__ import annotations

"""Explicit Pipeline + Fan-out/Fan-in + Producer-Reviewer orchestration."""

from concurrent.futures import ThreadPoolExecutor

from .agents import AgentContext, AgentSuite, EventSink
from .contracts import GateDecision, RunStatus
from .events import Stage, pipeline_done, stage_done, stage_error, stage_start


class AgentPipeline:
    """Run the fixed workflow and always return a serializable terminal state."""

    def __init__(self, agents: AgentSuite) -> None:
        self.agents = agents

    def run(self, context: AgentContext, emit: EventSink) -> AgentContext:
        current_stage: Stage | None = None
        try:
            current_stage = "intake"
            emit(stage_start(current_stage, "입력 검증 및 개인정보 마스킹 중", run_id=context.run_id))
            context.intake = self.agents.intake.run(context)
            emit(
                stage_done(
                    current_stage,
                    "입력 가드 통과",
                    {
                        "request_id": context.intake.request_id,
                        "title_chars": len(context.intake.original_title),
                        "body_chars": len(context.intake.original_body),
                        "pii_masked": context.intake.pii_masked,
                    },
                    run_id=context.run_id,
                )
            )

            current_stage = "analyze"
            emit(stage_start(current_stage, "민원 유형·쟁점·민감도 분석 중", run_id=context.run_id))
            context.analysis = self.agents.analyze.run(context)
            emit(
                stage_done(
                    current_stage,
                    "민원 분석 완료",
                    context.analysis.to_dict(),
                    run_id=context.run_id,
                )
            )

            current_stage = "retrieve"
            emit(stage_start(current_stage, "원문 직접 매칭으로 공식 근거 후보 검색 중", run_id=context.run_id))
            context.evidence = self.agents.retrieve.run(context)
            emit(
                stage_done(
                    current_stage,
                    "근거 후보 검색 완료",
                    context.evidence.to_dict(),
                    run_id=context.run_id,
                )
            )

            current_stage = "draft"
            emit(stage_start(current_stage, "허용된 근거만 사용해 답변 초안 작성 중", run_id=context.run_id))
            context.draft = self.agents.draft.run(context, revision=0)
            emit(
                stage_done(
                    current_stage,
                    "초안 작성 완료",
                    context.draft.to_dict(),
                    run_id=context.run_id,
                )
            )

            current_stage = None
            self._run_reviews(context, emit)
            current_stage = "gate"
            self._run_gate(context, emit)

            if context.decision is not None and context.decision.allow_revision:
                feedback = context.decision.reasons
                context.revision_count = 1
                current_stage = "draft"
                emit(
                    stage_start(
                        current_stage,
                        "검수 피드백을 반영해 초안을 1회 재작성 중",
                        run_id=context.run_id,
                    )
                )
                context.draft = self.agents.draft.run(
                    context,
                    revision=1,
                    feedback=feedback,
                )
                emit(
                    stage_done(
                        current_stage,
                        "재작성 완료",
                        context.draft.to_dict(),
                        run_id=context.run_id,
                    )
                )
                current_stage = None
                self._run_reviews(context, emit)
                current_stage = "gate"
                self._run_gate(context, emit)

            decision = context.decision
            if decision is None or decision.status is RunStatus.RUNNING:
                raise RuntimeError("pipeline ended without a terminal gate decision")
            context.status = decision.status
            context.final = self.agents.gate.final_for(context, decision)
        except Exception as exc:
            message = _safe_error(exc)
            context.errors.append(message)
            context.status = RunStatus.FAILED
            context.final = None
            context.decision = GateDecision(
                status=RunStatus.FAILED,
                passed=False,
                reasons=(f"PIPELINE_ERROR:{type(exc).__name__}",),
                allow_revision=False,
            )
            if current_stage is not None:
                emit(
                    stage_error(
                        current_stage,
                        f"단계 실행 실패: {message}",
                        {"error_type": type(exc).__name__},
                        run_id=context.run_id,
                    )
                )

        decision_data = context.decision.to_dict() if context.decision else None
        emit(
            pipeline_done(
                context.run_id,
                context.status.value,
                {
                    "decision": decision_data,
                    "final_available": context.final is not None,
                    "revision_count": context.revision_count,
                },
            )
        )
        return context

    def _run_reviews(self, context: AgentContext, emit: EventSink) -> None:
        """Run independent reviewers concurrently, then join both artifacts."""

        emit(stage_start("grounding", "인용 ID와 검색 근거 일치 여부 검증 중", run_id=context.run_id))
        emit(stage_start("quality", "말투·누락·단정·개인정보 독립 검수 중", run_id=context.run_id))
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="minwon-review") as executor:
            grounding_future = executor.submit(self.agents.grounding.run, context)
            quality_future = executor.submit(self.agents.quality.run, context)

            try:
                context.grounding_review = grounding_future.result()
                emit(
                    stage_done(
                        "grounding",
                        "근거 정합성 검증 완료",
                        context.grounding_review.to_dict(),
                        run_id=context.run_id,
                    )
                )
            except Exception as exc:
                message = _safe_error(exc)
                failures.append(f"grounding={message}")
                emit(stage_error("grounding", f"근거 검증 실패: {message}", run_id=context.run_id))

            try:
                context.quality_review = quality_future.result()
                emit(
                    stage_done(
                        "quality",
                        "품질 검수 완료",
                        context.quality_review.to_dict(),
                        run_id=context.run_id,
                    )
                )
            except Exception as exc:
                message = _safe_error(exc)
                failures.append(f"quality={message}")
                emit(stage_error("quality", f"품질 검수 실패: {message}", run_id=context.run_id))

        if failures:
            raise RuntimeError("; ".join(failures))

    def _run_gate(self, context: AgentContext, emit: EventSink) -> None:
        emit(stage_start("gate", "결정론적 정책 게이트 판정 중", run_id=context.run_id))
        context.decision = self.agents.gate.run(context)
        context.status = context.decision.status
        emit(
            stage_done(
                "gate",
                _gate_message(context.decision),
                context.decision.to_dict(),
                run_id=context.run_id,
            )
        )


def _gate_message(decision: GateDecision) -> str:
    if decision.status is RunStatus.COMPLETED:
        return "자동 검증 통과: 최종 답변 승격"
    if decision.status is RunStatus.HUMAN_REVIEW_REQUIRED:
        return "자동 공개 차단: 담당자 검토 필요"
    if decision.allow_revision:
        return "검수 미통과: 1회 재작성 허용"
    return "실행 실패로 자동 공개 차단"


def _safe_error(exc: Exception) -> str:
    # Avoid dumping large provider responses or secrets into UI/events.
    message = " ".join(str(exc).split()) or type(exc).__name__
    return message[:500]
