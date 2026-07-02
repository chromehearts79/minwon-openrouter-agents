from __future__ import annotations

from dataclasses import dataclass, field
import json
from urllib import request
from urllib.error import URLError
from typing import Any

from .events import AgentEvent, Stage


STAGE_AGENT_IDS: dict[Stage, int] = {
    "intake": 1,
    "classify": 2,
    "retrieve": 3,
    "draft": 4,
    "review": 5,
}

STAGE_LABELS: dict[Stage, str] = {
    "intake": "Intake Agent",
    "classify": "Classify Agent",
    "retrieve": "Retrieve Agent",
    "draft": "Draft Agent",
    "review": "Review Agent",
}


@dataclass
class PixelAgentsAdapter:
    """Translate minwon pipeline events into Pixel Agents-style server messages."""

    current_tools: dict[Stage, str] = field(default_factory=dict)
    sequence: int = 0

    def boot_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {
                "type": "providerCapabilities",
                "readingTools": ["Intake", "Retrieve"],
                "subagentToolNames": [],
            }
        ]
        for stage, agent_id in STAGE_AGENT_IDS.items():
            messages.append(
                {
                    "type": "agentCreated",
                    "id": agent_id,
                    "folderName": STAGE_LABELS[stage],
                    "isExternal": True,
                }
            )
            messages.append({"type": "agentStatus", "id": agent_id, "status": "waiting"})
        return messages

    def translate(self, event: AgentEvent) -> list[dict[str, Any]]:
        if event.type == "done":
            return [{"type": "agentToolsClear", "id": agent_id} for agent_id in STAGE_AGENT_IDS.values()]
        if event.type != "stage" or event.stage is None:
            return []

        stage = event.stage
        agent_id = STAGE_AGENT_IDS[stage]
        if event.status == "start":
            self.sequence += 1
            tool_id = f"{stage}-{self.sequence}"
            self.current_tools[stage] = tool_id
            return [
                {"type": "agentStatus", "id": agent_id, "status": "active"},
                {
                    "type": "agentToolStart",
                    "id": agent_id,
                    "toolId": tool_id,
                    "toolName": STAGE_LABELS[stage],
                    "status": event.message or "작업 중",
                },
            ]

        if event.status in {"done", "error"}:
            tool_id = self.current_tools.pop(stage, f"{stage}-{self.sequence}")
            messages: list[dict[str, Any]] = [{"type": "agentToolDone", "id": agent_id, "toolId": tool_id}]
            if event.status == "error":
                messages.append(
                    {
                        "type": "agentToolStart",
                        "id": agent_id,
                        "toolId": f"{stage}-error",
                        "toolName": STAGE_LABELS[stage],
                        "status": event.message or "오류",
                    }
                )
            else:
                messages.append({"type": "agentStatus", "id": agent_id, "status": "waiting"})
            return messages

        return []


class PixelAgentsBridge:
    def __init__(self, base_url: str = "http://127.0.0.1:3100", timeout: float = 0.5) -> None:
        self.endpoint = base_url.rstrip("/") + "/api/external/messages"
        self.timeout = timeout

    def send(self, messages: list[dict[str, Any]]) -> None:
        if not messages:
            return
        body = json.dumps({"messages": messages}, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            request.urlopen(req, timeout=self.timeout).close()
        except (OSError, URLError):
            # Pixel Agents is a visualization layer. The minwon pipeline must keep
            # running even when the office server is not open.
            return
