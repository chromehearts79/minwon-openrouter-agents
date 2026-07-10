from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from minwon_agents.web import INDEX_HTML, _has_api_key, load_env_file


class WebGuardTests(unittest.TestCase):
    def test_ui_exposes_all_harness_stages(self) -> None:
        for stage in (
            "intake",
            "analyze",
            "retrieve",
            "draft",
            "grounding",
            "quality",
            "gate",
        ):
            self.assertIn(f'["{stage}"', INDEX_HTML)
        self.assertIn("human_review_required", INDEX_HTML)

    def test_placeholder_key_is_not_reported_as_configured(self) -> None:
        for value, expected in (("", False), ("sk-or-...", False), ("real-key", True)):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"OPENROUTER_API_KEY": value}, clear=True):
                    self.assertIs(expected, _has_api_key())

    def test_env_loader_does_not_override_existing_values(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            path.write_text("EXISTING=file\nNEW_VALUE=loaded\n", encoding="utf-8")
            with patch.dict(os.environ, {"EXISTING": "process"}, clear=True):
                load_env_file(path)
                self.assertEqual("process", os.environ["EXISTING"])
                self.assertEqual("loaded", os.environ["NEW_VALUE"])


if __name__ == "__main__":
    unittest.main()
