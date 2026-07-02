from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .events import Usage


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass(frozen=True)
class LlmResult:
    text: str
    usage: Usage


class OpenRouterClient:
    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not dry_run and not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required unless --dry-run is used")

    def chat_json(self, *, model: str, system: str, user: str, max_tokens: int = 900) -> LlmResult:
        if self.dry_run:
            return LlmResult(text="{}", usage=Usage())
        return self._request(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }
        )

    def chat_text(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 1600,
        temperature: float = 0.4,
    ) -> LlmResult:
        if self.dry_run:
            return LlmResult(text="", usage=Usage())
        return self._request(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )

    def _request(self, body: dict[str, Any]) -> LlmResult:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            OPENROUTER_URL,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost/minwon-openrouter-agents",
                "X-OpenRouter-Title": "Minwon OpenRouter Agents",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as res:
                payload = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            msg = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"OpenRouter HTTP {exc.code}: {msg}") from exc

        text = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage_raw = payload.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_raw.get("prompt_tokens") or 0),
            output_tokens=int(usage_raw.get("completion_tokens") or 0),
            cost_usd=float(usage_raw.get("cost") or 0.0),
        )
        return LlmResult(text=text, usage=usage)


def parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None

