"""Contract for `omarchy-magic-mouse setup`: preview is inert, apply is safe."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_setup(home, *args):
    return subprocess.run(
        [sys.executable, "-m", "magicmouse.cli", "setup", *args],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": home,
            "XDG_CONFIG_HOME": os.path.join(home, ".config"),
            "XDG_RUNTIME_DIR": os.path.join(home, "run"),
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + os.path.join(home, "bus"),
            "PYTHONPATH": REPO,
        },
        cwd=REPO,
        timeout=60,
    )


class SetupTest(unittest.TestCase):
    def test_dry_run_changes_nothing_and_apply_only_writes_config(self):
        home = tempfile.mkdtemp(prefix="mm-setup-test-")
        config = os.path.join(home, ".config/omarchy/magic-mouse.json")

        dry = run_setup(home, "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("would write the default config", dry.stdout)
        self.assertFalse(os.path.exists(config), "dry-run must not create the config")

        applied = run_setup(home)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertTrue(os.path.exists(config))
        with open(config, encoding="utf-8") as handle:
            json.load(handle)  # valid JSON

        again = run_setup(home)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("never overwritten", again.stdout, "existing config must not be touched")

    def test_custom_config_does_not_enable_the_default_service(self):
        home = tempfile.mkdtemp(prefix="mm-setup-test-")
        unit_dir = os.path.join(home, ".config/systemd/user")
        os.makedirs(unit_dir)
        open(os.path.join(unit_dir, "omarchy-magic-mouse.service"), "w").close()
        out = run_setup(home, "--dry-run", "--config", os.path.join(home, "custom.json"))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("not enabled", out.stdout)
        self.assertNotIn("systemctl --user enable", out.stdout)


if __name__ == "__main__":
    unittest.main()
