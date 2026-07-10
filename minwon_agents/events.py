from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal


Stage = Literal["intake", "analyze", "retrieve", "draft", "grounding", "quality", "gate"]


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
    run_id: str | None = None
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def stage_start(stage: Stage, message: str, *, run_id: str | None = None) -> AgentEvent:
    return AgentEvent(type="stage", stage=stage, status="running", message=message, run_id=run_id)


def stage_done(
    stage: Stage,
    message: str,
    data: dict[str, Any] | None = None,
    *,
    run_id: str | None = None,
) -> AgentEvent:
    return AgentEvent(
        type="stage",
        stage=stage,
        status="done",
        message=message,
        data=data,
        run_id=run_id,
    )


def stage_error(
    stage: Stage,
    message: str,
    data: dict[str, Any] | None = None,
    *,
    run_id: str | None = None,
) -> AgentEvent:
    return AgentEvent(
        type="stage",
        stage=stage,
        status="error",
        message=message,
        data=data,
        run_id=run_id,
    )


def pipeline_done(run_id: str, status: str, data: dict[str, Any] | None = None) -> AgentEvent:
    return AgentEvent(
        type="done",
        status=status,
        message="전체 하네스 실행 완료",
        data=data,
        run_id=run_id,
    )
