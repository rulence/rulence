from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rulence.installers import (
    BOUNDED_TOOL_MATCHERS,
    LEGACY_PRETOOLUSE_COMMAND,
    MATCHED_EVENTS,
    RULENCE_HOOK_COMMAND,
    UNIVERSAL_PRETOOLUSE_MATCHER,
    UNMATCHED_EVENTS,
    install_claude_code,
)


def _read_settings(home: Path) -> dict:
    return json.loads(
        (home / ".claude" / "settings.json").read_text(encoding="utf-8")
    )


def _flat_matchers(entries) -> list[str | None]:
    return [entry.get("matcher") for entry in entries if isinstance(entry, dict)]


class SafeVisibilityInstallerTests(unittest.TestCase):
    def test_pretooluse_uses_universal_matcher_post_m4(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("rulence.installers.Path.home", return_value=Path(directory)):
                install_claude_code()
            settings = _read_settings(Path(directory))

        pre_matchers = _flat_matchers(settings["hooks"]["PreToolUse"])
        # Exactly one Rulence PreToolUse entry, with matcher "*".
        self.assertIn(UNIVERSAL_PRETOOLUSE_MATCHER, pre_matchers)
        rulence_pre_entries = [
            e
            for e in settings["hooks"]["PreToolUse"]
            if RULENCE_HOOK_COMMAND in json.dumps(e)
        ]
        self.assertEqual(len(rulence_pre_entries), 1)
        self.assertEqual(
            rulence_pre_entries[0].get("matcher"),
            UNIVERSAL_PRETOOLUSE_MATCHER,
        )

    def test_posttooluse_keeps_bounded_matchers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("rulence.installers.Path.home", return_value=Path(directory)):
                install_claude_code()
            settings = _read_settings(Path(directory))

        post_matchers = set(_flat_matchers(settings["hooks"]["PostToolUse"]))
        # PostToolUse remains bounded in M4 (no '*' there).
        self.assertNotIn(UNIVERSAL_PRETOOLUSE_MATCHER, post_matchers)
        for required in BOUNDED_TOOL_MATCHERS:
            self.assertIn(required, post_matchers, f"missing {required} in PostToolUse")

    def test_installer_writes_unmatched_lifecycle_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("rulence.installers.Path.home", return_value=Path(directory)):
                install_claude_code()
            settings = _read_settings(Path(directory))

        for event in UNMATCHED_EVENTS:
            entries = settings["hooks"].get(event, [])
            rulence_entries = [
                entry
                for entry in entries
                if RULENCE_HOOK_COMMAND in json.dumps(entry)
            ]
            self.assertTrue(rulence_entries, f"missing rulence hook for {event}")
            for entry in rulence_entries:
                # Lifecycle events must not have a matcher attached.
                self.assertNotIn("matcher", entry)

    def test_installer_preserves_unrelated_user_hooks_per_event(self) -> None:
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
                            ],
                            "Notification": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "/usr/local/bin/user-notify",
                                        }
                                    ]
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("rulence.installers.Path.home", return_value=home):
                install_claude_code()
            settings = _read_settings(home)

        # Original user-pre hook is preserved verbatim.
        pre_entries = settings["hooks"]["PreToolUse"]
        user_pre = [e for e in pre_entries if "user-pre" in json.dumps(e)]
        self.assertEqual(len(user_pre), 1)
        # And Notification (an event we don't install hooks for) is untouched.
        notify_entries = settings["hooks"]["Notification"]
        self.assertEqual(len(notify_entries), 1)
        self.assertIn("user-notify", json.dumps(notify_entries[0]))

    def test_installer_does_not_duplicate_existing_rulence_hook_per_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("rulence.installers.Path.home", return_value=Path(directory)):
                first = install_claude_code()
                second = install_claude_code()
            settings = _read_settings(Path(directory))

        self.assertEqual(first["status"], "installed")
        self.assertEqual(second["status"], "already_installed")
        # PreToolUse: exactly one Rulence entry with matcher "*".
        pre_rulence = [
            e
            for e in settings["hooks"]["PreToolUse"]
            if RULENCE_HOOK_COMMAND in json.dumps(e)
        ]
        self.assertEqual(len(pre_rulence), 1)
        self.assertEqual(pre_rulence[0].get("matcher"), UNIVERSAL_PRETOOLUSE_MATCHER)
        # PostToolUse: each bounded matcher present exactly once.
        post_matchers = _flat_matchers(settings["hooks"]["PostToolUse"])
        for matcher in BOUNDED_TOOL_MATCHERS:
            self.assertEqual(
                post_matchers.count(matcher), 1, f"PostToolUse:{matcher} duplicated"
            )

    def test_force_replaces_only_existing_rulence_entries(self) -> None:
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
                                            "command": "/user/external/keep-me",
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
                # Now force-reinstall.
                result = install_claude_code(force=True)
            settings = _read_settings(home)

        self.assertIn("events_replaced", result)
        # The unrelated user hook is still there.
        pre_entries = settings["hooks"]["PreToolUse"]
        keep_me = [e for e in pre_entries if "keep-me" in json.dumps(e)]
        self.assertEqual(len(keep_me), 1)
        # And exactly one Rulence PreToolUse entry, with universal matcher.
        rulence_pre = [
            e for e in pre_entries if RULENCE_HOOK_COMMAND in json.dumps(e)
        ]
        self.assertEqual(len(rulence_pre), 1)
        self.assertEqual(rulence_pre[0].get("matcher"), UNIVERSAL_PRETOOLUSE_MATCHER)
        # PostToolUse still has each bounded matcher exactly once.
        post_matchers = _flat_matchers(settings["hooks"]["PostToolUse"])
        for matcher in BOUNDED_TOOL_MATCHERS:
            self.assertEqual(
                post_matchers.count(matcher), 1, f"PostToolUse:{matcher} duplicated"
            )

    def test_legacy_pretooluse_hook_is_recognized_as_rulence(self) -> None:
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
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("rulence.installers.Path.home", return_value=home):
                install_claude_code()
            settings = _read_settings(home)

        # Without --force, an existing legacy Rulence PreToolUse entry is
        # preserved verbatim; no second Rulence entry is added on top.
        pre_entries = settings["hooks"]["PreToolUse"]
        bash_edit_write = [e for e in pre_entries if e.get("matcher") == "Bash|Edit|Write"]
        self.assertEqual(len(bash_edit_write), 1)
        self.assertIn(LEGACY_PRETOOLUSE_COMMAND, json.dumps(bash_edit_write[0]))
        # And no universal Rulence entry was created (force migration is
        # required to upgrade to the M4 universal matcher).
        universal = [
            e
            for e in pre_entries
            if e.get("matcher") == UNIVERSAL_PRETOOLUSE_MATCHER
            and RULENCE_HOOK_COMMAND in json.dumps(e)
        ]
        self.assertEqual(universal, [])

    def test_backup_is_written_when_settings_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings_path = home / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text('{"existing": "data"}', encoding="utf-8")
            with patch("rulence.installers.Path.home", return_value=home):
                result = install_claude_code()
            self.assertIsNotNone(result["backup"])
            self.assertTrue(Path(result["backup"]).exists())


if __name__ == "__main__":
    unittest.main()
