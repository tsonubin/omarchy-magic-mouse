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

    def test_invalid_json_is_rejected(self):
        import tempfile

        contents = (
            '{"scroll": {"inertia": {"tau": NaN}}}',
            '{"scroll": {"inertia": {"tau": Infinity}}}',
            '{"scroll": {"inertia": {"tau": 1e400}}}',
            '{"scroll": {"natural": true, "natural": false}}',
        )
        for content in contents:
            with self.subTest(content=content):
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
                    handle.write(content)
                try:
                    with self.assertRaises(ConfigError):
                        load_config(path=handle.name)
                finally:
                    os.unlink(handle.name)

    def test_config_file_compatibility(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8-sig") as handle:
            handle.write('{"scroll": {"natural": false}}')
        try:
            cfg = load_config(path=handle.name)  # editor-produced BOM must load
            self.assertFalse(cfg["scroll"]["natural"])
        finally:
            os.unlink(handle.name)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigError):
                load_config(path=directory)  # a directory is a clean error

    def test_all_problems_are_reported(self):
        cfg = default_config()
        _deep_merge(cfg, {"gestures": {"fingers": 5}, "scroll": {"inertia": {"tau": -1}}})
        with self.assertRaises(ConfigError) as caught:
            validate(cfg)
        self.assertEqual(len(caught.exception.problems), 2, "one round-trip must fix everything at once")
        self.assertIn("gestures.fingers", str(caught.exception))
        self.assertIn("scroll.inertia.tau", str(caught.exception))

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
