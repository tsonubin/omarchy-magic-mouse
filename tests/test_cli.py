"""CLI contract: an explicit --config path must exist (no silent defaults)."""

import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigPathTest(unittest.TestCase):
    def test_missing_explicit_config_is_an_error(self):
        home = tempfile.mkdtemp(prefix="mm-cli-test-")
        missing = os.path.join(home, "no-such-config.json")
        commands = (
            ("run",),
            ("simulate",),
            ("config", "--show"),  # --show reads; bare config creates it
        )
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, "-m", "magicmouse.cli", *command, "--config", missing],
                    capture_output=True,
                    text=True,
                    env={**os.environ, "HOME": home, "PYTHONPATH": REPO},
                    cwd=REPO,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("config file not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
