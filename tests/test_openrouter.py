from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import patch
import urllib.error

from minwon_agents.openrouter import OpenRouterClient, parse_json_object


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False

    def read(self) -> bytes:
        return self.payload


class OpenRouterClientTests(unittest.TestCase):
    def make_live_client(self) -> OpenRouterClient:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=True):
            return OpenRouterClient(dry_run=False)

    def test_live_mode_requires_non_placeholder_api_key(self) -> None:
        for value in (None, "", "sk-or-..."):
            with self.subTest(value=value):
                environment = {} if value is None else {"OPENROUTER_API_KEY": value}
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaisesRegex(RuntimeError, "OPENROUTER_API_KEY"):
                        OpenRouterClient(dry_run=False)

    def test_dry_run_never_requires_key_or_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = OpenRouterClient(dry_run=True)
        with patch("minwon_agents.openrouter.urllib.request.urlopen") as urlopen:
            json_result = client.chat_json(
                model="test/model", system="system", user="user", max_tokens=10
            )
            text_result = client.chat_text(
                model="test/model", system="system", user="user", max_tokens=10
            )
        urlopen.assert_not_called()
        self.assertEqual("{}", json_result.text)
        self.assertEqual("", text_result.text)
        self.assertEqual("dry-run", json_result.finish_reason)

    def test_successful_response_is_strictly_unwrapped_and_usage_sanitized(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {"content": "정상 응답"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": "12",
                "completion_tokens": 7,
                "cost": "0.0015",
            },
        }
        client = self.make_live_client()
        with patch(
            "minwon_agents.openrouter.urllib.request.urlopen",
            return_value=_FakeResponse(json.dumps(payload).encode("utf-8")),
        ) as urlopen:
            result = client.chat_text(
                model="test/model", system="system", user="user"
            )

        self.assertEqual("정상 응답", result.text)
        self.assertEqual("stop", result.finish_reason)
        self.assertEqual(12, result.usage.input_tokens)
        self.assertEqual(7, result.usage.output_tokens)
        self.assertEqual(0.0015, result.usage.cost_usd)
        request = urlopen.call_args.args[0]
        self.assertEqual("Bearer test-key", request.get_header("Authorization"))

    def test_invalid_provider_response_shapes_fail_closed(self) -> None:
        malformed = (
            (b"not-json", "invalid JSON"),
            (b"[]", "non-object"),
            (b"{}", "no completion choice"),
            (b'{"choices": [1]}', "no completion choice"),
            (b'{"choices": [{}]}', "no message"),
            (b'{"choices": [{"message": {"content": ""}}]}', "empty content"),
        )
        client = self.make_live_client()
        for raw, expected in malformed:
            with self.subTest(expected=expected):
                with patch(
                    "minwon_agents.openrouter.urllib.request.urlopen",
                    return_value=_FakeResponse(raw),
                ):
                    with self.assertRaisesRegex(RuntimeError, expected):
                        client.chat_text(
                            model="test/model", system="system", user="user"
                        )

    def test_http_and_network_errors_are_normalized(self) -> None:
        client = self.make_live_client()
        http_error = urllib.error.HTTPError(
            "https://openrouter.ai",
            429,
            "Too Many Requests",
            None,
            io.BytesIO(b'{"error":"rate limited"}'),
        )
        with patch(
            "minwon_agents.openrouter.urllib.request.urlopen",
            side_effect=http_error,
        ):
            with self.assertRaisesRegex(RuntimeError, "OpenRouter HTTP 429"):
                client.chat_text(model="test/model", system="system", user="user")
        http_error.close()

        with patch(
            "minwon_agents.openrouter.urllib.request.urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            with self.assertRaisesRegex(RuntimeError, "OpenRouter network error"):
                client.chat_text(model="test/model", system="system", user="user")

    def test_parse_json_object_accepts_object_or_wrapped_object_only(self) -> None:
        self.assertEqual({"ok": True}, parse_json_object('{"ok": true}'))
        self.assertEqual(
            {"ok": True},
            parse_json_object('모델 설명\n```json\n{"ok": true}\n```'),
        )
        self.assertIsNone(parse_json_object("[1, 2, 3]"))
        self.assertIsNone(parse_json_object("{not-json}"))


if __name__ == "__main__":
    unittest.main()
