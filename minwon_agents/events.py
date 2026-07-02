from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


Stage = Literal["intake", "classify", "retrieve", "draft", "review"]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class AgentEvent:
    type: str
    stage: Stage | None = None
    status: str | None = None
    message: str | None = None
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def stage_start(stage: Stage, message: str) -> AgentEvent:
    return AgentEvent(type="stage", stage=stage, status="start", message=message)


def stage_done(stage: Stage, message: str, data: dict[str, Any] | None = None) -> AgentEvent:
    return AgentEvent(type="stage", stage=stage, status="done", message=message, data=data)


def stage_error(stage: Stage, message: str) -> AgentEvent:
    return AgentEvent(type="stage", stage=stage, status="error", message=message)


def token(stage: Stage, delta: str) -> AgentEvent:
    return AgentEvent(type="token", stage=stage, data={"delta": delta})

