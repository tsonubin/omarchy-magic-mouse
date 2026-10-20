"""Install/uninstall contracts in a throwaway HOME: idempotent, non-destructive.

These scripts own the user's machine state; the contracts are that re-running
is safe, dry-run changes nothing, and no user data is ever deleted.
"""

import glob
import os
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class InstallScriptTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mm-install-test-")
        self.env = {**os.environ, "HOME": self.home}

    def script(self, name, *args):
        return subprocess.run(
            ["bash", os.path.join(REPO, name), "--no-system", *args],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=REPO,
            timeout=60,
        )

    def test_install_is_idempotent_and_dry_run_changes_nothing(self):
        dry = self.script("install.sh", "--dry-run", "--no-enable")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("dry-run:", dry.stdout)
        self.assertFalse(
            os.path.exists(os.path.join(self.home, ".local/share/omarchy-magic-mouse"))
        )

        first = self.script("install.sh", "--no-enable")
        self.assertEqual(first.returncode, 0, first.stderr)
        config = os.path.join(self.home, ".config/omarchy/magic-mouse.json")
        self.assertTrue(os.path.exists(config))
        self.assertTrue(
            os.path.islink(os.path.join(self.home, ".local/bin/omarchy-magic-mouse"))
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(self.home, ".config/systemd/user/omarchy-magic-mouse.service")
            )
        )

        with open(config, "w", encoding="utf-8") as handle:  # a user edit...
            handle.write('{"scroll": {"natural": false}}\n')
        second = self.script("install.sh", "--no-enable")
        self.assertEqual(second.returncode, 0, second.stderr)
        with open(config, encoding="utf-8") as handle:
            self.assertIn(
                "natural", handle.read(), "...must survive re-install (config --init)"
            )

    def test_uninstall_keeps_config_and_purge_moves_it_aside(self):
        self.assertEqual(self.script("install.sh", "--no-enable").returncode, 0)
        config = os.path.join(self.home, ".config/omarchy/magic-mouse.json")
        with open(config, "w", encoding="utf-8") as handle:
            handle.write("USER DATA\n")

        out = self.script("uninstall.sh")
        self.assertEqual(out.returncode, 0, out.stderr)
        with open(config, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "USER DATA\n", "plain uninstall keeps the config")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local/bin/omarchy-magic-mouse")))

        out = self.script("uninstall.sh", "--purge")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(os.path.exists(config))
        backups = glob.glob(config + ".bak-*")
        self.assertEqual(len(backups), 1, "purge must move the config aside, not delete it")
        with open(backups[0], encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "USER DATA\n")

        with open(config, "w", encoding="utf-8") as handle:
            handle.write("NEWER\n")
        self.assertEqual(self.script("uninstall.sh", "--purge").returncode, 0)
        self.assertEqual(
            len(glob.glob(config + ".bak-*")), 2, "a second purge must not overwrite the first"
        )


if __name__ == "__main__":
    unittest.main()
