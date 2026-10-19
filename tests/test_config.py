import os
import unittest

from magicmouse.config import (
    ConfigError,
    default_config,
    load_config,
    resolve_actions,
    validate,
)
from magicmouse.config import _deep_merge


class ConfigTest(unittest.TestCase):
    def test_defaults_load_without_user_file(self):
        cfg = load_config(path="/nonexistent/magic-mouse.json")
        self.assertTrue(cfg["scroll"]["natural"])
        self.assertEqual(cfg["gestures"]["fingers"], 2)
        self.assertEqual(cfg["gestures"]["map"]["swipe_left"], "workspace_next")

    def test_user_override_merges(self):
        cfg = default_config()
        _deep_merge(cfg, {"scroll": {"natural": False}, "gestures": {"fingers": 3}})
        validate(cfg)
        self.assertFalse(cfg["scroll"]["natural"])
        self.assertEqual(cfg["gestures"]["fingers"], 3)
        # untouched defaults survive
        self.assertEqual(cfg["scroll"]["inertia"]["tau"], 0.35)

    def test_unknown_setting_is_rejected(self):
        cfg = default_config()
        with self.assertRaises(ConfigError) as caught:
            _deep_merge(cfg, {"scroll": {"natral": False}})
        self.assertIn("scroll.natral", str(caught.exception))

    def test_invalid_values_are_rejected(self):
        for patch in (
            {"gestures": {"fingers": 1}},
            {"gestures": {"map": {"swipe_left": "open_pod_bay_doors"}}},
            {"gestures": {"map": {"sideways": "none"}}},
            {"scroll": {"inertia": {"tau": 0}}},
            {"commands": {"app_grid": []}},
            {"commands": {"app-grid": ["true"]}},  # typo: silently dead before
            {"devices": {"manage": ["apple_trackpad"]}},
            {"scroll": {"touch": {"detents_per_mm": True}}},  # bool is an int
            {"gestures": {"map": {"tap": {"command": ["bad\0cmd"]}}}},
        ):
            with self.assertRaises(ConfigError, msg=patch):
                cfg = default_config()
                _deep_merge(cfg, patch)
                validate(cfg)

    def test_nonstandard_json_numbers_are_rejected(self):
        import tempfile

        for literal in ("NaN", "Infinity", "1e400"):
            with self.subTest(literal=literal):
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
                    handle.write('{"scroll": {"inertia": {"tau": %s}}}' % literal)
                try:
                    with self.assertRaises(ConfigError):
                        load_config(path=handle.name)
                finally:
                    os.unlink(handle.name)

    def test_custom_command_action(self):
        cfg = default_config()
        _deep_merge(
            cfg,
            {"gestures": {"map": {"double_tap": {"command": ["notify-send", "zap"]}}}},
        )
        validate(cfg)
        actions = resolve_actions(cfg)
        self.assertEqual(
            actions["double_tap"],
            {"name": "custom", "command": ["notify-send", "zap"]},
        )

    def test_resolve_actions(self):
        actions = resolve_actions(default_config())
        self.assertEqual(
            actions["swipe_left"],
            {"name": "workspace_next", "command": ["hyprctl", "dispatch", "workspace", "e+1"]},
        )
        self.assertEqual(actions["tap"], {"name": "none", "command": None})


if __name__ == "__main__":
    unittest.main()
