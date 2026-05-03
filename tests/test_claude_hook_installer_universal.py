"""M4 installer tests: universal PreToolUse matcher with safe migration.

Synthetic settings.json fixtures only — no real user data.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rulence.installers import (
    BOUNDED_TOOL_MATCHERS,
    LEGACY_PRETOOLUSE_COMMAND,
    RULENCE_HOOK_COMMAND,
    UNIVERSAL_PRETOOLUSE_MATCHER,
    install_claude_code,
)


def _read(home: Path) -> dict:
    return json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))


def _rulence_pretooluse(settings: dict) -> list[dict]:
    return [
        e
        for e in settings["hooks"]["PreToolUse"]
        if RULENCE_HOOK_COMMAND in json.dumps(e)
    ]


class UniversalPretoolUseInstallerTests(unittest.TestCase):
    def test_fresh_install_uses_universal_matcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch("rulence.installers.Path.home", return_value=home):
                result = install_claude_code()
            settings = _read(home)
        self.assertEqual(result["status"], "installed")
        self.assertIn(
            f"PreToolUse:{UNIVERSAL_PRETOOLUSE_MATCHER}", result["events_installed"]
        )
        rulence = _rulence_pretooluse(settings)
        self.assertEqual(len(rulence), 1)
        self.assertEqual(rulence[0]["matcher"], UNIVERSAL_PRETOOLUSE_MATCHER)

    def test_unrelated_user_pretooluse_hook_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings_path = home / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "Bash",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "/usr/local/bin/user-pre",
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("rulence.installers.Path.home", return_value=home):
                install_claude_code()
            settings = _read(home)
        pre_entries = settings["hooks"]["PreToolUse"]
        user_pre = [e for e in pre_entries if "user-pre" in json.dumps(e)]
        self.assertEqual(len(user_pre), 1, "user hook must be preserved verbatim")

    def test_reinstall_without_force_does_not_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch("rulence.installers.Path.home", return_value=home):
                first = install_claude_code()
                second = install_claude_code()
            settings = _read(home)
        self.assertEqual(first["status"], "installed")
        self.assertEqual(second["status"], "already_installed")
        rulence = _rulence_pretooluse(settings)
        self.assertEqual(len(rulence), 1)
        self.assertEqual(rulence[0]["matcher"], UNIVERSAL_PRETOOLUSE_MATCHER)

    def test_legacy_bounded_install_is_preserved_without_force(self) -> None:
        # User on M1/M3 has bounded matchers. M4 install without --force
        # must not silently double the coverage.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings_path = home / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "Bash|Edit|Write",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": RULENCE_HOOK_COMMAND,
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("rulence.installers.Path.home", return_value=home):
                result = install_claude_code()
            settings = _read(home)
        # Bounded entry preserved; no universal entry added.
        rulence = _rulence_pretooluse(settings)
        self.assertEqual(len(rulence), 1)
        self.assertEqual(rulence[0]["matcher"], "Bash|Edit|Write")
        self.assertIn("PreToolUse:legacy_bounded", result["events_skipped"])

    def test_force_migrates_bounded_to_universal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings_path = home / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "Bash|Edit|Write",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": LEGACY_PRETOOLUSE_COMMAND,
                                        }
                                    ],
                                },
                                {
                                    "matcher": "Read|Glob|Grep",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": RULENCE_HOOK_COMMAND,
                                        }
                                    ],
                                },
                                {
                                    "matcher": "PleaseKeepMe",
                                    "hooks": [
                                        {"type": "command", "command": "/bin/keep"}
                                    ],
                                },
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("rulence.installers.Path.home", return_value=home):
                result = install_claude_code(force=True)
            settings = _read(home)
        rulence = _rulence_pretooluse(settings)
        self.assertEqual(len(rulence), 1)
        self.assertEqual(rulence[0]["matcher"], UNIVERSAL_PRETOOLUSE_MATCHER)
        # Unrelated entry preserved.
        kept = [
            e
            for e in settings["hooks"]["PreToolUse"]
            if e.get("matcher") == "PleaseKeepMe"
        ]
        self.assertEqual(len(kept), 1)
        self.assertIn("/bin/keep", json.dumps(kept[0]))
        self.assertIn(
            f"PreToolUse:{UNIVERSAL_PRETOOLUSE_MATCHER}", result["events_replaced"]
        )

    def test_posttooluse_remains_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch("rulence.installers.Path.home", return_value=home):
                install_claude_code()
            settings = _read(home)
        post_matchers = [
            e.get("matcher") for e in settings["hooks"]["PostToolUse"]
        ]
        self.assertNotIn(UNIVERSAL_PRETOOLUSE_MATCHER, post_matchers)
        for required in BOUNDED_TOOL_MATCHERS:
            self.assertIn(required, post_matchers)


if __name__ == "__main__":
    unittest.main()
