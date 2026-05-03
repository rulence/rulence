from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rulence.installers import install_claude_code, install_cursor, install_n8n


class InstallersTests(unittest.TestCase):
    def test_claude_code_writes_hook_to_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("rulence.installers.Path.home", return_value=Path(directory)):
                result = install_claude_code()
                settings = json.loads((Path(directory) / ".claude" / "settings.json").read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "installed")
        self.assertIn("PreToolUse", settings["hooks"])
        self.assertTrue(any("rulence" in json.dumps(hook) for hook in settings["hooks"]["PreToolUse"]))
        # Post-M1 the unified `rulence hook claude-code` command is installed.
        self.assertTrue(
            any(
                "rulence hook claude-code" in json.dumps(hook)
                for hook in settings["hooks"]["PreToolUse"]
            )
        )

    def test_claude_code_backs_up_existing_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / ".claude" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text('{"existing": "data"}', encoding="utf-8")
            with patch("rulence.installers.Path.home", return_value=Path(directory)):
                result = install_claude_code()

            self.assertIsNotNone(result["backup"])
            self.assertTrue(Path(result["backup"]).exists())

    def test_claude_code_skips_if_already_installed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("rulence.installers.Path.home", return_value=Path(directory)):
                install_claude_code()
                second = install_claude_code()

        self.assertEqual(second["status"], "already_installed")

    def test_claude_code_raises_helpful_error_on_malformed_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / ".claude" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text("not json at all", encoding="utf-8")
            with patch("rulence.installers.Path.home", return_value=Path(directory)):
                with self.assertRaises(ValueError) as ctx:
                    install_claude_code()

        message = str(ctx.exception)
        self.assertIn(str(settings), message)
        self.assertIn("not valid JSON", message)
        self.assertIn("re-run", message)

    def test_cursor_writes_rule_to_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = install_cursor(directory)
            rule = Path(directory) / ".cursor" / "rules" / "rulence.md"

            self.assertEqual(result["status"], "installed")
            self.assertTrue(rule.exists())
            self.assertIn("rulence preflight", rule.read_text(encoding="utf-8"))

    def test_n8n_returns_config_to_paste(self) -> None:
        result = install_n8n()

        self.assertEqual(result["status"], "print_only")
        self.assertIn("rulence", result["config"]["mcpServers"])


if __name__ == "__main__":
    unittest.main()
