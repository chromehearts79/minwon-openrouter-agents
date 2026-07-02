from __future__ import annotations

from collections.abc import Iterable

from .agents import Agent, AgentContext, EventSink
from .events import AgentEvent, stage_error


class AgentPipeline:
    def __init__(self, agents: Iterable[Agent]) -> None:
        self.agents = list(agents)

    def run(self, context: AgentContext, emit: EventSink) -> AgentContext:
        for agent in self.agents:
            try:
                agent.run(context, emit)
            except Exception as exc:
                stage = _stage_for_agent(agent.name)
                emit(stage_error(stage, f"{agent.name} failed: {exc}"))
                raise
        emit(AgentEvent(type="done", message="전체 멀티에이전트 실행 완료"))
        return context


def _stage_for_agent(name: str):
    lower = name.lower()
    if "intake" in lower:
        return "intake"
    if "classify" in lower:
        return "classify"
    if "retrieve" in lower:
        return "retrieve"
    if "draft" in lower:
        return "draft"
    if "review" in lower:
        return "review"
    return None

