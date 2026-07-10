from __future__ import annotations

import json
import os
import socket
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
    finish_reason: str | None = None


class OpenRouterClient:
    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not dry_run and (not self.api_key or self.api_key == "sk-or-..."):
            raise RuntimeError("OPENROUTER_API_KEY is required unless --dry-run is used")

    def chat_json(self, *, model: str, system: str, user: str, max_tokens: int = 900) -> LlmResult:
        if self.dry_run:
            return LlmResult(text="{}", usage=Usage(), finish_reason="dry-run")
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
            return LlmResult(text="", usage=Usage(), finish_reason="dry-run")
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
                raw = res.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            msg = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"OpenRouter HTTP {exc.code}: {msg}") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise RuntimeError(f"OpenRouter network error: {exc}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenRouter returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("OpenRouter returned a non-object response")

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise RuntimeError("OpenRouter response has no completion choice")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("OpenRouter response has no message")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("OpenRouter returned empty content")
        usage_raw = payload.get("usage") or {}
        if not isinstance(usage_raw, dict):
            usage_raw = {}
        usage = Usage(
            input_tokens=_safe_int(usage_raw.get("prompt_tokens")),
            output_tokens=_safe_int(usage_raw.get("completion_tokens")),
            cost_usd=_safe_float(usage_raw.get("cost")),
        )
        finish_reason = choice.get("finish_reason")
        return LlmResult(
            text=text,
            usage=usage,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )


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


def _safe_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0
